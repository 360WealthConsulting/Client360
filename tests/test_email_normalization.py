"""Inbound Outlook email becomes a canonical communication record (Batch 4a, ADR-074).

Mail was already ingested into `timeline_events` and, for an unrecognised sender,
`microsoft_unmatched_messages`. Neither is a communication record, and the two identifiers that make
an email addressable — `internetMessageId` and `conversationId` — were read by the preview route and
discarded by the sync. This pins the normalization that closes that, and the two defects it repairs.

THE RULE EVERYTHING ELSE SERVES: **one email, one timeline row.** The D.44 registry classifies a
timeline row by `(source, event_type)` and falls back to `event_type` alone, so a
`conversation_opened` event for an email that already has `email_received` would make it appear
TWICE in every engagement timeline. `test_normalizing_writes_no_second_timeline_event` is the guard.

Identity is the RFC 5322 `internetMessageId`, never the Graph `id` — which is mailbox-scoped and
CHANGES when a message moves between folders, the cause of the pre-existing duplicate-timeline
defect. `/me/messages` is not folder-scoped either, so Sent Items arrived in the same pass and
accumulated in the review queue as unrecognised inbound senders; both are asserted fixed below.

No live Graph call is made anywhere in this file: the HTTP layer is stubbed and every payload is a
literal shaped like a real Graph message.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db import (
    communication_conversations,
    communication_events,
    communication_message_sources,
    communication_messages,
    communication_recipients,
    engine,
    households,
    microsoft_unmatched_messages,
    people,
    timeline_events,
)
from app.jobs import microsoft_mail_sync as sync
from app.services.communications import email_ingest

OWNER = "advisor@360wealth.example"
TENANT = "tenant-a"
MAILBOX = "mailbox-1"


def _account(user_id=MAILBOX, tenant=TENANT, email=OWNER):
    return {"id": 1, "tenant_id": tenant, "user_id": user_id, "email": email}


def _person(email=None, household_id=None, name="Ada Client"):
    tag = uuid.uuid4().hex[:8]
    address = email or f"ada-{tag}@example.test"
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            full_name=f"{name} {tag}", primary_email=address, normalized_email=address,
            active=True, household_id=household_id).returning(people.c.id)).scalar_one()
    return pid, address


def _household(name="Client Household"):
    with engine.begin() as c:
        return c.execute(households.insert().values(
            name=f"{name} {uuid.uuid4().hex[:8]}").returning(households.c.id)).scalar_one()


def _message(*, sender, to=(), cc=(), graph_id=None, internet_id=None, conversation=None,
             subject="Quarterly statement", preview="Please see the attached statement."):
    tag = uuid.uuid4().hex[:10]
    return {
        "id": graph_id or f"AAMk{tag}",
        "subject": subject,
        "from": {"emailAddress": {"name": "Sender", "address": sender}},
        "toRecipients": [{"emailAddress": {"name": a, "address": a}} for a in to],
        "ccRecipients": [{"emailAddress": {"name": a, "address": a}} for a in cc],
        "receivedDateTime": "2026-09-01T10:00:00Z",
        "bodyPreview": preview,
        "webLink": f"https://outlook.office.com/mail/{tag}",
        "hasAttachments": False,
        "isRead": True,
        "conversationId": conversation if conversation is not None else f"AAQk{tag}",
        "internetMessageId": internet_id if internet_id is not None else f"<{tag}@example.test>",
    }


def _people_map(*entries):
    return {address: (pid, household_id) for pid, address, household_id in entries}


def _normalize(message, match, account=None):
    with engine.begin() as c:
        return email_ingest.normalize_email(
            c, account=account or _account(), message=message, match=match)


def _resolve(message, people_map, owner=OWNER):
    return email_ingest.resolve_match(message, people_map, owner)


def _sources(message_id):
    with engine.connect() as c:
        return c.execute(select(communication_message_sources).where(
            communication_message_sources.c.message_id == message_id)).mappings().all()


# ============================ IDENTITY ============================

def test_identity_is_the_internet_message_id_not_the_graph_id():
    m = _message(sender="a@b.test", internet_id="<abc@example.test>", graph_id="AAMkORIGINAL")
    assert email_ingest.source_external_id(m, _account()) == "<abc@example.test>"


def test_identity_falls_back_to_a_composite_when_there_is_no_message_id():
    m = _message(sender="a@b.test", internet_id="", graph_id="AAMkXYZ")
    assert email_ingest.source_external_id(m, _account()) == f"{TENANT}:{MAILBOX}:AAMkXYZ"


def test_the_fallback_drops_empty_parts_rather_than_writing_none():
    """The review-queue path has no tenant or mailbox; the id must still be usable."""
    m = _message(sender="a@b.test", internet_id="", graph_id="AAMkQ")
    assert email_ingest.source_external_id(m, {}) == "AAMkQ"


# ============================ IDEMPOTENCY ============================

def test_the_same_message_twice_creates_one_message():
    pid, address = _person()
    m = _message(sender=address, to=[OWNER])
    match = _resolve(m, _people_map((pid, address, None)))

    first = _normalize(m, match)
    second = _normalize(m, match)

    assert first is not None and second == first
    assert len(_sources(first)) == 1


def test_a_moved_message_keeps_its_identity():
    """A folder move changes the Graph id and nothing else. This is the pre-existing duplicate
    defect: keyed on the Graph id it would be a second record; keyed on the Message-ID it is not."""
    pid, address = _person()
    internet_id = f"<moved-{uuid.uuid4().hex[:8]}@example.test>"
    before = _message(sender=address, internet_id=internet_id, graph_id="AAMkINBOX")
    after = _message(sender=address, internet_id=internet_id, graph_id="AAMkFILED")
    people_map = _people_map((pid, address, None))

    first = _normalize(before, _resolve(before, people_map))
    second = _normalize(after, _resolve(after, people_map))

    assert second == first, "a moved message must not become a second record"


def test_the_same_message_in_a_second_mailbox_is_one_message_with_two_sightings():
    pid, address = _person()
    m = _message(sender=address)
    match = _resolve(m, _people_map((pid, address, None)))
    message_id = _normalize(m, match, account=_account(user_id="mailbox-1"))

    with engine.begin() as c:
        email_ingest.record_sighting(
            c, account=_account(user_id="mailbox-2"), message=m, message_id=message_id)

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(communication_messages).where(
            communication_messages.c.id == message_id)) == 1
    sources = _sources(message_id)
    assert len(sources) == 1, "same Message-ID in both mailboxes is one identity, one sighting row"
    assert sources[0]["source_metadata"]["mailbox_user_id"] == "mailbox-1"


def test_the_identity_is_unique_in_the_database():
    """The idempotency guarantee is a constraint, not a convention — a racing worker cannot win."""
    from sqlalchemy import text
    with engine.connect() as c:
        assert c.scalar(text("""select count(*) from pg_constraint
            where conname = 'uq_comm_message_source_identity'""")) == 1


# ============================ ONE TIMELINE ROW ============================

def test_normalizing_writes_no_second_timeline_event():
    """The whole point. Normalization must not publish conversation_opened, which the D.44 registry
    would classify as a second interaction for the same email."""
    pid, address = _person()
    m = _message(sender=address)
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(timeline_events))

    _normalize(m, _resolve(m, _people_map((pid, address, None))))

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(timeline_events)) == before
        assert c.scalar(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.event_type == "conversation_opened",
            timeline_events.c.person_id == pid)) == 0


def test_the_domain_records_its_own_audit_event_instead():
    pid, address = _person()
    m = _message(sender=address)
    message_id = _normalize(m, _resolve(m, _people_map((pid, address, None))))
    with engine.connect() as c:
        row = c.execute(select(communication_events).where(
            communication_events.c.message_id == message_id)).mappings().one()
    assert row["event_type"] == "message_ingested"


# ============================ THREADING ============================

def test_two_messages_in_one_provider_conversation_share_a_conversation():
    pid, address = _person()
    conversation = f"AAQk{uuid.uuid4().hex[:10]}"
    people_map = _people_map((pid, address, None))
    a = _message(sender=address, conversation=conversation)
    b = _message(sender=address, conversation=conversation)

    first = _normalize(a, _resolve(a, people_map))
    second = _normalize(b, _resolve(b, people_map))

    with engine.connect() as c:
        conv_a = c.scalar(select(communication_messages.c.conversation_id).where(
            communication_messages.c.id == first))
        conv_b = c.scalar(select(communication_messages.c.conversation_id).where(
            communication_messages.c.id == second))
    assert first != second and conv_a == conv_b


def test_the_same_conversation_id_in_a_different_mailbox_is_a_different_thread():
    """conversationId is mailbox-scoped, so the key is composite. Sharing the value across mailboxes
    must not merge two unrelated threads."""
    pid, address = _person()
    conversation = f"AAQk{uuid.uuid4().hex[:10]}"
    people_map = _people_map((pid, address, None))
    a = _message(sender=address, conversation=conversation)
    b = _message(sender=address, conversation=conversation)

    first = _normalize(a, _resolve(a, people_map), account=_account(user_id="mailbox-1"))
    second = _normalize(b, _resolve(b, people_map), account=_account(user_id="mailbox-2"))

    with engine.connect() as c:
        conv_a = c.scalar(select(communication_messages.c.conversation_id).where(
            communication_messages.c.id == first))
        conv_b = c.scalar(select(communication_messages.c.conversation_id).where(
            communication_messages.c.id == second))
    assert conv_a != conv_b


def test_a_conversation_keeps_its_first_anchor():
    """A later message naming a different client never silently moves the thread's owner."""
    household = _household()
    first_person, first_address = _person(household_id=household)
    other_person, other_address = _person()
    conversation = f"AAQk{uuid.uuid4().hex[:10]}"

    a = _message(sender=first_address, conversation=conversation)
    _normalize(a, _resolve(a, _people_map((first_person, first_address, household))))
    b = _message(sender=other_address, conversation=conversation)
    second = _normalize(b, _resolve(b, _people_map((other_person, other_address, None))))

    with engine.connect() as c:
        conv = c.scalar(select(communication_messages.c.conversation_id).where(
            communication_messages.c.id == second))
        anchor = c.execute(select(communication_conversations.c.person_id,
                                  communication_conversations.c.household_id).where(
            communication_conversations.c.id == conv)).mappings().one()
    assert anchor["person_id"] == first_person and anchor["household_id"] == household


