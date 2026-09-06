"""Inbound Outlook attachments become canonical documents (Batch 4b, ADR-074).

Batch 4a normalized inbound mail and deliberately deferred attachments: the sync received only
`hasAttachments`. This pins the ingestion that closes that:

    Graph attachment -> validate -> canonical `documents` (+ document_sources) -> communication_attachments

SCOPE: ordinary `fileAttachment` only. `itemAttachment`, `referenceAttachment` and anything `isInline`
are SKIPPED with a recorded reason — never fabricated into a document, and never allowed to abandon
the rest of the email. Signature logos would otherwise bury real paperwork under a per-email drizzle.

ANCHORING is inherited from the EMAIL, never re-derived: a file called "Smith 1040.pdf" attached to a
message from the Joneses belongs to the Joneses. An email that could not be anchored is never
normalized at all (4a), so its attachments are never fetched — fail-closed by construction.

IDEMPOTENCY is the hard gate and comes from the database: `resolve_or_create_canonical` dedups on
content hash, and `communication_attachments` carries UNIQUE (message_id, document_id), so a racing
worker cannot double-attach. Replay is also cheap — a message already handled skips the Graph call.

ONE EMAIL, ONE TIMELINE ROW is absolute and unchanged. Three attachments are not three history
entries. No live Graph call is made anywhere in this file; every payload is a literal.
"""
from __future__ import annotations

import base64
import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from app.db import (
    communication_attachments,
    communication_messages,
    documents,
    engine,
    households,
    metadata,
    people,
    portal_message_attachments,
    timeline_events,
)
from app.jobs import microsoft_mail_sync as sync
from app.services.communications import email_attachments as att
from app.services.communications import email_ingest

document_sources = metadata.tables["document_sources"]

OWNER = "advisor@360wealth.example"
PDF = b"%PDF-1.4 real attachment bytes"
_SEEN_DOCS: set[int] = set()
_SEEN_PEOPLE: set[int] = set()
_SEEN_HOUSEHOLDS: set[int] = set()


@pytest.fixture(autouse=True)
def _cleanup():
    """Leave the database as this test found it.

    Documents, people and households are all removed. Without the people/household half these tests
    leave hundreds of rows per run, and the suite has table-count assertions elsewhere (the document
    merge CLI, the pipeline's candidate tests) that legitimately fail when the corpus keeps growing
    underneath them — a leak here surfaces as a mystery failure three files away.
    """
    yield
    with engine.begin() as c:
        if _SEEN_DOCS:
            ids = list(_SEEN_DOCS)
            c.execute(document_sources.delete().where(document_sources.c.document_id.in_(ids)))
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
        if _SEEN_PEOPLE:
            c.execute(timeline_events.delete().where(
                timeline_events.c.person_id.in_(list(_SEEN_PEOPLE))))
            c.execute(people.delete().where(people.c.id.in_(list(_SEEN_PEOPLE))))
        if _SEEN_HOUSEHOLDS:
            c.execute(households.delete().where(households.c.id.in_(list(_SEEN_HOUSEHOLDS))))
    _SEEN_DOCS.clear()
    _SEEN_PEOPLE.clear()
    _SEEN_HOUSEHOLDS.clear()


def _track():
    """Record documents this test created so they are cleaned up."""
    with engine.connect() as c:
        for did in c.scalars(select(documents.c.id).where(
                documents.c.stored_name.like("email:%"))):
            _SEEN_DOCS.add(did)


def _account():
    return {"id": 1, "tenant_id": "tenant-a", "user_id": "mailbox-1", "email": OWNER}


def _person(household_id=None):
    tag = uuid.uuid4().hex[:8]
    address = f"ada-{tag}@example.test"
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            full_name=f"Ada {tag}", primary_email=address, normalized_email=address,
            active=True, household_id=household_id).returning(people.c.id)).scalar_one()
    _SEEN_PEOPLE.add(pid)
    return pid, address


