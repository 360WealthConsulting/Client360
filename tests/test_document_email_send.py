"""Emailing one canonical document from the signed-in staff user's Microsoft 365 mailbox.

Graph is ALWAYS mocked — no test sends real mail or opens a socket. The recurring invariants: the
exact stored bytes are attached under the clean delivery filename, the document row never changes,
and emailing can never widen document access beyond what download already allows.
"""
from __future__ import annotations

import base64
import hashlib
import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import audit_events, documents, engine, microsoft_accounts, people
from app.security.models import Principal
from app.services.communications import mail_send
from app.services.communications.mail_send import (
    MAX_ATTACHMENT_BYTES,
    DocumentNotAccessible,
    MailSendError,
    send_document_email,
    validate_recipient,
)

STAFF_EMAIL = "staff.sender@example.com"
STAFF = Principal(1, STAFF_EMAIL, "Staff", frozenset({"communications.send", "record.read_all"}))
OTHER_STAFF = Principal(2, "other.staff@example.com", "Other",
                        frozenset({"communications.send", "record.read_all"}))
NO_SEND = Principal(3, STAFF_EMAIL, "NoSend", frozenset({"record.read_all"}))
#: no record.read_all -> no document scope, exactly as the download route would deny
UNSCOPED = Principal(4, STAFF_EMAIL, "Unscoped", frozenset({"communications.send"}))

_TAGS: list[str] = []


class FakeGraph:
    """Records the single Graph call instead of making it."""

    def __init__(self, status_code=202, headers=None, raises=None):
        self.status_code, self.headers, self.raises = status_code, headers or {}, raises
        self.calls = []

    def __call__(self, token, payload):
        self.calls.append({"token": token, "payload": payload})
        if self.raises:
            raise self.raises
        return self

    # convenience accessors over the single recorded call
    @property
    def attachment(self):
        return self.calls[0]["payload"]["message"]["attachments"][0]

    @property
    def message(self):
        return self.calls[0]["payload"]["message"]


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        with engine.begin() as c:
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(f"%{tag}%"))))
            if ppl:
                c.execute(documents.delete().where(documents.c.person_id.in_(ppl)))
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            c.execute(documents.delete().where(documents.c.original_name.like(f"%{tag}%")))
            c.execute(microsoft_accounts.delete().where(microsoft_accounts.c.email.like("%example.com")))
    _TAGS.clear()


def _tag():
    t = "EML" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


@pytest.fixture
def connected_mailbox():
    """A connected Microsoft account for the staff sender; the token itself is stubbed."""
    with engine.begin() as c:
        c.execute(microsoft_accounts.delete().where(microsoft_accounts.c.email == STAFF_EMAIL))
        c.execute(insert(microsoft_accounts).values(
            email=STAFF_EMAIL, display_name="Staff Sender", tenant_id="t", user_id="graph-user-1",
            token_cache_encrypted="stub"))
    yield


@pytest.fixture(autouse=True)
def _stub_token(monkeypatch):
    monkeypatch.setattr(mail_send, "get_microsoft_access_token", lambda account: "TEST-TOKEN")


def _doc(c, tmp_path, name, *, display_name=None, body=b"THE-STORED-BYTES", person_id=None,
         content_type="application/pdf"):
    u = uuid.uuid4().hex
    f = tmp_path / f"{u}.bin"
    f.write_bytes(body)
    return c.execute(insert(documents).values(
        original_name=name, stored_name=f"stored-{u}", storage_path=str(f), storage_uri=str(f),
        size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), status="active",
        archived=False, display_name=display_name, content_type=content_type, person_id=person_id,
    ).returning(documents.c.id)).scalar_one()


def _row(doc_id):
    with engine.connect() as c:
        return dict(c.execute(select(documents).where(documents.c.id == doc_id)).mappings().one())


def _person(c, tag, email=None):
    return c.execute(insert(people).values(first_name="Adam", last_name=f"Davis{tag}", active=True,
                                           primary_email=email).returning(people.c.id)).scalar_one()