# ============================ MATCHING ============================

def test_a_matched_sender_anchors_the_person_and_their_household():
    household = _household()
    pid, address = _person(household_id=household)
    m = _message(sender=address, to=[OWNER])

    match = _resolve(m, _people_map((pid, address, household)))

    assert match.direction == email_ingest.INBOUND and match.anchored
    assert match.person_id == pid and match.household_id == household


def test_a_matched_recipient_anchors_even_when_the_sender_is_unknown():
    pid, address = _person()
    m = _message(sender="stranger@elsewhere.test", to=[address])

    match = _resolve(m, _people_map((pid, address, None)))

    assert match.anchored and match.person_id == pid


def test_several_members_of_one_household_anchor_the_household():
    household = _household()
    a_id, a_address = _person(household_id=household, name="Ada")
    b_id, b_address = _person(household_id=household, name="Bert")
    m = _message(sender=a_address, to=[b_address, OWNER])

    match = _resolve(m, _people_map((a_id, a_address, household), (b_id, b_address, household)))

    assert match.anchored
    assert match.household_id == household and match.person_id is None


def test_people_in_different_households_are_ambiguous_and_anchor_nothing():
    """The Hub's rule: an owner is never guessed. Ambiguity belongs in the review queue."""
    a_id, a_address = _person(household_id=_household(), name="Ada")
    b_id, b_address = _person(household_id=_household(), name="Bert")
    m = _message(sender=a_address, to=[b_address])

    match = _resolve(m, _people_map((a_id, a_address, None), (b_id, b_address, None)))

    assert match.ambiguous and not match.anchored
    assert _normalize(m, match) is None, "an ambiguous email must write no conversation"