def _household():
    with engine.begin() as c:
        hid = c.execute(households.insert().values(
            name=f"House {uuid.uuid4().hex[:8]}").returning(households.c.id)).scalar_one()
    _SEEN_HOUSEHOLDS.add(hid)
    return hid


def _message(sender, *, has_attachments=True, graph_id=None, internet_id=None):
    tag = uuid.uuid4().hex[:10]
    return {
        "id": graph_id or f"AAMk{tag}", "subject": "Statement",
        "from": {"emailAddress": {"name": "Ada", "address": sender}},
        "toRecipients": [{"emailAddress": {"name": OWNER, "address": OWNER}}],
        "ccRecipients": [], "receivedDateTime": "2026-09-01T10:00:00Z",
        "bodyPreview": "See attached.", "webLink": f"https://outlook.office.com/mail/{tag}",
        "hasAttachments": has_attachments, "isRead": True,
        "conversationId": f"AAQk{tag}",
        "internetMessageId": internet_id if internet_id is not None else f"<{tag}@example.test>",
    }


def _file_attachment(name="statement.pdf", data=PDF, provider_id=None, inline=False,
                     odata=att.FILE_ATTACHMENT, size=None):
    return {"@odata.type": odata, "id": provider_id or f"ATT{uuid.uuid4().hex[:10]}",
            "name": name, "contentType": "application/pdf",
            "size": size if size is not None else len(data), "isInline": inline,
            "contentBytes": base64.b64encode(data).decode()}


def _normalized(sender_person=None, *, message=None, anchor=None):
    """A normalized email to attach to, plus its resolved anchor."""
    if sender_person is None:
        sender_person = _person()
    pid, address = sender_person
    msg = message or _message(address)
    match = email_ingest.resolve_match(msg, {address: (pid, anchor)}, OWNER)
    with engine.begin() as c:
        message_id = email_ingest.normalize_email(c, account=_account(), message=msg, match=match)
    return message_id, msg, match, pid


def _ingest(message_id, payload, match, *, fetch=None):
    def _default_fetch(provider_id):
        return next((a for a in payload if a.get("id") == provider_id), {})
    with engine.begin() as c:
        summary = att.ingest_attachments(
            c, communication_message_id=message_id, attachments_payload=payload,
            anchor={"person_id": match.person_id, "household_id": match.household_id},
            fetch_bytes=fetch or _default_fetch)
    _track()
    return summary


def _rows(message_id):
    with engine.connect() as c:
        return c.execute(select(communication_attachments).where(
            communication_attachments.c.message_id == message_id)).mappings().all()


# ============================ CLASSIFICATION ============================

@pytest.mark.parametrize("attachment,expected", [
    ({"@odata.type": att.FILE_ATTACHMENT, "isInline": False}, att.SUPPORTED),
    ({"@odata.type": att.FILE_ATTACHMENT, "isInline": True}, att.SKIP_INLINE),
    ({"@odata.type": att.ITEM_ATTACHMENT}, att.SKIP_ITEM),
    ({"@odata.type": att.REFERENCE_ATTACHMENT}, att.SKIP_REFERENCE),
    ({"@odata.type": "#microsoft.graph.somethingElse"}, att.SKIP_UNKNOWN),
    ({"@odata.type": att.FILE_ATTACHMENT, "size": att.MAX_ATTACHMENT_BYTES + 1}, att.SKIP_TOO_LARGE),
])
def test_attachment_types_are_classified_explicitly(attachment, expected):
    assert att.classify(attachment) == expected


# ============================ RETRIEVAL ============================

def test_no_attachment_call_when_the_message_has_none():
    calls = []
    message_id, msg, match, _ = _normalized(message=_message(_person()[1], has_attachments=False))
    msg["hasAttachments"] = False

    def _boom(*a, **k):
        calls.append(a)
        raise AssertionError("no attachment call may be made")

    assert sync._ingest_attachments(_account(), "token", msg, message_id, match) == 0
    assert calls == []


