"""Client-portal security review — the eight adversarial scenarios required before a real client is
let onto the portal. Every check drives the actual routes/services (not mocks) with real principals.

  1. A client cannot access another client's documents.
  2. A client cannot access another client's messages.
  3. Forged person / household / document / request IDs are rejected.
  4. A client's upload cannot be assigned to an entity they are not authorized for.
  5. Profile updates cannot modify protected (non-allow-listed) fields.
  6. Staff reply requires the capability AND record scope (buttons carry no authority).
  7. Every mutation is audited.
  8. Logout invalidates the session server-side.
"""
from __future__ import annotations

import io

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import (
    audit_events,
    engine,
    portal_document_requests,
    vault_document_links,
)
from app.portal import profile as portal_profile
from app.portal import vault_documents as pv
from app.portal.service import (
    create_document_request,
    create_portal_session,
    create_thread,
    list_messages,
    resolve_portal_session,
    send_message,
)
from app.routes.portal import (
    portal_logout_browser,
    portal_message_reply,
    portal_message_thread_page,
)
from app.routes.portal_admin import portal_admin_thread, portal_admin_thread_reply
from app.routes.portal_api import api_download_document
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.vault import service as vault
from tests._portal_util import fake_request, seed_portal_account, seed_staff_user

STAFF_CAPS = frozenset({"vault.view", "vault.upload", "vault.download", "vault.manage",
                        "vault.access.all", "record.read_all", "record.write_all"})


def _staff_principal(uid, caps):
    return Principal(uid, "staff@e.test", "Staff", frozenset(caps))


def _approved_visible_doc(staff, staff_uid, person_id):
    doc_id = vault.create_document(staff, source=io.BytesIO(b"secret"), original_filename="s.pdf",
                                   display_name="Bob Doc", category="general",
                                   actor_user_id=staff_uid, person_id=person_id)
    vault.update_metadata(staff, doc_id, changes={"client_visible": True, "status": "approved"},
                          actor_user_id=staff_uid)
    return doc_id


def _audits(action, entity_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            (audit_events.c.action == action) & (audit_events.c.entity_id == str(entity_id))))


# 1 -------------------------------------------------------------------------
def test_client_cannot_access_another_clients_documents():
    staff_uid = seed_staff_user()
    staff = _staff_principal(staff_uid, STAFF_CAPS)
    _, alice, _, _ = seed_portal_account(staff_uid)
    _, bob, bob_pid, _ = seed_portal_account(staff_uid)
    bob_doc = _approved_visible_doc(staff, staff_uid, bob_pid)

    assert bob_doc not in {d["id"] for d in pv.portal_documents(alice)}       # not listed
    with pytest.raises(HTTPException) as ei:                                   # not downloadable
        api_download_document(fake_request("/api/portal/documents"), bob_doc, alice)
    assert ei.value.status_code == 403
    # Bob himself can.
    assert bob_doc in {d["id"] for d in pv.portal_documents(bob)}


# 2 -------------------------------------------------------------------------
def test_client_cannot_access_another_clients_messages():
    staff_uid = seed_staff_user()
    _, alice, _, _ = seed_portal_account(staff_uid)
    _, bob, bob_pid, bob_hid = seed_portal_account(staff_uid)
    thread = create_thread(bob, household_id=bob_hid, person_id=bob_pid, subject="Bob", body="Private")

    with pytest.raises(PermissionError):
        list_messages(alice, thread)
    with pytest.raises(HTTPException) as ei:
        portal_message_thread_page(thread, fake_request("/x"), alice)
    assert ei.value.status_code == 404                                        # existence hidden


# 3 -------------------------------------------------------------------------
def test_forged_ids_are_rejected():
    staff_uid = seed_staff_user()
    _, alice, alice_pid, alice_hid = seed_portal_account(staff_uid)

    # Forged / nonexistent document id → denied (never a 200, never discloses existence).
    with pytest.raises(HTTPException) as ei:
        api_download_document(fake_request("/x"), 999_000_111, alice)
    assert ei.value.status_code in (403, 404)

    # Forged thread id → 404.
    with pytest.raises(HTTPException) as ei:
        portal_message_thread_page(999_000_222, fake_request("/x"), alice)
    assert ei.value.status_code == 404

    # Forged person/household on a thread create → scope rejection (defense in depth: the browser
    # route never even accepts these from the client, but the service must still refuse).
    with pytest.raises(PermissionError):
        create_thread(alice, household_id=alice_hid, person_id=alice_pid + 987_654, subject="x", body="y")
    with pytest.raises(PermissionError):
        create_thread(alice, household_id=alice_hid + 987_654, person_id=alice_pid, subject="x", body="y")