def test_an_unmatched_email_writes_nothing():
    m = _message(sender="nobody@elsewhere.test", to=["someone@else.test"])
    match = _resolve(m, {})
    assert not match.anchored and _normalize(m, match) is None


def test_recipients_are_recorded_with_the_right_type():
    pid, address = _person()
    m = _message(sender=address, to=[OWNER], cc=["third@party.test"])
    message_id = _normalize(m, _resolve(m, _people_map((pid, address, None))))

    with engine.connect() as c:
        rows = c.execute(select(communication_recipients).where(
            communication_recipients.c.message_id == message_id)).mappings().all()
    by_ref = {r["recipient_ref"]: r for r in rows}
    assert by_ref[OWNER]["recipient_type"] == "external" and by_ref[OWNER]["recipient_role"] == "to"
    assert by_ref["third@party.test"]["recipient_role"] == "cc"


# ============================ DIRECTION ============================

def test_a_message_from_the_mailbox_owner_is_outbound():
    """/me/messages is not folder-scoped, so Sent Items arrive in the same pass."""
    pid, address = _person()
    m = _message(sender=OWNER, to=[address])

    match = _resolve(m, _people_map((pid, address, None)))

    assert match.direction == email_ingest.OUTBOUND
    assert match.anchored and match.person_id == pid, "anchored on the recipient, not the sender"