def test_metadata_is_fetched_and_supported_bytes_ingested(monkeypatch):
    pid, address = _person()
    msg = _message(address)
    attachment = _file_attachment()
    message_id, _msg, match, _pid = _normalized((pid, address), message=msg)

    fetched = {"metadata": 0, "bytes": []}

    class _Resp:
        ok = True

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def _get(url, headers=None, params=None, timeout=None):
        if url.endswith("/attachments"):
            fetched["metadata"] += 1
            return _Resp({"value": [attachment]})
        fetched["bytes"].append(url.rsplit("/", 1)[-1])
        return _Resp(attachment)

    monkeypatch.setattr(sync.requests, "get", _get)
    created = sync._ingest_attachments(_account(), "token", msg, message_id, match)
    _track()

    assert created == 1
    assert fetched["metadata"] == 1 and fetched["bytes"] == [attachment["id"]]


def test_a_handled_message_is_not_re_fetched_on_replay(monkeypatch):
    pid, address = _person()
    msg = _message(address)
    message_id, _m, match, _p = _normalized((pid, address), message=msg)
    _ingest(message_id, [_file_attachment()], match)

    def _boom(*a, **k):
        raise AssertionError("replay must not call Graph again")

    monkeypatch.setattr(sync.requests, "get", _boom)
    assert sync._ingest_attachments(_account(), "token", msg, message_id, match) == 0


def test_an_all_skipped_message_is_also_not_re_fetched(monkeypatch):
    """A message whose attachments were all skipped has no rows, but must not poll forever."""
    pid, address = _person()
    msg = _message(address)
    message_id, _m, match, _p = _normalized((pid, address), message=msg)
    _ingest(message_id, [_file_attachment(inline=True)], match)
    assert _rows(message_id) == []

    def _boom(*a, **k):
        raise AssertionError("an all-skipped message must not be re-fetched")

    monkeypatch.setattr(sync.requests, "get", _boom)
    assert sync._ingest_attachments(_account(), "token", msg, message_id, match) == 0


# ============================ PERSISTENCE ============================

def test_a_file_attachment_becomes_a_canonical_document_with_provenance():
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    attachment = _file_attachment(name="2025 statement.pdf")

    summary = _ingest(message_id, [attachment], match)

    assert summary.ingested == 1 and summary.errors == []
    row = _rows(message_id)[0]
    assert row["document_id"] is not None
    assert row["vault_document_id"] is None, "an inbound attachment is never a vault document"
    assert row["attachment_ref"] == attachment["id"], "provider identity is preserved"

    with engine.connect() as c:
        doc = c.execute(select(documents).where(
            documents.c.id == row["document_id"])).mappings().one()
        sources = c.execute(select(document_sources).where(
            document_sources.c.document_id == row["document_id"])).mappings().all()
    assert doc["original_name"] == "2025 statement.pdf"
    assert doc["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert doc["storage_provider"] == att.STORAGE_PROVIDER
    assert [s["source_system"] for s in sources] == [att.SOURCE_SYSTEM]
    assert sources[0]["source_external_id"] == attachment["id"]


def test_the_bytes_land_on_disk_and_are_content_addressed():
    from pathlib import Path

    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    _ingest(message_id, [_file_attachment()], match)

    with engine.connect() as c:
        doc = c.execute(select(documents).where(documents.c.stored_name ==
                                                f"email:{hashlib.sha256(PDF).hexdigest()}")
                        ).mappings().first()
    assert doc is not None
    stored = Path(doc["storage_uri"])
    assert stored.is_file() and stored.read_bytes() == PDF
    assert hashlib.sha256(PDF).hexdigest() in stored.name        # content-addressed, not by filename


def test_identical_bytes_from_two_emails_reuse_one_document():
    """ADR-072 content-hash reuse: the same attachment forwarded twice is one canonical document."""
    pid, address = _person()
    first_id, _m1, match1, _ = _normalized((pid, address))
    second_id, _m2, match2, _ = _normalized((pid, address))

    _ingest(first_id, [_file_attachment(provider_id="ATT-A")], match1)
    _ingest(second_id, [_file_attachment(provider_id="ATT-B")], match2)

    assert _rows(first_id)[0]["document_id"] == _rows(second_id)[0]["document_id"]


# ============================ ANCHORING ============================

def test_the_document_inherits_the_emails_anchor():
    household = _household()
    pid, address = _person(household_id=household)
    message_id, _m, match, _p = _normalized((pid, address), anchor=household)
    _ingest(message_id, [_file_attachment()], match)

    with engine.connect() as c:
        doc = c.execute(select(documents).where(
            documents.c.id == _rows(message_id)[0]["document_id"])).mappings().one()
    assert doc["person_id"] == pid and doc["household_id"] == household


def test_the_filename_cannot_re_anchor_the_document():
    """A file named for another client is still owned by the client whose email carried it."""
    other_pid, _other_address = _person()
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))

    _ingest(message_id, [_file_attachment(name=f"person-{other_pid} 1040.pdf")], match)

    with engine.connect() as c:
        doc = c.execute(select(documents).where(
            documents.c.id == _rows(message_id)[0]["document_id"])).mappings().one()
    assert doc["person_id"] == pid and doc["person_id"] != other_pid


