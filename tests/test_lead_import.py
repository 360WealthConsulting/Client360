"""Reviewed "Add to 360Plus" import of a forwarded lead email.

Modelled on the real production message: forwarder Lauren Curry, detected email
tillmanbowling@gmail.com, detected phone 5405620123, a detected NAME of "ctbvmi01" that must never
become a person's name, two real PDFs, and two inline signature images that must not be importable.
"""
from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import documents, engine, metadata, microsoft_accounts, people, users
from app.security.models import Principal
from app.services import prospect_import
from app.services.forwarded_email import looks_like_human_name, split_human_name

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nreal enough\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

#: record.write_all because importing documents onto an existing person requires WRITE record scope
#: (has_record_scope bypasses on record.write_all, not record.read_all). The out-of-scope test below
#: deliberately uses a principal without it.
_CAP = {"communication.read", "client.read", "documents.view", "documents.edit", "client.write",
        "record.read_all", "record.write_all"}
_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    ds = metadata.tables["document_sources"]
    for tag in _TAGS:
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.last_name.like(f"%{tag}%"))))
            pids += list(c.scalars(select(people.c.id)
                                   .where(people.c.normalized_email.like(f"%{tag}%"))))
            if pids:
                docs = list(c.scalars(select(documents.c.id)
                                      .where(documents.c.person_id.in_(pids))))
                if docs:
                    c.execute(delete(ds).where(ds.c.document_id.in_(docs)))
                    c.execute(delete(documents).where(documents.c.id.in_(docs)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(ds).where(ds.c.source_uri.like(f"%{tag}%")))
            c.execute(delete(microsoft_accounts).where(microsoft_accounts.c.tenant_id == tag))
            # users are deliberately NOT deleted: audit_events.actor_user_id is ON DELETE SET
            # NULL, and the append-only audit trigger rejects that UPDATE. scripts/test.sh resets
            # the schema per run, so the rows do not accumulate.
    _TAGS.clear()


def _tag():
    t = "LI" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _user(tag) -> int:
    """A real users row: people.created_by_user_id has a FK, so a hardcoded id would make these
    tests depend on another test having seeded one."""
    who = f"staff.{uuid.uuid4().hex[:8]}.{tag}@firm.test".casefold()
    with engine.begin() as c:
        return c.execute(insert(users).values(
            email=who, normalized_email=who, display_name="Staff", status="active",
        ).returning(users.c.id)).scalar_one()


def _principal(email, caps=None, uid=None, tag=None):
    return Principal(uid if uid is not None else _user(tag), email, "Staff",
                     frozenset(_CAP if caps is None else caps))


def _mailboxes(tag):
    """Production-shaped rows: no plaintext token, encrypted cache, stale expiry."""
    now = datetime.now(UTC)
    with engine.begin() as c:
        for who in ("a", "b"):
            c.execute(insert(microsoft_accounts).values(
                tenant_id=tag, user_id=f"entra-{who}-{tag}", email=f"{who}.{tag}@firm.test",
                access_token=None, refresh_token=None,
                token_cache_encrypted=f"cache-{who}-{tag}",
                expires_at=now - timedelta(days=3), updated_at=now))
    return {"a_email": f"a.{tag}@firm.test", "b_email": f"b.{tag}@firm.test"}


def _attachments(tag):
    return [
        {"@odata.type": "#microsoft.graph.fileAttachment", "id": f"att-tax-{tag}",
         "name": "2025 tax return.pdf", "contentType": "application/pdf",
         "size": len(PDF), "isInline": False},
        {"@odata.type": "#microsoft.graph.fileAttachment", "id": f"att-settle-{tag}",
         "name": "6347 scruggs settlement statement.pdf", "contentType": "application/pdf",
         "size": len(PDF), "isInline": False},
        {"@odata.type": "#microsoft.graph.fileAttachment", "id": f"att-sig1-{tag}",
         "name": "image.png", "contentType": "image/png", "size": len(PNG), "isInline": True},
        {"@odata.type": "#microsoft.graph.fileAttachment", "id": f"att-sig2-{tag}",
         "name": "image.png", "contentType": "image/png", "size": len(PNG), "isInline": True},
    ]


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._p = payload or {}

    def json(self):
        return self._p


def _graph(monkeypatch, tag, *, owner_token=None, attachments=None):
    """Graph stub keyed by BEARER TOKEN: a message exists only under its own mailbox's token."""
    owner_token = owner_token or f"token-a-{tag}"
    atts = _attachments(tag) if attachments is None else attachments
    calls = []

    def _token(account):
        return (account["token_cache_encrypted"] or "").replace("cache-", "token-")

    def _get(url, headers=None, params=None, timeout=None):
        tok = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        calls.append({"url": url, "token": tok})
        if tok != owner_token:
            return _Resp(404)
        if url.endswith("/attachments"):
            return _Resp(200, {"value": atts})
        if "/attachments/" in url:
            aid = url.rsplit("/", 1)[1]
            a = next((x for x in atts if x["id"] == aid), None)
            if a is None:
                return _Resp(404)
            body = PNG if a["contentType"] == "image/png" else PDF
            return _Resp(200, {**a, "contentBytes": base64.b64encode(body).decode()})
        return _Resp(200, {
            "id": f"AAMk{tag}", "subject": "Fw: Tax liability",
            "from": {"emailAddress": {"name": "Lauren Curry",
                                      "address": f"lauren.{tag}@firm.test"}},
            "receivedDateTime": "2026-08-25T10:39:00Z", "hasAttachments": True})

    monkeypatch.setattr(prospect_import, "get_microsoft_access_token", _token, raising=False)
    monkeypatch.setattr(prospect_import.requests, "get", _get)
    import app.routes.lead_import as lr
    monkeypatch.setattr(lr, "get_microsoft_access_token", _token)
    return calls


def _do(tag, principal, **kw):
    return prospect_import.import_reviewed_lead(
        principal, token=f"token-a-{tag}", message_id=f"AAMk{tag}", **kw)


# --------------------------------------------------------------------------- NAME
def test_ctbvmi01_never_becomes_a_name():
    assert looks_like_human_name("ctbvmi01") is False
    assert split_human_name("ctbvmi01") == (None, None)


def test_jane_prospect_passes():
    assert looks_like_human_name("Jane Prospect") is True
    assert split_human_name("Jane Prospect") == ("Jane", "Prospect")


@pytest.mark.parametrize("bad", [
    "tillmanbowling", "jane.prospect", "user123", "a@b.com", "Jane", "J P", "", None, "x" * 90,
])
def test_machine_like_values_are_rejected(bad):
    assert looks_like_human_name(bad) is False


def test_value_equal_to_the_email_local_part_is_rejected():
    assert looks_like_human_name("tillmanbowling", email="tillmanbowling@gmail.com") is False
    # a real two-token name is still accepted even when derived from the same address
    assert looks_like_human_name("Tillman Bowling", email="tillmanbowling@gmail.com") is True


def test_the_reviewed_form_prefills_the_real_name_from_the_production_shape(monkeypatch):
    """End to end on the production body: the form offers the prospect's real name, his email, and
    a BLANK phone -- the forwarder's office number never reaches it."""
    from tests._portal_util import render
    from tests.test_forwarded_email_candidate import PRODUCTION_FORWARD
    from tests.test_microsoft_message_detail import _mailboxes as _mb
    from tests.test_microsoft_message_detail import _message, _open, _stub
    from tests.test_microsoft_message_detail import _tag as _t
    tag = _t(); f = _mb(tag)
    msg = _message(tag, body=PRODUCTION_FORWARD, subject="Fw: Tax liability")
    msg["from"] = {"emailAddress": {"name": "Lauren Curry",
                                    "address": "lauren@360wealthconsulting.com"}}
    _stub(monkeypatch, by_token={f"token-a-{tag}": msg})
    staff = Principal(1, f["a_email"], "Staff", frozenset(_CAP))
    html = render(_open(staff, f"AAMk{tag}"))
    assert 'name="first_name" value="Tillman"' in html
    assert 'name="last_name" value="Bowling"' in html
    assert 'value="tillmanbowling@gmail.com"' in html
    assert 'name="phone" value=""' in html            # NOT Lauren's 5405620123
    assert "5405620123" not in html
    assert "ctbvmi01" in html                          # provenance only


def test_the_preview_does_not_prefill_a_machine_name(monkeypatch):
    from tests._portal_util import render
    from tests.test_microsoft_message_detail import _mailboxes as _mb
    from tests.test_microsoft_message_detail import _message, _open, _stub
    from tests.test_microsoft_message_detail import _tag as _t
    tag = _t(); f = _mb(tag)
    body = (f"<div>From: ctbvmi01 &lt;tillmanbowling.{tag}@gmail.com&gt;<br>Sent: Mon<br>"
            f"To: Lauren<br>Subject: Tax liability<br></div><p>540-562-0123</p>")
    _stub(monkeypatch, by_token={f"token-a-{tag}": _message(tag, body=body)})
    # a principal WITH documents.edit, so the reviewed import form actually renders
    staff = Principal(1, f["a_email"], "Staff", frozenset(_CAP))
    html = render(_open(staff, f"AAMk{tag}"))
    assert 'name="first_name" value=""' in html and 'name="last_name" value=""' in html
    assert f'value="tillmanbowling.{tag}@gmail.com"' in html
    assert 'name="phone" value="5405620123"' in html
    assert "does not look like a" in html          # provenance note (text wraps)
    assert "ctbvmi01" in html                       # still shown as provenance


# --------------------------------------------------------------------------- PERSON
def _existing(tag, email=None, phone=None, first="Tillman", last="Bowling"):
    from app.security.identity_utils import normalize_email
    from app.services.people import _normalize_phone
    with engine.begin() as c:
        return c.execute(insert(people).values(
            first_name=first, last_name=f"{last}{tag}",
            primary_email=email, normalized_email=normalize_email(email) or None,
            primary_phone=phone, normalized_phone=_normalize_phone(phone),
            contact_type="client", active=True).returning(people.c.id)).scalar_one()


def test_existing_person_is_reused_not_duplicated(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    pid = _existing(tag, email=f"tillmanbowling.{tag}@gmail.com")
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(people))
    r = _do(tag, _principal("a@f.test", tag=tag), person_id=pid, attachment_ids=[])
    assert r["person_id"] == pid and r["person_created"] is False
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(people)) == before


