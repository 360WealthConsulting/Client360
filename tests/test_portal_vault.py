"""Client Portal MVP — Vault integration, messaging, requests, profile, audit, isolation.

Reuses the existing portal primitives (invite → accept → session → PortalPrincipal) and the merged
Vault. Covers: login, unauthorized access, client upload (pending), download (approval-gated),
employee approval workflow, request completion, secure messaging, audit entries, and permission
isolation (a client sees only their own client-visible vault documents).
"""
import io
import uuid

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import (
    audit_events,
    engine,
    households,
    people,
    portal_access_grants,
    portal_accounts,
    portal_devices,
    portal_document_requests,
    portal_sessions,
    roles,
    user_roles,
    users,
    vault_document_links,
    vault_documents,
)
from app.portal import profile as portal_profile
from app.portal import vault_documents as pv
from app.portal.service import (
    accept_invitation,
    create_document_request,
    create_portal_session,
    create_thread,
    invite_portal_account,
    list_messages,
    resolve_portal_session,
    send_message,
)
from app.security.models import Principal
from app.services.vault import service as vault

# The firm-wide portal surface gates now genuinely close their surfaces; these behavioural tests
# exercise the surfaces themselves, so they switch the gates on (see tests/conftest.py).
pytestmark = pytest.mark.usefixtures("portal_documents_upload_on", "portal_documents_download_on")


STAFF_CAPS = frozenset({"vault.view", "vault.upload", "vault.download", "vault.manage",
                        "vault.access.all", "record.read_all"})


class _Env:
    def __init__(self):
        self.suffix = uuid.uuid4().hex[:10]
        self.people = []
        self.households = []
        self.accounts = []
        self.docs = []
        with engine.begin() as c:
            self.user_id = c.execute(insert(users).values(
                email=f"staff-{self.suffix}@e.test", normalized_email=f"staff-{self.suffix}@e.test",
                display_name="Portal Staff", auth_subject=f"staff-{self.suffix}", status="active"
            ).returning(users.c.id)).scalar_one()
            role_id = c.scalar(select(roles.c.id).where(roles.c.code == "advisor"))
            if role_id:
                c.execute(insert(user_roles).values(user_id=self.user_id, role_id=role_id))
        self.staff = Principal(self.user_id, "staff@e.test", "Staff", STAFF_CAPS)

    def account(self, *, permissions=None):
        with engine.begin() as c:
            hid = c.execute(insert(households).values(name=f"HH {self.suffix}-{len(self.accounts)}")
                            .returning(households.c.id)).scalar_one()
            pid = c.execute(insert(people).values(household_id=hid, full_name=f"Client {self.suffix}",
                            active=True).returning(people.c.id)).scalar_one()
        self.households.append(hid)
        self.people.append(pid)
        account_id, invitation = invite_portal_account(
            person_id=pid, household_id=hid, email=f"c-{self.suffix}-{pid}@e.test",
            display_name="Portal Client", access_type="self", invited_by_user_id=self.user_id,
            permissions=permissions or {"documents": True, "messages": True})
        accept_invitation(invitation, f"subject-{self.suffix}-{pid}", True)
        token = create_portal_session(account_id, device_fingerprint=f"d-{uuid.uuid4()}")
        self.accounts.append(account_id)
        return account_id, resolve_portal_session(token), pid, hid

    def staff_doc(self, person_id, *, category="general", client_visible=False):
        doc_id = vault.create_document(
            self.staff, source=io.BytesIO(b"staff document"), original_filename="staff.pdf",
            display_name="Staff Doc", category=category, actor_user_id=self.user_id, person_id=person_id)
        self.docs.append(doc_id)
        if client_visible:
            vault.update_metadata(self.staff, doc_id, changes={"client_visible": True},
                                  actor_user_id=self.user_id)
        return doc_id

    def cleanup(self):
        """Best-effort teardown. Append-only tables (portal_messages, audit_events) and the rows they
        reference (threads, the staff user) are intentionally left in the disposable test DB — deleting
        them trips immutability triggers. Each delete runs independently so one block never aborts another."""
        from app.db import portal_invitations, portal_notifications

        def _try(stmt):
            try:
                with engine.begin() as c:
                    c.execute(stmt)
            except Exception:
                pass

        with engine.connect() as c:
            link_docs = [r[0] for r in c.execute(select(vault_document_links.c.document_id).where(
                vault_document_links.c.person_id.in_(self.people))).all()] if self.people else []
        for doc_id in set(self.docs) | set(link_docs):
            _try(delete(vault_documents).where(vault_documents.c.id == doc_id))   # cascades versions/links/audit
        for acc in self.accounts:
            _try(delete(portal_sessions).where(portal_sessions.c.portal_account_id == acc))
            _try(delete(portal_devices).where(portal_devices.c.portal_account_id == acc))
            _try(delete(portal_access_grants).where(portal_access_grants.c.portal_account_id == acc))
            _try(delete(portal_notifications).where(portal_notifications.c.portal_account_id == acc))
            _try(delete(portal_invitations).where(portal_invitations.c.portal_account_id == acc))
        if self.people:
            _try(delete(portal_document_requests).where(portal_document_requests.c.person_id.in_(self.people)))
        for acc in self.accounts:
            _try(delete(portal_accounts).where(portal_accounts.c.id == acc))
        if self.people:
            _try(delete(people).where(people.c.id.in_(self.people)))
        if self.households:
            _try(delete(households).where(households.c.id.in_(self.households)))


@pytest.fixture
def env():
    e = _Env()
    try:
        yield e
    finally:
        e.cleanup()


def _doc_row(doc_id):
    with engine.connect() as c:
        return c.execute(select(vault_documents).where(vault_documents.c.id == doc_id)).mappings().one()