# --------------------------------------------------------------------- scope change
def test_mail_send_is_the_only_new_graph_permission():
    from app.services.microsoft_identity import GRAPH_DELEGATED_SCOPES, GRAPH_READ_SCOPES
    before = {"User.Read", "Mail.Read", "Calendars.Read", "Files.Read.All", "Sites.Read.All"}
    assert set(GRAPH_DELEGATED_SCOPES) - before == {"Mail.Send"}
    assert before - set(GRAPH_DELEGATED_SCOPES) == set()          # nothing removed
    for forbidden in ("Mail.ReadWrite", "Mail.Send.Shared", "Mail.ReadBasic", "User.ReadWrite"):
        assert forbidden not in GRAPH_DELEGATED_SCOPES
    assert GRAPH_READ_SCOPES is GRAPH_DELEGATED_SCOPES            # alias preserved


def test_inbound_microsoft_mail_reader_is_unchanged():
    import inspect

    from app.routes import microsoft365_mail
    src = inspect.getsource(microsoft365_mail)
    assert "sendMail" not in src and "Mail.Send" not in src


# --------------------------------------------------------------------- happy path
def test_document_with_display_name_sends_under_the_clean_filename(tmp_path, connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, tmp_path, f"2025 W2 (2) {tag}.pdf",
                   display_name="2025 - W-2 - ADAM DAVIS - (2)", person_id=pid)
    graph = FakeGraph()
    result = send_document_email(principal=STAFF, document_id=did, to="client@example.org",
                                 subject="Your W-2", body="Attached.", graph_post=graph)
    assert result["ok"] and result["provider"] == "microsoft_graph"
    assert result["attachment_filename"] == "2025 - W-2 - ADAM DAVIS - (2).pdf"
    assert graph.attachment["name"] == "2025 - W-2 - ADAM DAVIS - (2).pdf"
    assert graph.message["toRecipients"] == [{"emailAddress": {"address": "client@example.org"}}]
    assert graph.calls[0]["token"] == "TEST-TOKEN"                # the staff user's delegated token
    assert graph.calls[0]["payload"]["saveToSentItems"] is True


def test_null_display_name_falls_back_to_the_original_filename(tmp_path, connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"Fidelity 1099-R 2025 {tag}.pdf", person_id=_person(c, tag))
    graph = FakeGraph()
    send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                        body="b", graph_post=graph)
    assert graph.attachment["name"] == f"Fidelity 1099-R 2025 {tag}.pdf"


def test_exact_stored_bytes_are_base64_encoded_into_the_attachment(tmp_path, connected_mailbox):
    tag = _tag()
    payload_bytes = b"%PDF-1.7 exact bytes \x00\x01\x02 end"
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"doc {tag}.pdf", display_name="2025 - Form 1040 - Adam",
                   body=payload_bytes, person_id=_person(c, tag))
    graph = FakeGraph()
    send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                        body="b", graph_post=graph)
    assert base64.b64decode(graph.attachment["contentBytes"]) == payload_bytes
    assert hashlib.sha256(base64.b64decode(graph.attachment["contentBytes"])).hexdigest() == \
        _row(did)["sha256"]
    assert graph.attachment["@odata.type"] == "#microsoft.graph.fileAttachment"


@pytest.mark.parametrize("original,recorded,expected", [
    ("a.pdf", "application/pdf", "application/pdf"),
    ("a.pdf", None, "application/pdf"),          # guessed from the ORIGINAL extension
    ("a.xlsx", None, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("a.unknownext", None, "application/octet-stream"),
])
def test_mime_type_follows_the_download_convention(tmp_path, connected_mailbox, original, recorded,
                                                   expected):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"{tag} {original}", content_type=recorded, person_id=_person(c, tag))
    graph = FakeGraph()
    send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                        body="b", graph_post=graph)
    assert graph.attachment["contentType"] == expected


# --------------------------------------------------------------------- size policy
def test_just_under_three_megabytes_sends(tmp_path, connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"big {tag}.pdf", body=b"x" * (MAX_ATTACHMENT_BYTES - 1),
                   person_id=_person(c, tag))
    graph = FakeGraph()
    assert send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                               body="b", graph_post=graph)["ok"]
    assert len(graph.calls) == 1


@pytest.mark.parametrize("size", [MAX_ATTACHMENT_BYTES, MAX_ATTACHMENT_BYTES + 1])
def test_three_megabytes_or_larger_is_refused_before_any_graph_call(tmp_path, connected_mailbox,
                                                                    size):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"huge {tag}.pdf", body=b"x" * size, person_id=_person(c, tag))
    graph = FakeGraph()
    with pytest.raises(MailSendError) as exc:
        send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                            body="b", graph_post=graph)
    assert "too large" in str(exc.value).lower()
    assert graph.calls == []                                      # no Graph request occurred
    assert _row(did)["sha256"]                                    # document untouched