# 4 -------------------------------------------------------------------------
def test_upload_cannot_be_assigned_to_unauthorized_entities():
    staff_uid = seed_staff_user()
    _, alice, alice_pid, _ = seed_portal_account(staff_uid)
    _, bob, bob_pid, bob_hid = seed_portal_account(staff_uid)

    # (a) An upload always binds to the uploader's own person — there is no param to smuggle.
    doc_id = pv.upload_document(alice, source=io.BytesIO(b"x"), original_filename="a.pdf",
                                display_name="A", category="general")
    with engine.connect() as c:
        assert c.scalar(select(vault_document_links.c.person_id).where(
            vault_document_links.c.document_id == doc_id)) == alice_pid

    # (b) A forged request_id belonging to ANOTHER client is refused, and that request is untouched.
    bob_req = create_document_request(person_id=bob_pid, household_id=bob_hid, title="Bob W-2",
                                      requested_by_user_id=staff_uid)
    with pytest.raises(PermissionError):
        pv.upload_document(alice, source=io.BytesIO(b"x"), original_filename="b.pdf",
                           display_name="B", category="general", request_id=bob_req)
    with engine.connect() as c:
        assert c.scalar(select(portal_document_requests.c.status).where(
            portal_document_requests.c.id == bob_req)) == "open"             # NOT flipped to uploaded


# 5 -------------------------------------------------------------------------
def test_profile_updates_cannot_modify_protected_fields():
    staff_uid = seed_staff_user()
    _, alice, alice_pid, _ = seed_portal_account(staff_uid)
    from app.db import people
    with engine.connect() as c:
        name = c.scalar(select(people.c.full_name).where(people.c.id == alice_pid))
    result = portal_profile.update_profile(alice, {
        "full_name": "Forged Name", "household_id": 1, "id": 42, "active": False,
        "primary_email": "ok@example.com"})
    assert result["changed"] == ["primary_email"]                            # only the allow-listed field
    with engine.connect() as c:
        assert c.scalar(select(people.c.full_name).where(people.c.id == alice_pid)) == name


# 6 -------------------------------------------------------------------------
def test_staff_reply_requires_capability_and_record_scope():
    staff_uid = seed_staff_user()
    _, alice, alice_pid, alice_hid = seed_portal_account(staff_uid)
    thread = create_thread(alice, household_id=alice_hid, person_id=alice_pid, subject="S", body="B")

    # Capability gate: no client.write → the dependency itself rejects with 403.
    without = _staff_principal(seed_staff_user(), {"client.read"})
    with pytest.raises(HTTPException) as ei:
        require_capability("client.write")(principal=without)
    assert ei.value.status_code == 403

    # Record-scope gate: has the capability but no scope on this thread → 403 reply / 404 view.
    unscoped = _staff_principal(seed_staff_user(), {"client.read", "client.write"})
    with pytest.raises(HTTPException) as ei:
        portal_admin_thread(thread, fake_request("/x", state_principal=unscoped), unscoped)
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        portal_admin_thread_reply(thread, request=fake_request("/x", "POST"), body="hi",
                                  internal_note=None, principal=unscoped)
    assert ei.value.status_code == 403


# 7 -------------------------------------------------------------------------
def test_every_mutation_is_audited():
    staff_uid = seed_staff_user()
    account_id, alice, alice_pid, alice_hid = seed_portal_account(staff_uid)

    doc_id = pv.upload_document(alice, source=io.BytesIO(b"x"), original_filename="a.pdf",
                               display_name="A", category="general")
    assert _audits("portal.document.uploaded", doc_id) == 1

    portal_profile.update_profile(alice, {"primary_phone": "540-555-0100"})
    assert _audits("portal.profile.updated", account_id) == 1

    thread = create_thread(alice, household_id=alice_hid, person_id=alice_pid, subject="S", body="B")
    with engine.connect() as c:
        opening = c.scalar(select(func.max(audit_events.c.entity_id)).where(
            audit_events.c.action == "portal.message.sent"))
    assert opening is not None                                               # thread open is audited

    staff = _staff_principal(staff_uid, STAFF_CAPS | {"client.read", "client.write"})
    before = _audits_total("portal.message.sent")
    portal_admin_thread_reply(thread, request=fake_request("/x", "POST"), body="Answer",
                              internal_note=None, principal=staff)
    assert _audits_total("portal.message.sent") == before + 1               # staff reply is audited


def _audits_total(action):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == action))


# 8 -------------------------------------------------------------------------
def test_logout_invalidates_the_session_server_side():
    account_id, _, _, _ = seed_portal_account(seed_staff_user())
    token = create_portal_session(account_id, device_fingerprint="sec-logout")
    assert resolve_portal_session(token) is not None
    resp = portal_logout_browser(fake_request("/portal/logout", "POST",
                                              session={"portal_session_token": token}))
    assert resp.status_code == 303 and resp.headers["location"] == "/portal/login"
    assert resolve_portal_session(token) is None                            # revoked, not just cookie-cleared


# Also verify a client reply cannot leak into a thread they don't own (mutation-scope).
def test_reply_into_foreign_thread_is_blocked():
    staff_uid = seed_staff_user()
    _, alice, _, _ = seed_portal_account(staff_uid)
    _, bob, bob_pid, bob_hid = seed_portal_account(staff_uid)
    bob_thread = create_thread(bob, household_id=bob_hid, person_id=bob_pid, subject="B", body="B")
    with pytest.raises(PermissionError):
        send_message(alice, bob_thread, "sneak")
    with pytest.raises(HTTPException) as ei:
        portal_message_reply(bob_thread, request=fake_request("/x", "POST"), body="sneak", principal=alice)
    assert ei.value.status_code == 404