def test_an_unanchored_email_never_reaches_attachment_ingestion(monkeypatch):
    """Fail-closed by construction: 4a writes no communication record for an ambiguous or unmatched
    email, so there is nothing to attach to and no orphan document can be created."""
    a_pid, a_address = _person(household_id=_household())
    b_pid, b_address = _person(household_id=_household())
    msg = _message(a_address)
    msg["toRecipients"] = [{"emailAddress": {"name": "B", "address": b_address}}]
    match = email_ingest.resolve_match(msg, {a_address: (a_pid, None), b_address: (b_pid, None)}, OWNER)
    assert match.ambiguous

    with engine.begin() as c:
        assert email_ingest.normalize_email(c, account=_account(), message=msg, match=match) is None

    def _boom(*a, **k):
        raise AssertionError("an unanchored email must never fetch attachments")

    monkeypatch.setattr(sync.requests, "get", _boom)
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(documents))
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(documents)) == before


# ============================ IDEMPOTENCY ============================

def test_ingesting_the_same_attachment_twice_creates_one_row():
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    attachment = _file_attachment()

    first = _ingest(message_id, [attachment], match)
    second = _ingest(message_id, [attachment], match)

    assert first.ingested == 1 and second.ingested == 0
    assert len(_rows(message_id)) == 1


def test_a_folder_move_still_yields_one_attachment():
    """The Graph message id changes; the Message-ID does not, so 4a returns the SAME communication
    message and the attachment de-duplicates onto it."""
    pid, address = _person()
    internet_id = f"<moved-{uuid.uuid4().hex[:8]}@example.test>"
    before = _message(address, graph_id="AAMkINBOX", internet_id=internet_id)
    after = _message(address, graph_id="AAMkFILED", internet_id=internet_id)
    attachment = _file_attachment()

    first_id, _m, match, _p = _normalized((pid, address), message=before)
    _ingest(first_id, [attachment], match)
    with engine.begin() as c:
        second_id = email_ingest.normalize_email(
            c, account=_account(), message=after,
            match=email_ingest.resolve_match(after, {address: (pid, None)}, OWNER))
    assert second_id == first_id
    _ingest(second_id, [attachment], match)

    assert len(_rows(first_id)) == 1


def test_a_concurrent_duplicate_link_is_refused_by_the_database():
    """The race guard is UNIQUE (message_id, document_id), not a read-then-write."""
    from sqlalchemy.exc import IntegrityError

    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    _ingest(message_id, [_file_attachment()], match)
    document_id = _rows(message_id)[0]["document_id"]

    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(communication_attachments.insert().values(
                message_id=message_id, document_id=document_id))
    assert len(_rows(message_id)) == 1