# --------------------------------------------------------------------- authorization
def test_capability_is_required():
    with pytest.raises(PermissionError) as exc:
        send_document_email(principal=NO_SEND, document_id=1, to="c@example.org", subject="s",
                            body="b", graph_post=FakeGraph())
    assert "communications.send" in str(exc.value)


def test_out_of_scope_document_is_refused_and_does_not_disclose_existence(tmp_path,
                                                                          connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"private {tag}.pdf", person_id=_person(c, tag))
    graph = FakeGraph()
    with pytest.raises(DocumentNotAccessible) as real:
        send_document_email(principal=UNSCOPED, document_id=did, to="c@example.org", subject="s",
                            body="b", graph_post=graph)
    with pytest.raises(DocumentNotAccessible) as missing:
        send_document_email(principal=UNSCOPED, document_id=2_000_000_000, to="c@example.org",
                            subject="s", body="b", graph_post=graph)
    assert str(real.value) == str(missing.value)                  # indistinguishable
    assert graph.calls == []


def test_a_staff_user_cannot_send_from_another_users_mailbox(tmp_path, connected_mailbox):
    """Only the connected account matching the caller's own sign-in address may be used."""
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"doc {tag}.pdf", person_id=_person(c, tag))
    graph = FakeGraph()
    with pytest.raises(MailSendError) as exc:
        send_document_email(principal=OTHER_STAFF, document_id=did, to="c@example.org",
                            subject="s", body="b", graph_post=graph)
    assert "not connected" in str(exc.value).lower()
    assert graph.calls == []


def test_no_filesystem_path_can_be_supplied(tmp_path, connected_mailbox):
    """document_id is the ONLY selector; the service takes no path argument at all."""
    import inspect
    sig = inspect.signature(send_document_email)
    assert set(sig.parameters) == {"principal", "document_id", "to", "subject", "body",
                                   "graph_post", "request_id"}
    for bogus in ("/etc/passwd", "../../etc/passwd", "1; DROP TABLE documents"):
        with pytest.raises(DocumentNotAccessible):
            send_document_email(principal=STAFF, document_id=bogus, to="c@example.org",
                                subject="s", body="b", graph_post=FakeGraph())


def test_malicious_display_name_is_sanitized_through_the_shared_helper(tmp_path, connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"safe {tag}.pdf",
                   display_name="../../etc/passwd\r\nBcc: attacker@evil.test",
                   person_id=_person(c, tag))
    graph = FakeGraph()
    send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                        body="b", graph_post=graph)
    name = graph.attachment["name"]
    assert not any(ch in name for ch in "\r\n/\\")
    assert ".." not in name and name.endswith(".pdf")


@pytest.mark.parametrize("bad", ["", "   ", "not-an-email", "a@b", "a@b.c\r\nBcc: x@y.zz",
                                 "one@x.com, two@y.com", "<a@b.com>"])
def test_invalid_recipients_are_refused(bad):
    with pytest.raises(MailSendError):
        validate_recipient(bad)


def test_valid_recipient_is_accepted():
    assert validate_recipient("  client@example.org ") == "client@example.org"


# --------------------------------------------------------------------- failure handling
def test_graph_error_status_produces_failure_not_false_success(tmp_path, connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"doc {tag}.pdf", person_id=_person(c, tag))
    with pytest.raises(MailSendError):
        send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                            body="b", graph_post=FakeGraph(status_code=403))
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "document.emailed.failed",
            audit_events.c.entity_id == str(did))) == 1


def test_graph_transport_exception_produces_failure(tmp_path, connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"doc {tag}.pdf", person_id=_person(c, tag))
    with pytest.raises(MailSendError):
        send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                            body="b", graph_post=FakeGraph(raises=OSError("network down")))