def test_the_sender_type_records_an_external_correspondent():
    pid, address = _person()
    inbound = _message(sender=address)
    message_id = _normalize(inbound, _resolve(inbound, _people_map((pid, address, None))))
    with engine.connect() as c:
        row = c.execute(select(communication_messages).where(
            communication_messages.c.id == message_id)).mappings().one()
    assert row["sender_type"] == "external" and row["sender_ref"] == address
    assert row["direction"] == "inbound" and row["channel"] == "email"


# ============================ CONTENT ============================

def test_no_full_body_is_persisted():
    """Communications carries a REGULATORY retention class; full inbound correspondence is a
    compliance decision. Only the preview the timeline already stores is kept."""
    pid, address = _person()
    m = _message(sender=address, preview="Short preview.")
    m["body"] = {"contentType": "html", "content": "<p>SECRET FULL BODY CONTENT</p>"}

    message_id = _normalize(m, _resolve(m, _people_map((pid, address, None))))

    with engine.connect() as c:
        row = c.execute(select(communication_messages).where(
            communication_messages.c.id == message_id)).mappings().one()
    assert row["body"] == "Short preview."
    assert "SECRET FULL BODY CONTENT" not in str(dict(row))


def test_a_long_preview_is_bounded():
    pid, address = _person()
    m = _message(sender=address, preview="x" * 900)
    message_id = _normalize(m, _resolve(m, _people_map((pid, address, None))))
    with engine.connect() as c:
        body = c.scalar(select(communication_messages.c.body).where(
            communication_messages.c.id == message_id))
    assert len(body) == email_ingest.PREVIEW_LIMIT and body.endswith("...")


def test_no_token_or_credential_reaches_any_row():
    pid, address = _person()
    m = _message(sender=address)
    message_id = _normalize(m, _resolve(m, _people_map((pid, address, None))))
    with engine.connect() as c:
        row = dict(c.execute(select(communication_messages).where(
            communication_messages.c.id == message_id)).mappings().one())
        src = dict(_sources(message_id)[0])
    blob = str(row) + str(src)
    for leaked in ("access_token", "refresh_token", "Bearer", "client_secret"):
        assert leaked not in blob


# ============================ THE SYNC JOB END TO END ============================

class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {"value": self._payload}


@pytest.fixture
def graph(monkeypatch):
    """Stub the HTTP layer. No live Graph call is made by any test in this file."""
    box = {"messages": [], "params": None}

    def _get(url, headers=None, params=None, timeout=None):
        box["params"] = params
        return _Response(box["messages"])

    monkeypatch.setattr(sync.requests, "get", _get)
    monkeypatch.setattr(sync, "get_microsoft_access_token", lambda account: "token")
    monkeypatch.setattr(sync, "record_sync_health", lambda *a, **k: None)
    return box


def test_the_sync_requests_the_identifiers_it_needs(graph):
    pid, address = _person()
    graph["messages"] = [_message(sender=address, to=[OWNER])]
    sync._ingest_messages(_account(), "token", 50, _people_map((pid, address, None)))

    selected = graph["params"]["$select"]
    for field in ("conversationId", "internetMessageId", "toRecipients", "ccRecipients"):
        assert field in selected