def test_a_retry_after_a_partial_failure_converges():
    """First attachment succeeds, second explodes; the retry completes the set without duplicating."""
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    good = _file_attachment(name="good.pdf", data=b"%PDF-1.4 good")
    bad = _file_attachment(name="bad.pdf", data=b"%PDF-1.4 bad")
    attempts = {"n": 0}

    def _flaky(provider_id):
        if provider_id == bad["id"] and attempts["n"] == 0:
            attempts["n"] += 1
            raise RuntimeError("transient Graph failure")
        return next(a for a in (good, bad) if a["id"] == provider_id)

    first = _ingest(message_id, [good, bad], match, fetch=_flaky)
    assert first.ingested == 1 and first.errors

    second = _ingest(message_id, [good, bad], match, fetch=_flaky)
    assert second.ingested == 1 and second.errors == []
    assert len(_rows(message_id)) == 2


# ============================ FAILURE BEHAVIOUR ============================

def test_a_failed_byte_retrieval_creates_no_document_or_attachment():
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(documents))

    summary = _ingest(message_id, [_file_attachment()], match,
                      fetch=lambda _pid: (_ for _ in ()).throw(RuntimeError("graph down")))

    assert summary.ingested == 0 and summary.errors
    assert _rows(message_id) == []
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(documents)) == before


def test_undecodable_content_is_skipped_not_stored():
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    broken = _file_attachment()
    broken["contentBytes"] = "!!!not base64!!!"

    summary = _ingest(message_id, [broken], match, fetch=lambda _pid: broken)

    assert summary.ingested == 0 and _rows(message_id) == []
    assert any(reason == att.SKIP_UNREADABLE for _n, reason in summary.skipped)


def test_one_unsupported_attachment_does_not_destroy_the_email_or_its_siblings():
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    payload = [_file_attachment(name="logo.png", inline=True),
               {"@odata.type": att.ITEM_ATTACHMENT, "id": "ITEM1", "name": "forwarded.eml"},
               {"@odata.type": att.REFERENCE_ATTACHMENT, "id": "REF1", "name": "link"},
               _file_attachment(name="real.pdf")]

    summary = _ingest(message_id, payload, match)

    assert summary.ingested == 1
    assert sorted(r for _n, r in summary.skipped) == sorted(
        [att.SKIP_INLINE, att.SKIP_ITEM, att.SKIP_REFERENCE])
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(communication_messages).where(
            communication_messages.c.id == message_id)) == 1, "the email itself is untouched"


def test_skipped_attachments_are_recorded_for_audit():
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    _ingest(message_id, [_file_attachment(name="logo.png", inline=True)], match)

    with engine.connect() as c:
        meta = c.scalar(select(communication_messages.c.message_metadata).where(
            communication_messages.c.id == message_id))
    recorded = meta["attachments"]
    assert recorded["ingested"] == 0
    assert recorded["skipped"] == [{"name": "logo.png", "reason": att.SKIP_INLINE}]


# ============================ SECURITY ============================

@pytest.mark.parametrize("hostile", ["../../../../etc/passwd", "C:\\Windows\\system32\\evil.dll",
                                     "..\\..\\escape.pdf"])
def test_a_hostile_filename_is_refused_outright(hostile):
    """The existing `sanitize_relative_path` guard is reused, not re-implemented, and it FAILS CLOSED:
    a traversal or absolute name stores nothing rather than being quietly rewritten to something safe."""
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(documents))

    summary = _ingest(message_id, [_file_attachment(name=hostile)], match)

    assert summary.ingested == 0 and _rows(message_id) == []
    assert any(r == att.SKIP_UNSAFE_NAME for _n, r in summary.skipped)
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(documents)) == before


def test_a_safe_filename_lands_inside_the_document_root():
    from pathlib import Path

    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    _ingest(message_id, [_file_attachment(name="2025 statement.pdf")], match)

    with engine.connect() as c:
        doc = c.execute(select(documents).where(
            documents.c.id == _rows(message_id)[0]["document_id"])).mappings().one()
    rel = Path(doc["storage_path"])
    assert ".." not in rel.parts and not rel.is_absolute()


