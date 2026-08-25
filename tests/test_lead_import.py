"""Reviewed "Add to 360Plus" import of a forwarded lead email.

Modelled on the real production message: forwarder Lauren Curry, detected email
tillmanbowling@gmail.com, detected phone 5405620123, a detected NAME of "ctbvmi01" that must never
become a person's name, two real PDFs, and two inline signature images that must not be importable.
"""
from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

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