def test_create_new_prospect_uses_contact_type_prospect(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    r = _do(tag, _principal("a@f.test", tag=tag), create_new=True, first_name="Tillman",
            last_name=f"Bowling{tag}", email=f"tillmanbowling.{tag}@gmail.com",
            phone="540-562-0123", attachment_ids=[])
    assert r["person_created"] is True
    with engine.connect() as c:
        row = c.execute(select(people).where(people.c.id == r["person_id"])).mappings().one()
    assert row["contact_type"] == "prospect"
    assert row["normalized_email"] == f"tillmanbowling.{tag}@gmail.com".casefold()
    assert row["normalized_phone"] == "5405620123"
    assert row["full_name"] == f"Tillman Bowling{tag}"


def test_repeated_submission_does_not_create_a_second_person(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    kw = dict(create_new=True, first_name="Tillman", last_name=f"Bowling{tag}",
              email=f"tillmanbowling.{tag}@gmail.com", attachment_ids=[])
    _do(tag, _principal("a@f.test", tag=tag), **kw)
    with pytest.raises(prospect_import.LeadImportError, match="already exists"):
        _do(tag, _principal("a@f.test", tag=tag), **kw)
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(people).where(
            people.c.normalized_email == f"tillmanbowling.{tag}@gmail.com".casefold())) == 1


def test_create_new_requires_client_write(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    no_write = _principal("a@f.test", caps=_CAP - {"client.write"}, tag=tag)
    with pytest.raises(prospect_import.LeadImportError, match="client.write"):
        _do(tag, no_write, create_new=True, first_name="Jane", last_name=f"Doe{tag}",
            email=f"jane.{tag}@x.test", attachment_ids=[])
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(people)
                        .where(people.c.last_name == f"Doe{tag}")) == 0


def test_prospect_requires_a_surname_and_an_email(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    p = _principal("a@f.test", tag=tag)
    with pytest.raises(prospect_import.LeadImportError, match="last name"):
        _do(tag, p, create_new=True, first_name="Jane", email="j@x.test", attachment_ids=[])
    with pytest.raises(prospect_import.LeadImportError, match="email"):
        _do(tag, p, create_new=True, last_name=f"Doe{tag}", attachment_ids=[])


def test_out_of_scope_existing_person_is_rejected(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    pid = _existing(tag, email=f"x.{tag}@x.test")
    limited = Principal(_user(tag), "b@f.test", "L", frozenset(_CAP - {"record.read_all", "record.write_all"}))
    with pytest.raises(prospect_import.NotAccessible):
        _do(tag, limited, person_id=pid, attachment_ids=[])


def test_neither_choice_is_refused(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    with pytest.raises(prospect_import.LeadImportError, match="Choose an existing"):
        _do(tag, _principal("a@f.test", tag=tag), attachment_ids=[])


# --------------------------------------------------------------------------- ATTACHMENTS
def test_inline_images_are_not_importable(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    eligible = prospect_import.eligible_attachments(f"token-a-{tag}", f"AAMk{tag}")
    assert set(eligible) == {f"att-tax-{tag}", f"att-settle-{tag}"}
    assert f"att-sig1-{tag}" not in eligible


def test_selecting_an_inline_image_is_rejected(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    pid = _existing(tag, email=f"x.{tag}@x.test")
    with pytest.raises(prospect_import.LeadImportError, match="do not belong"):
        _do(tag, _principal("a@f.test", tag=tag),
            person_id=pid, attachment_ids=[f"att-sig1-{tag}"])


def test_attachment_id_from_another_message_is_rejected(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    pid = _existing(tag, email=f"x.{tag}@x.test")
    with pytest.raises(prospect_import.LeadImportError, match="do not belong"):
        _do(tag, _principal("a@f.test", tag=tag),
            person_id=pid, attachment_ids=["att-from-some-other-message"])


def test_selected_pdfs_are_imported_and_unselected_bytes_never_fetched(monkeypatch):
    tag = _tag(); _mailboxes(tag); calls = _graph(monkeypatch, tag)
    pid = _existing(tag, email=f"x.{tag}@x.test")
    r = _do(tag, _principal("a@f.test", tag=tag),
            person_id=pid, attachment_ids=[f"att-tax-{tag}"])
    assert len(r["imported"]) == 1 and r["imported"][0]["name"] == "2025 tax return.pdf"
    fetched = [c["url"] for c in calls if "/attachments/" in c["url"]]
    assert any(f"att-tax-{tag}" in u for u in fetched)
    assert not any(f"att-settle-{tag}" in u for u in fetched)     # unselected: never fetched
    assert not any("att-sig" in u for u in fetched)


def test_imported_document_is_canonical_and_owned_by_the_person(monkeypatch):
    import hashlib
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    pid = _existing(tag, email=f"x.{tag}@x.test")
    r = _do(tag, _principal("a@f.test", tag=tag),
            person_id=pid, attachment_ids=[f"att-tax-{tag}", f"att-settle-{tag}"])
    assert len(r["imported"]) == 2
    with engine.connect() as c:
        rows = c.execute(select(documents).where(documents.c.person_id == pid)).mappings().all()
    assert {x["original_name"] for x in rows} == {"2025 tax return.pdf",
                                                  "6347 scruggs settlement statement.pdf"}
    for x in rows:
        assert x["household_id"] is None and x["organization_id"] is None      # exactly one owner
        assert x["sha256"] == hashlib.sha256(PDF).hexdigest()
        assert x["size_bytes"] == len(PDF)
        assert x["content_type"] == "application/pdf"
        assert x["stored_name"] and x["stored_name"] != x["original_name"]     # random safe name
        assert str(pid) in x["storage_path"]


# --------------------------------------------------------------------------- IDEMPOTENCY
def test_second_submission_imports_nothing_twice(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    pid = _existing(tag, email=f"x.{tag}@x.test")
    p = _principal("a@f.test", tag=tag)
    first = _do(tag, p, person_id=pid, attachment_ids=[f"att-tax-{tag}"])
    second = _do(tag, p, person_id=pid, attachment_ids=[f"att-tax-{tag}"])
    assert len(first["imported"]) == 1 and first["skipped"] == []
    assert second["imported"] == [] and len(second["skipped"]) == 1
    assert second["skipped"][0]["document_id"] == first["imported"][0]["document_id"]
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(documents)
                        .where(documents.c.person_id == pid)) == 1


def test_provenance_uses_the_exact_message_and_attachment_identity(monkeypatch):
    tag = _tag(); _mailboxes(tag); _graph(monkeypatch, tag)
    pid = _existing(tag, email=f"x.{tag}@x.test")
    p = _principal("a@f.test", tag=tag)
    r = _do(tag, p, person_id=pid, attachment_ids=[f"att-tax-{tag}"])
    ds = metadata.tables["document_sources"]
    with engine.connect() as c:
        row = c.execute(select(ds).where(
            ds.c.document_id == r["imported"][0]["document_id"])).mappings().one()
    assert row["source_system"] == "microsoft365_mail"
    assert row["source_uri"] == f"outlook:message/AAMk{tag}/attachment/att-tax-{tag}"
    assert row["source_external_id"] == f"att-tax-{tag}"
    md = row["metadata"]
    assert md["graph_message_id"] == f"AAMk{tag}"
    assert md["subject"] == "Fw: Tax liability"
    assert md["forwarder_email"] == f"lauren.{tag}@firm.test"
    assert md["imported_by_user_id"] == p.user_id and md["imported_at"]
    assert "contentBytes" not in str(md) and "token" not in str(md).lower()


def test_same_filename_different_attachment_ids_stay_distinct(monkeypatch):
    """Idempotency is source identity, never filename."""
    tag = _tag(); _mailboxes(tag)
    same_name = [
        {"@odata.type": "#microsoft.graph.fileAttachment", "id": f"att-1-{tag}",
         "name": "statement.pdf", "contentType": "application/pdf",
         "size": len(PDF), "isInline": False},
        {"@odata.type": "#microsoft.graph.fileAttachment", "id": f"att-2-{tag}",
         "name": "statement.pdf", "contentType": "application/pdf",
         "size": len(PDF), "isInline": False},
    ]
    _graph(monkeypatch, tag, attachments=same_name)
    pid = _existing(tag, email=f"x.{tag}@x.test")
    r = _do(tag, _principal("a@f.test", tag=tag),
            person_id=pid, attachment_ids=[f"att-1-{tag}", f"att-2-{tag}"])
    assert len(r["imported"]) == 2
    ds = metadata.tables["document_sources"]
    with engine.connect() as c:
        uris = set(c.scalars(select(ds.c.source_uri).where(
            ds.c.document_id.in_([d["document_id"] for d in r["imported"]]))))
    assert uris == {f"outlook:message/AAMk{tag}/attachment/att-1-{tag}",
                    f"outlook:message/AAMk{tag}/attachment/att-2-{tag}"}


# --------------------------------------------------------------------------- MAILBOX SECURITY
def test_a_cannot_import_from_bs_mailbox(monkeypatch):
    """The message exists only under B's token; A's token 404s and nothing is written."""
    tag = _tag(); _mailboxes(tag)
    _graph(monkeypatch, tag, owner_token=f"token-b-{tag}")
    pid = _existing(tag, email=f"x.{tag}@x.test")
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(documents))
    with pytest.raises(prospect_import.NotAccessible):
        _do(tag, _principal("a@f.test", tag=tag),
            person_id=pid, attachment_ids=[f"att-tax-{tag}"])
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(documents)) == before


# --------------------------------------------------------------------------- ROUTE
def test_route_requires_documents_edit_and_lives_outside_microsoft365():
    from app.main import app
    from app.routes.lead_import import lead_import
    from app.security.dependencies import CAPABILITY_DEP_ATTR
    from app.security.middleware import RULES
    dep = next(d.dependency for d in lead_import.__defaults__
               if getattr(d, "dependency", None) is not None)
    assert getattr(dep, CAPABILITY_DEP_ATTR) == ("documents.edit",)
    # No middleware RULE matches, so the fail-closed default applies unless the route self-protects
    # -- which is exactly why the documents.edit dependency above is the authoritative gate.
    assert next((c for pat, c in RULES if pat.search("/lead-import/AAMk1")), None) is None
    route = next(r for r in app.routes if getattr(r, "path", None) == "/lead-import/{message_id}")
    assert set(route.methods) == {"POST"}
    # and the preview is still a read-only GET under the Microsoft prefix
    assert next(c for pat, c in RULES if pat.search("/microsoft365/mail/x")) == "communication.read"


def test_no_connected_account_fails_closed(monkeypatch):
    import app.routes.lead_import as lr
    tag = _tag(); _mailboxes(tag)

    async def _run():
        from tests._portal_util import fake_request
        req = fake_request("/lead-import/x", method="POST")
        req.body = lambda: _empty()
        return await lr.lead_import(req, message_id=f"AAMk{tag}",
                                    principal=_principal(f"nobody.{tag}@firm.test", tag=tag))

    async def _empty():
        return b""

    import asyncio
    resp = asyncio.get_event_loop().run_until_complete(_run())
    assert resp.status_code == 303
    assert resp.headers["location"] == "/microsoft365/connect"


# --------------------------------------------------------------------------- GENERAL
def test_no_graph_writes_and_no_new_scopes():
    import inspect

    from app.services.microsoft_identity import GRAPH_DELEGATED_SCOPES
    src = inspect.getsource(prospect_import)
    for verb in ("requests.post", "requests.put", "requests.patch", "requests.delete", "sendMail"):
        assert verb not in src
    assert GRAPH_DELEGATED_SCOPES == ["User.Read", "Mail.Read", "Mail.Send", "Calendars.Read",
                                      "Files.Read.All", "Sites.Read.All"]


def test_no_vault_or_sharepoint_write():
    import inspect
    src = inspect.getsource(prospect_import)
    for bad in ("vault_documents", "sharepoint", "onedrive", "/drives/"):
        assert bad not in src.lower()


def test_the_real_gmail_nested_shape_prefills_safely_end_to_end(monkeypatch):
    """The production page, rebuilt: Outlook forward wrapping a Gmail reply, Exchange display name.

    Asserts the reviewed form offers the PROSPECT and never the forwarder -- and that the unrelated
    client holding the forwarder's office number is not offered as a match.
    """
    from tests._portal_util import render
    from tests.test_forwarded_email_candidate import REAL_FORWARD
    from tests.test_microsoft_message_detail import _mailboxes as _mb
    from tests.test_microsoft_message_detail import _message, _open, _stub
    from tests.test_microsoft_message_detail import _tag as _t
    tag = _t(); f = _mb(tag)
    decoy = _existing(_tag(), first="Mike", last="Agree", phone="540-562-0123")

    msg = _message(tag, body=REAL_FORWARD, subject="Fw: Tax liability")
    msg["from"] = {"emailAddress": {"name": "Curry, Lauren",
                                    "address": "lauren@360wealthconsulting.com"}}
    _stub(monkeypatch, by_token={f"token-a-{tag}": msg})
    staff = Principal(1, f["a_email"], "Staff", frozenset(_CAP))
    html = render(_open(staff, f"AAMk{tag}"))

    assert 'name="first_name" value="Tillman"' in html
    assert 'name="last_name" value="Bowling"' in html
    assert 'value="tillmanbowling@gmail.com"' in html
    assert 'name="phone" value=""' in html
    assert 'value="5405620123"' not in html          # never the forwarder's number as a form value
    assert f"/client/{decoy}" not in html            # and therefore no false existing-person match
    assert "ctbvmi01" in html                        # provenance remains visible


# --------------------------------------------------------------------------------------------
# BROWSER-SEMANTICS regression for the Add to 360Plus form.
#
# Production reported {"detail":"Choose an existing client or create a new prospect."} while the
# "Create new prospect" radio was visibly selected. The radio was rendered checked AND disabled: it
# paints as selected, but a disabled control is not a successful control, so the browser omitted
# person_choice entirely and the handler saw no choice.
#
# The suite missed it because every earlier test called the service with keyword arguments or
# hand-built a payload that already satisfied the backend. These tests instead parse the ACTUAL
# rendered HTML and submit exactly what a browser would.

class _FormHarvest(HTMLParser):
    """Collects the successful controls of the POST form, per HTML submission rules."""

    def __init__(self):
        super().__init__()
        self.in_form = False
        self.action = None
        self.fields = []        # what a browser would send
        self.controls = []      # (type, name, value, checked, disabled) for every control
        self.has_submit = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and (a.get("method") or "").lower() == "post":
            self.in_form, self.action = True, a.get("action")
            return
        if not self.in_form or tag not in ("input", "button", "select", "textarea"):
            return
        itype = (a.get("type") or "text").lower()
        name, value = a.get("name"), a.get("value", "")
        checked, disabled = "checked" in a, "disabled" in a
        self.controls.append((itype, name, value, checked, disabled, "required" in a))
        if itype in ("submit",) or tag == "button":
            self.has_submit = self.has_submit or not disabled
            return
        if not name or disabled:
            return
        if itype in ("radio", "checkbox"):
            if checked:
                self.fields.append((name, value))
            return
        self.fields.append((name, value))

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


def _harvest(html):
    h = _FormHarvest()
    h.feed(html)
    return h


def _render_review(monkeypatch, caps, *, with_existing=False):
    """Render the real mail-detail page for a principal with `caps`.

    The candidate's address is made unique per test. Hermetic on purpose: the first version of these
    tests used the fixture's hard-coded address, and a person left behind by an earlier test matched
    it, so a "no existing match" case silently rendered WITH a match.
    """
    from tests._portal_util import render
    from tests.test_forwarded_email_candidate import REAL_FORWARD
    from tests.test_microsoft_message_detail import _mailboxes as _mb
    from tests.test_microsoft_message_detail import _message, _open, _stub
    from tests.test_microsoft_message_detail import _tag as _t
    tag = _t(); f = _mb(tag)
    # Case A genuinely creates a prospect through the real POST path. Its tag comes from the
    # message-detail module, whose cleanup this file does not run, so register it here too --
    # otherwise that prospect survives and makes the next "no existing match" case find one.
    _TAGS.append(tag)
    candidate_email = f"tillmanbowling.{tag}@gmail.com".casefold()
    body = REAL_FORWARD.replace("tillmanbowling@gmail.com", candidate_email)
    if with_existing:
        _existing(_tag(), first="Tillman", last="Bowling", email=candidate_email)
    msg = _message(tag, body=body, subject="Fw: Tax liability")
    msg["from"] = {"emailAddress": {"name": "Curry, Lauren",
                                    "address": "lauren@360wealthconsulting.com"}}
    _stub(monkeypatch, by_token={f"token-a-{tag}": msg})
    principal = Principal(_user(tag), f["a_email"], "Staff", frozenset(caps))
    return tag, principal, render(_open(principal, f"AAMk{tag}"))


def _post_harvested(monkeypatch, tag, principal, fields):
    """POST exactly what the browser would send, to the real route."""
    import asyncio
    from urllib.parse import urlencode

    import app.routes.lead_import as lr
    from tests._portal_util import fake_request
    monkeypatch.setattr(lr, "get_microsoft_access_token",
                        lambda a: (a["token_cache_encrypted"] or "").replace("cache-", "token-"))
    req = fake_request(f"/lead-import/AAMk{tag}", method="POST")
    body = urlencode(fields).encode()

    async def _body():
        return body
    req.body = _body
    return asyncio.new_event_loop().run_until_complete(
        lr.lead_import(req, message_id=f"AAMk{tag}", principal=principal))


_IMPORT_CAPS = {"communication.read", "client.read", "documents.view", "documents.edit",
                "record.read_all", "record.write_all"}


def test_a_with_client_write_and_no_match_the_browser_sends_create_new(monkeypatch):
    tag, p, html = _render_review(monkeypatch, _IMPORT_CAPS | {"client.write"})
    h = _harvest(html)
    radios = [c for c in h.controls if c[0] == "radio"]
    # checked, ENABLED, and required
    assert ("radio", "person_choice", "create_new", True, False, True) in radios
    assert dict(h.fields).get("person_choice") == "create_new"
    assert h.has_submit
    resp = _post_harvested(monkeypatch, tag, p, h.fields)
    # reaches the real path -- not the misleading 400
    assert resp.status_code != 400
    assert b"Choose an existing client" not in getattr(resp, "body", b"")


def test_b_without_client_write_and_no_match_offers_nothing_submittable(monkeypatch):
    tag, p, html = _render_review(monkeypatch, _IMPORT_CAPS)
    h = _harvest(html)
    assert not any(c[1] == "person_choice" for c in h.controls)   # no radio at all
    assert not h.has_submit                                        # nothing to press
    assert "the client.write capability" in html          # wording wraps across lines
    assert "No existing client matched this message" in html
    # the browser cannot construct the production payload any more
    assert "person_choice" not in dict(h.fields)


def test_b_the_production_payload_is_no_longer_reachable(monkeypatch):
    """The old failure came from submitting a form whose only choice was checked+disabled."""
    _, _, html = _render_review(monkeypatch, _IMPORT_CAPS)
    h = _harvest(html)
    assert h.fields == [], "no form should be submittable at all in this state"


def test_c_without_client_write_an_existing_match_is_still_selectable(monkeypatch):
    tag, p, html = _render_review(monkeypatch, _IMPORT_CAPS, with_existing=True)
    h = _harvest(html)
    choice = dict(h.fields).get("person_choice")
    assert choice and choice.startswith("existing:")
    assert h.has_submit
    assert not any(c[2] == "create_new" for c in h.controls)      # create is not offered
    assert "requires the client.write capability" in html
    resp = _post_harvested(monkeypatch, tag, p, h.fields)
    # lacking client.write must NOT block attaching to an authorised existing client
    assert resp.status_code == 303
    assert f"/client/{choice.split(':', 1)[1]}" in resp.headers["location"]


@pytest.mark.parametrize("caps,existing", [
    (_IMPORT_CAPS | {"client.write"}, False),
    (_IMPORT_CAPS, False),
    (_IMPORT_CAPS | {"client.write"}, True),
    (_IMPORT_CAPS, True),
])
def test_d_no_control_is_ever_both_checked_and_disabled(monkeypatch, caps, existing):
    """The defect class, not just the instance."""
    _, _, html = _render_review(monkeypatch, caps, with_existing=existing)
    for itype, name, value, checked, disabled, _required in _harvest(html).controls:
        assert not (checked and disabled), (itype, name, value)


# --------------------------------------------------------------------------------------------
# Explicit choice with MULTIPLE matches.
#
# b9897ac fixed a choice that could not be submitted. This is the other way to reach the same
# message: with several matches nothing is preselected -- deliberately, because auto-picking one of
# several same-surname records is the duplicate-client hazard the reviewed flow exists to prevent --
# so pressing submit without clicking sent no person_choice and produced a raw JSON 400.
#
# `required` stops that round-trip in the browser. It is a convenience, never the enforcement:
# test G posts straight past it and the service still refuses.

def _radios(html):
    return [c for c in _harvest(html).controls if c[1] == "person_choice"]


def _after_click(html, value):
    """The payload a browser sends once the staff member clicks one radio."""
    h = _harvest(html)
    fields = [(n, v) for n, v in h.fields if n != "person_choice"]
    return [("person_choice", value)] + fields


def _render_multi(monkeypatch, caps):
    """Two people sharing the candidate address -> matches.outcome == 'multiple'."""
    from tests._portal_util import render
    from tests.test_forwarded_email_candidate import REAL_FORWARD
    from tests.test_microsoft_message_detail import _mailboxes as _mb
    from tests.test_microsoft_message_detail import _message, _open, _stub
    from tests.test_microsoft_message_detail import _tag as _t
    tag = _t(); f = _mb(tag)
    _TAGS.append(tag)
    email = f"tillmanbowling.{tag}@gmail.com".casefold()
    t2 = _tag()
    a = _existing(t2, first="Tillman", last="Bowling", email=email)
    b = _existing(t2, first="Tillmann", last="Bowling", email=email)
    msg = _message(tag, body=REAL_FORWARD.replace("tillmanbowling@gmail.com", email),
                   subject="Fw: Tax liability")
    msg["from"] = {"emailAddress": {"name": "Curry, Lauren",
                                    "address": "lauren@360wealthconsulting.com"}}
    _stub(monkeypatch, by_token={f"token-a-{tag}": msg})
    principal = Principal(_user(tag), f["a_email"], "Staff", frozenset(caps))
    return tag, principal, render(_open(principal, f"AAMk{tag}")), (a, b)


def test_multi_a_nothing_preselected_but_every_radio_is_required(monkeypatch):
    _, _, html, (a, b) = _render_multi(monkeypatch, _IMPORT_CAPS | {"client.write"})
    radios = _radios(html)
    values = {c[2] for c in radios}
    assert values == {f"existing:{a}", f"existing:{b}", "create_new"}
    for _itype, _name, value, checked, disabled, required in radios:
        assert not disabled, value
        assert required, f"{value} must carry required"
        assert not checked, f"{value} must not be preselected when several matched"
    # browser semantics: nothing submitted until staff choose
    assert "person_choice" not in dict(_harvest(html).fields)
    assert "choose an existing client or select Create new prospect" in html


def test_multi_b_selecting_create_new_sends_create_new(monkeypatch):
    tag, p, html, _ = _render_multi(monkeypatch, _IMPORT_CAPS | {"client.write"})
    fields = _after_click(html, "create_new")
    assert dict(fields)["person_choice"] == "create_new"
    resp = _post_harvested(monkeypatch, tag, p, fields)
    body = getattr(resp, "body", b"")
    # The create branch is genuinely entered -- it is no longer the "no choice made" refusal.
    assert b"Choose an existing client or create a new prospect." not in body
    # And here it is correctly refused further in, by the in-transaction duplicate guard: those
    # matches exist precisely BECAUSE someone already holds this address.
    assert b"already exists" in body


def test_multi_c_selecting_an_existing_match_sends_that_id(monkeypatch):
    tag, p, html, (a, _b) = _render_multi(monkeypatch, _IMPORT_CAPS | {"client.write"})
    fields = _after_click(html, f"existing:{a}")
    assert dict(fields)["person_choice"] == f"existing:{a}"
    resp = _post_harvested(monkeypatch, tag, p, fields)
    assert resp.status_code == 303
    assert f"/client/{a}" in resp.headers["location"]


def test_multi_d_one_exact_match_stays_preselected_and_required(monkeypatch):
    _, _, html = _render_review(monkeypatch, _IMPORT_CAPS | {"client.write"}, with_existing=True)
    existing = [c for c in _radios(html) if c[2].startswith("existing:")]
    assert len(existing) == 1
    assert existing[0][3] is True          # checked
    assert existing[0][5] is True          # required


def test_multi_e_no_match_keeps_create_new_preselected_and_required(monkeypatch):
    _, _, html = _render_review(monkeypatch, _IMPORT_CAPS | {"client.write"})
    create = [c for c in _radios(html) if c[2] == "create_new"]
    assert len(create) == 1
    assert create[0][3] is True            # checked
    assert create[0][5] is True            # required


def test_multi_f_no_match_and_no_client_write_still_renders_no_usable_form(monkeypatch):
    """The b9897ac fix is untouched by adding required."""
    _, _, html = _render_review(monkeypatch, _IMPORT_CAPS)
    assert _radios(html) == []
    assert not _harvest(html).has_submit


def test_multi_g_the_server_still_refuses_a_post_with_no_choice(monkeypatch):
    """`required` is browser convenience. Bypass it and the service still fails closed."""
    tag, p, html, _ = _render_multi(monkeypatch, _IMPORT_CAPS | {"client.write"})
    no_choice = [(n, v) for n, v in _harvest(html).fields if n != "person_choice"]
    assert "person_choice" not in dict(no_choice)
    resp = _post_harvested(monkeypatch, tag, p, no_choice)
    assert resp.status_code == 400
    assert b"Choose an existing client or create a new prospect." in resp.body