def _audit_count(action, entity_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            (audit_events.c.action == action) & (audit_events.c.entity_id == str(entity_id))))


# --- login / unauthorized ----------------------------------------------------

def test_login_establishes_portal_session(env):
    account_id, principal, pid, _ = env.account()
    assert principal is not None
    assert principal.account_id == account_id and principal.person_id == pid


def test_unauthorized_access_denied():
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.routes.portal import current_portal
    req = Request({"type": "http", "method": "GET", "path": "/api/portal/documents",
                   "headers": [], "query_string": b"", "state": {}})
    with pytest.raises(HTTPException) as exc:
        current_portal(req)                       # no portal principal on state → 401
    assert exc.value.status_code == 401


# --- upload / download / approval --------------------------------------------

def test_upload_creates_pending_vault_document(env):
    _, principal, pid, _ = env.account()
    doc_id = pv.upload_document(principal, source=io.BytesIO(b"%PDF-1.4\nclient w2"),
                                original_filename="w2.pdf", display_name="My W-2", category="tax")
    row = _doc_row(doc_id)
    assert row["status"] == "uploaded" and row["client_visible"] is True
    assert row["uploaded_by_portal_account_id"] == principal.account_id
    with engine.connect() as c:
        linked = c.scalar(select(vault_document_links.c.person_id).where(
            vault_document_links.c.document_id == doc_id))
    assert linked == pid


def test_approval_workflow_makes_upload_official(env):
    _, principal, pid, _ = env.account()
    doc_id = pv.upload_document(principal, source=io.BytesIO(b"%PDF-1.4\nx"), original_filename="f.pdf",
                                display_name="Doc", category="general")
    assert _doc_row(doc_id)["status"] == "uploaded"          # pending
    pv.approve_upload(env.staff, doc_id, approved=True, actor_user_id=env.user_id)
    assert _doc_row(doc_id)["status"] == "approved"          # official
    # now downloadable by the client
    path, filename, _ = pv.download_document(principal, doc_id)
    assert path.exists() and filename == "f.pdf"


def test_download_requires_visible_and_approved(env):
    _, principal, pid, _ = env.account()
    # A staff doc NOT marked client-visible is invisible + not downloadable to the client.
    hidden = env.staff_doc(pid, client_visible=False)
    with pytest.raises(PermissionError):
        pv.download_document(principal, hidden)
    # Make it visible + approved → downloadable.
    vault.update_metadata(env.staff, hidden, changes={"client_visible": True, "status": "approved"},
                          actor_user_id=env.user_id)
    path, _, _ = pv.download_document(principal, hidden)
    assert path.exists()


def test_client_sees_only_client_visible_documents(env):
    _, principal, pid, _ = env.account()
    hidden = env.staff_doc(pid, client_visible=False)
    visible = env.staff_doc(pid, client_visible=True)
    ids = {d["id"] for d in pv.portal_documents(principal)}
    assert visible in ids and hidden not in ids


# --- request completion ------------------------------------------------------

def test_request_completion_marks_request_uploaded(env):
    _, principal, pid, hid = env.account()
    req_id = create_document_request(person_id=pid, household_id=hid, title="Upload your W-2",
                                     requested_by_user_id=env.user_id)
    pv.upload_document(principal, source=io.BytesIO(b"%PDF-1.4\nw2"), original_filename="w2.pdf",
                       display_name="W-2", category="tax", request_id=req_id)
    with engine.connect() as c:
        status = c.scalar(select(portal_document_requests.c.status).where(
            portal_document_requests.c.id == req_id))
    assert status == "uploaded"


# --- messaging ---------------------------------------------------------------

def test_secure_messaging_thread_and_reply(env):
    _, principal, pid, hid = env.account()
    thread_id = create_thread(principal, household_id=hid, person_id=pid,
                              subject="Question", body="Hello, I have a question.")
    send_message(principal, thread_id, "A follow-up message.")
    messages = list_messages(principal, thread_id)
    assert len(messages) >= 2
    # The client projection no longer exposes the internal ``visibility`` flag; assert the property
    # instead — a safe sender label, and no internal staff identity.
    assert all(m["sender_type"] == "client" for m in messages)
    assert all("visibility" not in m and "sender_user_id" not in m for m in messages)


# --- audit -------------------------------------------------------------------

def test_audit_entries_for_upload_download_and_profile(env):
    _, principal, pid, _ = env.account()
    doc_id = pv.upload_document(principal, source=io.BytesIO(b"%PDF-1.4\na"), original_filename="a.pdf",
                                display_name="A", category="general")
    assert _audit_count("portal.document.uploaded", doc_id) == 1
    pv.approve_upload(env.staff, doc_id, approved=True, actor_user_id=env.user_id)
    pv.download_document(principal, doc_id)
    assert _audit_count("portal.document.downloaded", doc_id) == 1
    portal_profile.update_profile(principal, {"phone": "540-555-0100", "preferred_contact_method": "email"})
    assert _audit_count("portal.profile.updated", principal.account_id) == 1


# --- permission isolation ----------------------------------------------------

def test_permission_isolation_between_clients(env):
    _, alice, alice_pid, _ = env.account()
    _, bob, bob_pid, _ = env.account()
    bob_doc = env.staff_doc(bob_pid, client_visible=True)   # a doc for Bob
    vault.update_metadata(env.staff, bob_doc, changes={"status": "approved"}, actor_user_id=env.user_id)
    # Alice cannot see or download Bob's document.
    assert bob_doc not in {d["id"] for d in pv.portal_documents(alice)}
    with pytest.raises(PermissionError):
        pv.download_document(alice, bob_doc)
    # Bob can.
    assert bob_doc in {d["id"] for d in pv.portal_documents(bob)}
    path, _, _ = pv.download_document(bob, bob_doc)
    assert path.exists()