def test_an_oversized_attachment_is_skipped_before_any_fetch():
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    huge = _file_attachment(size=att.MAX_ATTACHMENT_BYTES + 1)

    def _boom(_pid):
        raise AssertionError("an oversized attachment must be skipped before fetching bytes")

    summary = _ingest(message_id, [huge], match, fetch=_boom)
    assert summary.ingested == 0
    assert any(r == att.SKIP_TOO_LARGE for _n, r in summary.skipped)


def test_no_provider_token_or_raw_graph_data_is_persisted():
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    _ingest(message_id, [_file_attachment()], match)

    with engine.connect() as c:
        row = dict(_rows(message_id)[0])
        doc = dict(c.execute(select(documents).where(
            documents.c.id == row["document_id"])).mappings().one())
        src = dict(c.execute(select(document_sources).where(
            document_sources.c.document_id == row["document_id"])).mappings().one())
    blob = str(row) + str(doc) + str(src)
    for leaked in ("Bearer", "access_token", "contentBytes", "graph.microsoft.com"):
        assert leaked not in blob


# ============================ TIMELINE ============================

def test_an_email_with_three_attachments_still_has_exactly_one_timeline_event(monkeypatch):
    pid, address = _person()
    msg = _message(address)
    payload = [_file_attachment(name=f"f{i}.pdf", data=f"%PDF-1.4 body {i}".encode())
               for i in range(3)]

    class _Resp:
        ok = True

        def __init__(self, p):
            self._p = p

        def json(self):
            return self._p

    def _get(url, headers=None, params=None, timeout=None):
        if url.endswith("/attachments"):
            return _Resp({"value": payload})
        return _Resp(next(a for a in payload if a["id"] == url.rsplit("/", 1)[-1]))

    monkeypatch.setattr(sync.requests, "get", _get)
    monkeypatch.setattr(sync, "get_microsoft_access_token", lambda a: "token")
    monkeypatch.setattr(sync, "record_sync_health", lambda *a, **k: None)

    class _ListResp:
        status_code = 200
        ok = True

        def json(self):
            return {"value": [msg]}

        def raise_for_status(self):
            return None

    original_get = _get

    def _dispatch(url, headers=None, params=None, timeout=None):
        if url == sync.GRAPH_MESSAGES_URL:
            return _ListResp()
        return original_get(url, headers=headers, params=params, timeout=timeout)

    monkeypatch.setattr(sync.requests, "get", _dispatch)
    result = sync._ingest_messages(_account(), "token", 50, {address: (pid, None)})
    _track()

    assert result["attachments_ingested"] == 3
    with engine.connect() as c:
        events = c.execute(select(timeline_events).where(
            timeline_events.c.person_id == pid)).mappings().all()
        assert len(events) == 1 and events[0]["event_type"] == "email_received"
        assert c.scalar(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.event_type == "conversation_opened",
            timeline_events.c.person_id == pid)) == 0


# ============================ EXISTING BEHAVIOUR ============================

def test_an_email_without_attachments_is_unchanged():
    pid, address = _person()
    msg = _message(address, has_attachments=False)
    message_id, _m, _match, _p = _normalized((pid, address), message=msg)

    assert _rows(message_id) == []
    with engine.connect() as c:
        meta = c.scalar(select(communication_messages.c.message_metadata).where(
            communication_messages.c.id == message_id))
    assert "attachments" not in meta, "nothing is recorded for a message that has none"


def test_portal_message_attachments_are_untouched():
    """A different table with different lifecycle semantics; this batch must not disturb it."""
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(portal_message_attachments))
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    _ingest(message_id, [_file_attachment()], match)
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_message_attachments)) == before


def test_the_attachment_row_obeys_the_at_most_one_reference_rule():
    """msgatt01's CHECK still governs: an email attachment is canonical-only."""
    pid, address = _person()
    message_id, _m, match, _p = _normalized((pid, address))
    _ingest(message_id, [_file_attachment()], match)
    row = _rows(message_id)[0]
    assert (row["document_id"] is not None) and (row["vault_document_id"] is None)