def test_successful_send_is_audited(tmp_path, connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"doc {tag}.pdf", display_name="2025 - W-2 - Adam",
                   person_id=_person(c, tag))
    send_document_email(principal=STAFF, document_id=did, to="client@example.org", subject="s",
                        body="b", graph_post=FakeGraph(headers={"request-id": "graph-abc"}))
    with engine.connect() as c:
        row = c.execute(select(audit_events).where(
            audit_events.c.action == "document.emailed.sent",
            audit_events.c.entity_id == str(did))).mappings().one()
    meta = row["metadata"]
    assert row["actor_user_id"] == STAFF.user_id
    assert meta["recipient"] == "client@example.org"
    assert meta["attachment_filename"] == "2025 - W-2 - Adam.pdf"
    assert meta["provider"] == "microsoft_graph"
    assert "contentBytes" not in str(meta) and "TEST-TOKEN" not in str(meta)   # no bytes, no tokens


# --------------------------------------------------------------------- invariants
def test_the_document_row_is_byte_identical_after_sending(tmp_path, connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"doc {tag}.pdf", display_name="2025 - W-2 - Adam",
                   person_id=_person(c, tag))
    before = _row(did)
    send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                        body="b", graph_post=FakeGraph())
    assert _row(did) == before


def test_existing_download_behaviour_is_unchanged(tmp_path, connected_mailbox):
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"doc {tag}.pdf", display_name="2025 - W-2 - Adam",
                   person_id=_person(c, tag))
    from app.routes.documents import download_document
    from tests._portal_util import fake_request
    resp = download_document(did, fake_request(f"/documents/{did}/download"))
    assert resp.filename == "2025 - W-2 - Adam.pdf"
    send_document_email(principal=STAFF, document_id=did, to="c@example.org", subject="s",
                        body="b", graph_post=FakeGraph())
    again = download_document(did, fake_request(f"/documents/{did}/download"))
    assert again.filename == resp.filename and str(again.path) == str(resp.path)


def test_routes_are_registered_and_capability_gated():
    from app.main import app
    from app.security.dependencies import CAPABILITY_DEP_ATTR
    routes = [r for r in app.routes
              if getattr(r, "path", None) == "/documents/{document_id}/email"]
    assert {m for r in routes for m in r.methods if m in ("GET", "POST")} == {"GET", "POST"}
    for r in routes:
        caps = [getattr(d.call, CAPABILITY_DEP_ATTR, None) for d in r.dependant.dependencies]
        assert ("communications.send",) in caps


# --------------------------------------------------------------------- compose page renders
def test_compose_page_renders_and_its_template_is_tracked(tmp_path, connected_mailbox):
    """Renders the real template. Without this, a missing or mislocated template file passes every
    other test and only fails in production — which is exactly what a bare ``documents/`` .gitignore
    rule caused once already.
    """
    from app.routes.document_email import compose_document_email
    from tests._portal_util import fake_request, render
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, email="client@example.org")
        did = _doc(c, tmp_path, f"2025 W2 (2) {tag}.pdf",
                   display_name="2025 - W-2 - ADAM DAVIS - (2)", person_id=pid)
    html = render(compose_document_email(
        request=fake_request(f"/documents/{did}/email", state_principal=STAFF),
        document_id=did, principal=STAFF))
    assert "Email document" in html
    assert "2025 - W-2 - ADAM DAVIS - (2).pdf" in html               # the delivered filename
    assert 'value="client@example.org"' in html                      # prefilled, still editable
    assert f'action="/documents/{did}/email"' in html                # attachment fixed to this id


def test_compose_page_refuses_an_oversized_document(tmp_path, connected_mailbox):
    from app.routes.document_email import compose_document_email
    from tests._portal_util import fake_request, render
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"huge {tag}.pdf", body=b"x" * MAX_ATTACHMENT_BYTES,
                   person_id=_person(c, tag))
    html = render(compose_document_email(
        request=fake_request(f"/documents/{did}/email", state_principal=STAFF),
        document_id=did, principal=STAFF))
    assert "Too large to email" in html
    # the SEND form is absent (the page still has the global search form from the base layout)
    assert f'action="/documents/{did}/email"' not in html
    assert 'name="to"' not in html


def test_compose_page_is_404_for_an_out_of_scope_document(tmp_path, connected_mailbox):
    from app.routes.document_email import compose_document_email
    from tests._portal_util import fake_request
    tag = _tag()
    with engine.begin() as c:
        did = _doc(c, tmp_path, f"private {tag}.pdf", person_id=_person(c, tag))
    resp = compose_document_email(
        request=fake_request(f"/documents/{did}/email", state_principal=UNSCOPED),
        document_id=did, principal=UNSCOPED)
    assert resp.status_code == 404