def test_a_matched_email_produces_one_timeline_row_and_one_communication_record(graph):
    household = _household()
    pid, address = _person(household_id=household)
    message = _message(sender=address, to=[OWNER])
    graph["messages"] = [message]

    result = sync._ingest_messages(_account(), "token", 50,
                                   _people_map((pid, address, household)))

    assert result["matched_messages"] == 1 and result["normalized_messages"] == 1
    with engine.connect() as c:
        events = c.execute(select(timeline_events).where(
            timeline_events.c.person_id == pid)).mappings().all()
        assert len(events) == 1 and events[0]["event_type"] == "email_received"
        conv = c.execute(select(communication_conversations).where(
            communication_conversations.c.person_id == pid)).mappings().all()
        assert len(conv) == 1 and conv[0]["channel"] == "email"


def test_rerunning_the_sync_adds_nothing(graph):
    pid, address = _person()
    graph["messages"] = [_message(sender=address, to=[OWNER])]
    people_map = _people_map((pid, address, None))

    sync._ingest_messages(_account(), "token", 50, people_map)
    sync._ingest_messages(_account(), "token", 50, people_map)

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == pid)) == 1
        assert c.scalar(select(func.count()).select_from(communication_conversations).where(
            communication_conversations.c.person_id == pid)) == 1


def test_a_failed_normalization_rolls_back_the_timeline_event(graph, monkeypatch):
    """The two writes share one transaction, so they can never disagree about whether this email
    was ingested."""
    pid, address = _person()
    graph["messages"] = [_message(sender=address, to=[OWNER])]

    def _boom(*a, **k):
        raise RuntimeError("normalization failed")

    monkeypatch.setattr(email_ingest, "normalize_email", _boom)
    with pytest.raises(RuntimeError):
        sync._ingest_messages(_account(), "token", 50, _people_map((pid, address, None)))

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == pid)) == 0


def test_the_owners_own_sent_mail_no_longer_pollutes_the_review_queue(graph):
    """The second pre-existing defect: outbound copies were queued as unrecognised inbound senders."""
    pid, address = _person()
    graph["messages"] = [_message(sender=OWNER, to=[address])]

    result = sync._ingest_messages(_account(), "token", 50, _people_map((pid, address, None)))

    assert result["unmatched_messages"] == 0
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(microsoft_unmatched_messages).where(
            microsoft_unmatched_messages.c.sender_address == OWNER)) == 0
        # ...and it is still normalized, anchored on the client it was sent to.
        conv = c.execute(select(communication_conversations).where(
            communication_conversations.c.person_id == pid)).mappings().all()
        assert len(conv) == 1
        direction = c.scalar(select(communication_messages.c.direction).where(
            communication_messages.c.conversation_id == conv[0]["id"]))
    assert direction == "outbound"


def test_an_unknown_sender_still_reaches_the_review_queue_unchanged(graph):
    """The queue contract is preserved: this is what it has always done."""
    stranger = f"stranger-{uuid.uuid4().hex[:8]}@elsewhere.test"
    graph["messages"] = [_message(sender=stranger, to=["nobody@else.test"])]

    result = sync._ingest_messages(_account(), "token", 50, {})

    assert result["unmatched_messages"] == 1
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(microsoft_unmatched_messages).where(
            microsoft_unmatched_messages.c.sender_address == stranger)) == 1


def test_a_recipient_match_normalizes_without_adding_a_timeline_event(graph):
    """Recipient matching widens what can be NORMALIZED, never what appears on a client's timeline.
    The message is still queued, exactly as before, because its sender is unknown."""
    pid, address = _person()
    graph["messages"] = [_message(sender="stranger@elsewhere.test", to=[address])]

    result = sync._ingest_messages(_account(), "token", 50, _people_map((pid, address, None)))

    assert result["unmatched_messages"] == 1 and result["normalized_messages"] == 1
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == pid)) == 0
        assert c.scalar(select(func.count()).select_from(communication_conversations).where(
            communication_conversations.c.person_id == pid)) == 1
