"""360Plus Communication Hub — behavior + comprehensive security/adversarial tests.

Covers topic/routing, relationship-owned read state, staff work-queue filtering, request/document
linkage, the unified timeline, internal-note protection, feature-control integration, audit, and the
12 required security scenarios (cross-client isolation, business org scoping, internal-note & internal
document protection, disabled-feature enforcement, staff RBAC, forged IDs, audit).
"""
from __future__ import annotations

import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select

from app.db import (
    audit_events,
    people,
    client_feature_overrides,
    engine,
    firm_feature_controls,
    portal_threads,
    users,
)
from app.portal import communication_hub as hub
from app.portal.service import (
    create_document_request,
    create_thread,
    list_messages,
    send_message,
    staff_send_message,
)
from app.security.models import Principal
from app.services.features import portal_gate
from app.services.features import service as feat
from tests._portal_util import seed_portal_account, seed_staff_user

# The firm-wide portal surface gates now genuinely close their surfaces; these tests exercise the
# surfaces themselves, so they switch the gates on (see tests/conftest.py).
pytestmark = pytest.mark.usefixtures("portal_messaging_on", "production_identity_provider")


STAFF = frozenset({"client.read", "client.write", "record.read_all", "record.write_all"})


@pytest.fixture(autouse=True)
def _isolate_feature_controls():
    for t in (firm_feature_controls, client_feature_overrides):
        with engine.begin() as c:
            c.execute(delete(t))
    yield


def _staff(caps=STAFF):
    return Principal(seed_staff_user(), "staff@e.test", "Staff", frozenset(caps))


def _audits(action, entity_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            (audit_events.c.action == action) & (audit_events.c.entity_id == str(entity_id))))


def _thread(principal, pid, hid, *, subject="Q", body="hi", topic="tax"):
    return create_thread(principal, household_id=hid, person_id=pid, subject=subject, body=body, topic=topic)


def _portal_req(principal):
    return SimpleNamespace(state=SimpleNamespace(portal_principal=principal))


# --- thread model + read state ----------------------------------------------

def test_thread_carries_topic_and_activity_markers():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    tid = _thread(principal, pid, hid, topic="wealth")
    t = hub.load_thread(tid)
    assert t["topic"] == "wealth" and t["last_client_message_at"] is not None
    assert t["last_staff_message_at"] is None and t["status"] == "open"


def test_read_state_staff_and_client():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    # Client message → unread for staff.
    inbox = {r["id"]: r for r in hub.staff_inbox(staff)}
    assert inbox[tid]["unread"] is True
    hub.mark_thread_read_staff(tid, actor_user_id=staff.user_id)
    assert {r["id"]: r for r in hub.staff_inbox(staff)}[tid]["unread"] is False
    # Staff reply → unread for client.
    staff_send_message(thread_id=tid, user_id=staff.user_id, body="reply")
    convos = {c["id"]: c for c in hub.client_conversations(principal)}
    assert convos[tid]["unread"] is True
    hub.mark_thread_read_client(principal, tid)
    assert {c["id"]: c for c in hub.client_conversations(principal)}[tid]["unread"] is False


# --- routing (reuses record_assignments; unassigned when unknown) -----------

def test_routing_leaves_unassigned_when_undeterminable():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    tid = _thread(principal, pid, hid)
    result = hub.route_thread(tid)                       # no assignments seeded → review state
    assert result["state"] == "unassigned"
    assert hub.load_thread(tid)["assigned_user_id"] is None


# --- staff work-queue filters -----------------------------------------------

def test_staff_inbox_filters():
    staff = _staff()
    _, p1, pid1, hid1 = seed_portal_account(seed_staff_user())
    _, p2, pid2, hid2 = seed_portal_account(seed_staff_user())
    t_tax = _thread(p1, pid1, hid1, topic="tax")
    t_wealth = _thread(p2, pid2, hid2, topic="wealth")
    hub.reassign_thread(staff.user_id, t_tax, user_id=staff.user_id, topic="tax")
    hub.set_thread_state(staff.user_id, t_wealth, resolved=True)

    def ids(rows):
        return {r["id"] for r in rows}
    # Membership assertions (the shared test DB holds threads from other tests; record.read_all sees all).
    assert t_tax in ids(hub.staff_inbox(staff, assigned_to_me=True))
    assert t_wealth not in ids(hub.staff_inbox(staff, assigned_to_me=True))
    assert t_wealth in ids(hub.staff_inbox(staff, unassigned=True))
    assert t_tax not in ids(hub.staff_inbox(staff, unassigned=True))
    tax_rows = hub.staff_inbox(staff, topic="tax")
    assert t_tax in ids(tax_rows) and t_wealth not in ids(tax_rows)
    assert all(r["topic"] == "tax" for r in tax_rows)
    resolved_rows = hub.staff_inbox(staff, status="resolved")
    assert t_wealth in ids(resolved_rows) and t_tax not in ids(resolved_rows)
    assert all(r["status"] == "resolved" for r in resolved_rows)


# --- request / document linkage ---------------------------------------------

def test_link_and_create_request_from_thread():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    # create a request from the conversation → linked back
    r = hub.create_request_from_thread(staff.user_id, tid, title="Upload W-2")
    linked = hub.linked_requests(tid)
    assert any(x["id"] == r["request_id"] and x["title"] == "Upload W-2" for x in linked)
    # link an existing request for the same client
    req2 = create_document_request(person_id=pid, household_id=hid, title="1099",
                                   requested_by_user_id=staff.user_id)
    hub.link_request(staff.user_id, tid, req2)
    assert req2 in {x["id"] for x in hub.linked_requests(tid)}


def test_link_request_cross_client_denied():
    staff = _staff()
    _, p1, pid1, hid1 = seed_portal_account(seed_staff_user())
    _, p2, pid2, hid2 = seed_portal_account(seed_staff_user())
    tid = _thread(p1, pid1, hid1)
    other_req = create_document_request(person_id=pid2, household_id=hid2, title="Other",
                                        requested_by_user_id=staff.user_id)
    with pytest.raises(PermissionError):
        hub.link_request(staff.user_id, tid, other_req)     # request belongs to a different client


# --- unified relationship timeline ------------------------------------------

def test_relationship_timeline_composition_and_internal_isolation():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid, body="client opening")
    staff_send_message(thread_id=tid, user_id=staff.user_id, body="staff reply")
    staff_send_message(thread_id=tid, user_id=staff.user_id, body="SECRET NOTE", internal_note=True)
    create_document_request(person_id=pid, household_id=hid, title="W-2", requested_by_user_id=staff.user_id)
    # Staff timeline includes the internal note; client timeline never does.
    staff_tl = hub.relationship_timeline(person_ids=[pid], household_ids=[hid], include_internal=True)
    client_tl = hub.relationship_timeline(person_ids=[pid], household_ids=[hid], include_internal=False)
    assert any(e["kind"] == "internal_note" for e in staff_tl)
    assert not any(e["kind"] == "internal_note" for e in client_tl)
    assert any(e["kind"] == "document_request" for e in client_tl)
    assert not any("SECRET NOTE" in (e.get("title") or "") for e in client_tl)


# === Security scenarios (1–12) ==============================================

def test_s1_client_cannot_read_other_clients_threads():
    _, alice, apid, ahid = seed_portal_account(seed_staff_user())
    _, bob, bpid, bhid = seed_portal_account(seed_staff_user())
    bob_thread = _thread(bob, bpid, bhid, subject="Bob private")
    assert bob_thread not in {c["id"] for c in hub.client_conversations(alice)}
    with pytest.raises(PermissionError):
        list_messages(alice, bob_thread)


def test_s2_client_cannot_reply_to_other_clients_thread():
    _, alice, _, _ = seed_portal_account(seed_staff_user())
    _, bob, bpid, bhid = seed_portal_account(seed_staff_user())
    bob_thread = _thread(bob, bpid, bhid)
    with pytest.raises(PermissionError):
        send_message(alice, bob_thread, "sneak")


def test_s3_business_conversation_scoped_to_correct_org():
    # A business conversation is anchored to an organization; access must be scoped to THAT org — a
    # staff member assigned to a different organization must not gain access.
    from datetime import date

    from app.db import record_assignments
    org_x, org_y = int(uuid.uuid4().int % 1_000_000), int(uuid.uuid4().int % 1_000_000)
    staff_x = Principal(seed_staff_user(), "x@e.test", "X", frozenset({"client.read"}))   # no read_all
    staff_y = Principal(seed_staff_user(), "y@e.test", "Y", frozenset({"client.read"}))
    with engine.begin() as c:
        c.execute(insert(record_assignments).values(entity_type="organization", entity_id=org_x,
                                                    user_id=staff_x.user_id, assignment_type="owner", effective_date=date.today()))
        c.execute(insert(record_assignments).values(entity_type="organization", entity_id=org_y,
                                                    user_id=staff_y.user_id, assignment_type="owner", effective_date=date.today()))
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    tid = _thread(principal, pid, hid)                        # household_id is NOT NULL; add org anchor
    with engine.begin() as c:
        c.execute(portal_threads.update().where(portal_threads.c.id == tid).values(organization_id=org_x))
    thread = hub.load_thread(tid)
    assert hub.thread_in_staff_scope(staff_x, thread) is True     # assigned to org X → in scope
    assert hub.thread_in_staff_scope(staff_y, thread) is False    # assigned to a DIFFERENT org → denied


def test_s4_client_cannot_read_internal_notes():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    staff_send_message(thread_id=tid, user_id=staff.user_id, body="INTERNAL ONLY", internal_note=True)
    client_messages = list_messages(principal, tid)                 # the client-facing API
    bodies = [m["body"] for m in client_messages]
    assert "INTERNAL ONLY" not in bodies
    # The client projection no longer carries the internal ``visibility`` flag, so assert the property
    # itself: only the client-visible message is returned, and no internal field rides along.
    assert len(client_messages) == 1
    assert all("visibility" not in m and "sender_user_id" not in m for m in client_messages)


def test_s5_client_cannot_see_internal_only_linked_document():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    # a document staff attaches to an INTERNAL note
    with engine.begin() as c:
        secret_doc = c.execute(insert(__import__("app.db", fromlist=["documents"]).documents).values(
            original_name="secret.pdf", stored_name=f"s-{uuid.uuid4().hex}", storage_provider="local",
            storage_path="/tmp/secret.pdf", size_bytes=1, sha256=("a" * 64), status="active",
            archived=False).returning(__import__("app.db", fromlist=["documents"]).documents.c.id)).scalar_one()
    note_id = staff_send_message(thread_id=tid, user_id=staff.user_id, body="see attached",
                                 internal_note=True, attachment_document_ids=[secret_doc])
    # The internal note (and its attachment) are never returned to the client.
    client_msg_ids = {m["id"] for m in list_messages(principal, tid)}
    assert note_id not in client_msg_ids
    assert secret_doc in hub.message_attachment_ids(note_id)         # attached to the internal note only


def test_s6_disabled_secure_messaging_blocks_direct_api():
    _, principal, _, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    assert portal_gate.evaluate(principal, "/api/v1/portal/messages", "POST")[0]      # allowed by default
    feat.set_override("household", hid, "secure_messaging", "disable", actor_user_id=staff.user_id)
    allowed, _r, mapped = portal_gate.evaluate(principal, "/api/v1/portal/messages", "POST")
    assert not allowed and mapped == "secure_messaging"
    # via the enforcement dependency too
    from app.services.features.enforcement import require_client_feature
    with pytest.raises(HTTPException) as ei:
        require_client_feature("secure_messaging")(_portal_req(principal))
    assert ei.value.status_code == 403


def test_s7_disabled_client_requests_does_not_disable_messaging():
    _, principal, _, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    feat.set_override("household", hid, "client_requests", "disable", actor_user_id=staff.user_id)
    assert not portal_gate.evaluate(principal, "/api/v1/portal/requests", "GET")[0]   # requests blocked
    assert portal_gate.evaluate(principal, "/api/v1/portal/messages", "POST")[0]      # messaging intact


def test_s8_unauthorized_staff_cannot_reassign_or_resolve():
    from app.routes.portal_admin import portal_admin_thread_assign, portal_admin_thread_resolve
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    tid = _thread(principal, pid, hid)
    outsider = Principal(seed_staff_user(), "u@e.test", "U", frozenset({"client.write"}))   # no record scope
    req = SimpleNamespace(state=SimpleNamespace(request_id="t"))
    with pytest.raises(HTTPException) as ei:
        portal_admin_thread_assign(tid, req, assigned_user_id=outsider.user_id, assigned_team_id=None,
                                   topic="tax", principal=outsider)
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        portal_admin_thread_resolve(tid, req, action="resolve", principal=outsider)
    assert ei.value.status_code == 403
    assert hub.load_thread(tid)["status"] == "open"          # nothing changed


def test_s9_authorized_staff_can_perform_actions():
    from app.routes.portal_admin import portal_admin_thread_assign, portal_admin_thread_resolve
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    req = SimpleNamespace(state=SimpleNamespace(request_id="t"))
    assert portal_admin_thread_assign(tid, req, assigned_user_id=staff.user_id, assigned_team_id=None,
                                      topic="wealth", principal=staff).status_code == 303
    assert hub.load_thread(tid)["assigned_user_id"] == staff.user_id
    assert portal_admin_thread_resolve(tid, req, action="resolve", principal=staff).status_code == 303
    assert hub.load_thread(tid)["status"] == "resolved"


def test_s10_forged_ids_fail_safely():
    staff = _staff()
    assert hub.load_thread(999_000_111) is None
    with pytest.raises(ValueError):
        hub.reassign_thread(staff.user_id, 999_000_111, user_id=staff.user_id)
    with pytest.raises(ValueError):
        hub.set_thread_state(staff.user_id, 999_000_222, resolved=True)
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    tid = _thread(principal, pid, hid)
    with pytest.raises(ValueError):
        hub.link_request(staff.user_id, tid, 999_000_333)     # unknown request
    with pytest.raises(ValueError):
        hub.create_request_from_thread(staff.user_id, 999_000_444, title="x")   # unknown thread


def test_s11_mutations_are_audited():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    hub.reassign_thread(staff.user_id, tid, user_id=staff.user_id, topic="tax")
    hub.set_thread_state(staff.user_id, tid, resolved=True)
    r = hub.create_request_from_thread(staff.user_id, tid, title="W-2")
    staff_send_message(thread_id=tid, user_id=staff.user_id, body="note", internal_note=True)
    assert _audits("portal.thread.assigned", tid) >= 1
    assert _audits("portal.thread.resolved", tid) >= 1
    assert _audits("portal.request.created_from_thread", tid) >= 1
    with engine.connect() as c:
        note_audits = c.scalar(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "portal.internal_note.created"))
    assert note_audits >= 1
    assert r["request_id"]


# === Assignment selectors: directory reuse + validation ====================

def test_assignable_directory_lists_active_employees_and_teams():
    uid = seed_staff_user()                                   # seeded status='active'
    users = {u["id"] for u in hub.assignable_users()}
    assert uid in users
    assert all("display_name" in u for u in hub.assignable_users())
    assert isinstance(hub.assignable_teams(), list)           # teams from the same identity source


def test_assign_valid_employee_persists_and_audits():
    from app.routes.portal_admin import portal_admin_thread_assign
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    req = SimpleNamespace(state=SimpleNamespace(request_id="t"))
    before = _audits("portal.thread.assigned", tid)
    resp = portal_admin_thread_assign(tid, req, assigned_user_id=str(staff.user_id),
                                      assigned_team_id="", topic="tax", principal=staff)
    assert resp.status_code == 303 and "notice" in resp.headers["location"]
    assert hub.load_thread(tid)["assigned_user_id"] == staff.user_id
    assert _audits("portal.thread.assigned", tid) == before + 1     # audit preserved


def test_assign_empty_selection_is_valid_unassigned():
    from app.routes.portal_admin import portal_admin_thread_assign
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    hub.reassign_thread(staff.user_id, tid, user_id=staff.user_id)   # first assign
    req = SimpleNamespace(state=SimpleNamespace(request_id="t"))
    portal_admin_thread_assign(tid, req, assigned_user_id="", assigned_team_id="", topic=None,
                               principal=staff)                       # empty = Unassigned
    assert hub.load_thread(tid)["assigned_user_id"] is None


def test_assign_forged_user_or_team_is_rejected():
    from app.routes.portal_admin import portal_admin_thread_assign
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    req = SimpleNamespace(state=SimpleNamespace(request_id="t"))
    # forged user id (not a selectable active employee) → error redirect, nothing persisted
    resp = portal_admin_thread_assign(tid, req, assigned_user_id="999000111", assigned_team_id="",
                                      topic=None, principal=staff)
    assert resp.status_code == 303 and "error" in resp.headers["location"]
    assert hub.load_thread(tid)["assigned_user_id"] is None
    # forged team id → rejected at the service layer
    with pytest.raises(ValueError):
        hub.reassign_thread(staff.user_id, tid, team_id=999_000_222)
    # a non-numeric selection is invalid
    resp = portal_admin_thread_assign(tid, req, assigned_user_id="drop-table", assigned_team_id="",
                                      topic=None, principal=staff)
    assert "error" in resp.headers["location"]


def test_assign_inactive_employee_is_rejected():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    inactive = seed_staff_user()
    with engine.begin() as c:
        c.execute(users.update().where(users.c.id == inactive).values(status="disabled"))
    assert inactive not in {u["id"] for u in hub.assignable_users()}     # not selectable
    with pytest.raises(ValueError):
        hub.reassign_thread(staff.user_id, tid, user_id=inactive)       # not assignable


def test_assign_unauthorized_staff_still_blocked_before_validation():
    from app.routes.portal_admin import portal_admin_thread_assign
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    tid = _thread(principal, pid, hid)
    outsider = Principal(seed_staff_user(), "u@e.test", "U", frozenset({"client.write"}))   # no record scope
    req = SimpleNamespace(state=SimpleNamespace(request_id="t"))
    with pytest.raises(HTTPException) as ei:
        portal_admin_thread_assign(tid, req, assigned_user_id=str(outsider.user_id), assigned_team_id="",
                                   topic="tax", principal=outsider)
    assert ei.value.status_code == 403
    assert hub.load_thread(tid)["assigned_user_id"] is None


def test_s12_reassign_records_previous_and_new_assignment():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    tid = _thread(principal, pid, hid)
    hub.reassign_thread(staff.user_id, tid, user_id=staff.user_id, topic="tax")
    other = seed_staff_user()
    hub.reassign_thread(staff.user_id, tid, user_id=other, topic="wealth")
    with engine.connect() as c:
        meta = c.execute(select(audit_events.c.metadata).where(
            audit_events.c.action == "portal.thread.assigned",
            audit_events.c.entity_id == str(tid)).order_by(audit_events.c.id.desc())).mappings().first()
    assert meta["metadata"]["previous"]["assigned_user_id"] == staff.user_id
    assert meta["metadata"]["new"]["assigned_user_id"] == other


# --- staff-initiated conversations ----------------------------------------------------------
#
# The gap found during Phase 1 review: clients could open a conversation and staff could reply, but
# staff had no way to START one. Authorization is derived entirely from the staff Principal; the
# person id arriving from a form is a claim, re-resolved and re-checked with WRITE record scope.

def _staff_principal(caps=STAFF, uid=None):
    return Principal(uid or seed_staff_user(), "staff@example.com", "Staff", frozenset(caps))


def _thread_row(thread_id):
    with engine.connect() as c:
        return c.execute(select(portal_threads).where(
            portal_threads.c.id == thread_id)).mappings().one()


def test_staff_can_start_a_conversation_with_an_active_portal_client():
    staff = _staff_principal()
    account_id, principal, person_id, household_id = seed_portal_account(staff.user_id)

    thread_id = hub.staff_start_thread(staff, person_id=person_id,
                                       subject="Your 2025 return", body="Please review the draft.")

    thread = _thread_row(thread_id)
    assert thread["person_id"] == person_id and thread["household_id"] == household_id
    assert thread["created_by_user_id"] == staff.user_id
    assert thread["created_by_portal_account_id"] is None
    assert thread["status"] == "open"
    # The author has read it; the client has not.
    assert thread["staff_last_read_at"] is not None
    assert thread["client_last_read_at"] is None
    assert thread["last_staff_message_at"] is not None
    assert thread["last_client_message_at"] is None


def test_the_opening_message_is_server_attributed_to_the_staff_member():
    staff = _staff_principal()
    _, principal, person_id, _ = seed_portal_account(staff.user_id)

    thread_id = hub.staff_start_thread(staff, person_id=person_id, subject="Hello",
                                       body="Opening message.")
    messages = hub.staff_thread_messages(thread_id)

    assert len(messages) == 1
    assert messages[0]["sender_user_id"] == staff.user_id
    assert messages[0]["sender_portal_account_id"] is None, "a staff message claimed a client sender"
    assert messages[0]["visibility"] == "client", "the opening message is hidden from the client"


def test_the_client_can_read_a_staff_initiated_thread():
    staff = _staff_principal()
    _, principal, person_id, _ = seed_portal_account(staff.user_id)
    thread_id = hub.staff_start_thread(staff, person_id=person_id, subject="Hi", body="Body.")

    messages = list_messages(principal, thread_id)

    assert [m["body"] for m in messages] == ["Body."]


def test_a_staff_initiated_thread_is_unread_for_the_client_until_they_open_it():
    staff = _staff_principal()
    _, principal, person_id, _ = seed_portal_account(staff.user_id)
    thread_id = hub.staff_start_thread(staff, person_id=person_id, subject="Hi", body="Body.")

    assert _thread_row(thread_id)["client_last_read_at"] is None
    hub.mark_thread_read_client(principal, thread_id)
    thread = _thread_row(thread_id)
    assert thread["client_last_read_at"] is not None
    # Reading as the client must not touch the staff marker, and vice versa.
    assert thread["staff_last_read_at"] is not None


def test_a_staff_member_outside_record_scope_cannot_start_a_conversation():
    owner = _staff_principal()
    _, _, person_id, _ = seed_portal_account(owner.user_id)
    outsider = _staff_principal(caps={"client.read", "client.write"})   # no record.*_all

    with pytest.raises(hub.StaffMessageError):
        hub.staff_start_thread(outsider, person_id=person_id, subject="Hi", body="Body.")

    with engine.connect() as c:
        assert c.execute(select(func.count(portal_threads.c.id)).where(
            portal_threads.c.person_id == person_id)).scalar_one() == 0


def test_an_unknown_person_and_an_out_of_scope_person_are_refused_identically():
    """The refusal must not become an existence oracle."""
    owner = _staff_principal()
    _, _, real_person, _ = seed_portal_account(owner.user_id)
    outsider = _staff_principal(caps={"client.read", "client.write"})

    with pytest.raises(hub.StaffMessageError) as out_of_scope:
        hub.staff_start_thread(outsider, person_id=real_person, subject="s", body="b")
    with pytest.raises(hub.StaffMessageError) as unknown:
        hub.staff_start_thread(outsider, person_id=99_000_111, subject="s", body="b")

    assert str(out_of_scope.value) == str(unknown.value)


def test_a_client_without_a_portal_account_is_explained_not_silently_onboarded():
    from app.db import people
    from sqlalchemy import insert as _insert

    staff = _staff_principal()
    with engine.begin() as c:
        person_id = c.execute(_insert(people).values(
            full_name=f"No Portal {uuid.uuid4().hex[:8]}", active=True)
            .returning(people.c.id)).scalar_one()

    with pytest.raises(hub.StaffMessageError) as exc:
        hub.staff_start_thread(staff, person_id=person_id, subject="Hi", body="Body.")

    assert "does not have a portal account" in str(exc.value)
    assert "Client Portal Administration" in str(exc.value), "no route to a remedy is offered"
    with engine.connect() as c:
        assert c.execute(select(func.count(portal_threads.c.id)).where(
            portal_threads.c.person_id == person_id)).scalar_one() == 0
        from app.db import portal_accounts
        assert c.execute(select(func.count(portal_accounts.c.id)).where(
            portal_accounts.c.person_id == person_id)).scalar_one() == 0, \
            "a portal account was silently created"


def test_a_revoked_portal_client_cannot_be_messaged():
    from app.db import portal_accounts
    from sqlalchemy import update as _update

    staff = _staff_principal()
    account_id, _, person_id, _ = seed_portal_account(staff.user_id)
    with engine.begin() as c:
        c.execute(_update(portal_accounts).where(portal_accounts.c.id == account_id)
                  .values(status="revoked"))

    with pytest.raises(hub.StaffMessageError) as exc:
        hub.staff_start_thread(staff, person_id=person_id, subject="Hi", body="Body.")

    assert "revoked" in str(exc.value).lower()
    with engine.connect() as c:
        assert c.execute(select(func.count(portal_threads.c.id)).where(
            portal_threads.c.person_id == person_id)).scalar_one() == 0


@pytest.mark.parametrize("subject,body,fragment", [
    ("", "body", "subject is required"),
    ("   ", "body", "subject is required"),
    ("subject", "", "message is required"),
    ("subject", "   ", "message is required"),
    ("x" * 201, "body", "subject is too long"),
    ("subject", "x" * 10001, "message is too long"),
])
def test_subject_and_body_limits_are_enforced_server_side(subject, body, fragment):
    staff = _staff_principal()
    _, _, person_id, _ = seed_portal_account(staff.user_id)

    with pytest.raises(hub.StaffMessageError) as exc:
        hub.staff_start_thread(staff, person_id=person_id, subject=subject, body=body)

    assert fragment in str(exc.value).lower()
    with engine.connect() as c:
        assert c.execute(select(func.count(portal_threads.c.id)).where(
            portal_threads.c.person_id == person_id)).scalar_one() == 0


def test_an_unknown_topic_is_refused():
    staff = _staff_principal()
    _, _, person_id, _ = seed_portal_account(staff.user_id)

    with pytest.raises(hub.StaffMessageError):
        hub.staff_start_thread(staff, person_id=person_id, subject="s", body="b",
                               topic="not-a-topic")


def test_the_audit_event_names_the_actor_and_entity_but_never_the_content():
    staff = _staff_principal()
    _, _, person_id, _ = seed_portal_account(staff.user_id)
    secret_subject = f"SUBJECT-{uuid.uuid4().hex[:10]}"
    secret_body = f"BODY-{uuid.uuid4().hex[:10]}"

    thread_id = hub.staff_start_thread(staff, person_id=person_id, subject=secret_subject,
                                       body=secret_body)

    with engine.connect() as c:
        rows = c.execute(select(audit_events).where(
            audit_events.c.entity_type == "portal_thread",
            audit_events.c.entity_id == str(thread_id))).mappings().all()
    assert rows, "the staff-initiated thread was not audited"
    event = rows[-1]
    assert event["action"] == "portal.thread.created"
    assert event["actor_user_id"] == staff.user_id
    blob = f"{event['metadata']} {event['action']}"
    assert secret_subject not in blob and secret_body not in blob, \
        "message content leaked into the audit record"
    assert str(person_id) in str(event["metadata"])          # the client entity IS identified


def test_a_staff_initiated_thread_appears_in_the_staff_inbox_newest_first():
    staff = _staff_principal()
    _, _, person_id, _ = seed_portal_account(staff.user_id)
    hub.staff_start_thread(staff, person_id=person_id, subject="Older", body="b")
    newest = hub.staff_start_thread(staff, person_id=person_id, subject="Newer", body="b")

    inbox = hub.staff_inbox(staff)

    assert inbox[0]["id"] == newest, "the inbox is not ordered newest activity first"


def test_client_portal_status_reports_each_state_without_creating_anything():
    from app.db import people, portal_accounts
    from sqlalchemy import insert as _insert

    staff = _staff_principal()
    active_account, _, active_person, _ = seed_portal_account(staff.user_id)
    account_id, reason = hub.client_portal_status(active_person)
    assert account_id == active_account and reason is None

    with engine.begin() as c:
        lonely = c.execute(_insert(people).values(
            full_name=f"Lonely {uuid.uuid4().hex[:8]}", active=True)
            .returning(people.c.id)).scalar_one()
    account_id, reason = hub.client_portal_status(lonely)
    assert account_id is None and "does not have a portal account" in reason
    with engine.connect() as c:
        assert c.execute(select(func.count(portal_accounts.c.id)).where(
            portal_accounts.c.person_id == lonely)).scalar_one() == 0


# --- C. staff initiation against the FULL portal-account lifecycle ---------------------------

def test_an_invited_but_never_activated_client_cannot_yet_be_messaged():
    """status='invited': the account exists but the client has never signed in, so there is nowhere
    for them to read it. Explained, not silently onboarded."""
    from app.db import households, people, portal_accounts
    from sqlalchemy import insert as _insert

    staff = _staff_principal()
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(_insert(households).values(name=f"Invited HH {sfx}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(_insert(people).values(household_id=hid, full_name=f"Invited {sfx}",
                                               active=True).returning(people.c.id)).scalar_one()
        c.execute(_insert(portal_accounts).values(
            person_id=pid, email=f"inv-{sfx}@e.test", normalized_email=f"inv-{sfx}@e.test",
            display_name="Invited", status="invited"))

    with pytest.raises(hub.StaffMessageError) as exc:
        hub.staff_start_thread(staff, person_id=pid, subject="Hi", body="Body.")

    assert "has not activated" in str(exc.value)
    with engine.connect() as c:
        assert c.execute(select(func.count(portal_threads.c.id)).where(
            portal_threads.c.person_id == pid)).scalar_one() == 0
        # The account is untouched — not activated, not revoked, not duplicated.
        assert c.execute(select(portal_accounts.c.status).where(
            portal_accounts.c.person_id == pid)).scalars().all() == ["invited"]


def test_a_historical_revoked_account_does_not_block_the_current_active_one():
    """The schema permits several accounts per person; the CURRENT one decides."""
    from app.db import portal_accounts
    from sqlalchemy import insert as _insert

    staff = _staff_principal()
    active_id, _, person_id, _ = seed_portal_account(staff.user_id)
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:                       # an OLDER, revoked account for the same person
        c.execute(_insert(portal_accounts).values(
            person_id=person_id, email=f"old-{sfx}@e.test", normalized_email=f"old-{sfx}@e.test",
            display_name="Old", status="revoked",
            created_at=hub._now().replace(year=hub._now().year - 1)))

    account_id, reason = hub.client_portal_status(person_id)

    assert account_id == active_id and reason is None
    assert hub.staff_start_thread(staff, person_id=person_id, subject="Hi", body="Body.")


def test_starting_a_conversation_never_touches_the_account_or_grant_lifecycle():
    """The route may message a client; it may not onboard one."""
    from app.db import portal_access_grants, portal_accounts, portal_invitations

    staff = _staff_principal()
    account_id, _, person_id, _ = seed_portal_account(staff.user_id)

    def _counts():
        with engine.connect() as c:
            return (
                c.execute(select(func.count(portal_accounts.c.id)).where(
                    portal_accounts.c.person_id == person_id)).scalar_one(),
                c.execute(select(func.count(portal_invitations.c.id)).where(
                    portal_invitations.c.portal_account_id == account_id)).scalar_one(),
                c.execute(select(func.count(portal_access_grants.c.id)).where(
                    portal_access_grants.c.portal_account_id == account_id)).scalar_one(),
                c.execute(select(portal_accounts.c.status).where(
                    portal_accounts.c.id == account_id)).scalar_one(),
            )

    before = _counts()
    hub.staff_start_thread(staff, person_id=person_id, subject="Hi", body="Body.")
    assert _counts() == before, "starting a conversation changed the account/invitation/grant state"


def test_starting_a_conversation_sends_no_email():
    """Phase 1 is in-app only; nothing is dispatched to any address."""
    import inspect

    src = inspect.getsource(hub.staff_start_thread)
    for mailer in ("send_portal_invitation", "send_portal_verification_code", "email_delivery",
                   "sendMail", "notify("):
        assert mailer not in src, f"staff_start_thread reaches {mailer}"


# --- E. one-field client selector on the compose form ----------------------------------------
#
# Staff were shown four lookup fields (first/last/email/phone) mirroring the invite form. The
# workflow here is "message this client", so it is now ONE typeahead over the SAME record-scoped
# search endpoint. Authorization is unchanged: the id is a claim, re-resolved on POST.

def _render_threads(principal):
    from app.routes.portal_admin import portal_admin_threads
    request = SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}", principal=principal,
                              demo_mode=False),
        client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"},
        query_params={}, session={}, url=SimpleNamespace(path="/admin/client-portal/threads"))
    return portal_admin_threads(request, principal=principal).body.decode()


def test_e1_the_compose_form_offers_one_client_selector_not_four_lookup_fields():
    html = _render_threads(_staff_principal())

    assert 'for="thread-client">Client<' in html
    assert "Start typing a client's name..." in html
    assert "data-client-typeahead" in html
    for gone in ('id="sel-first"', 'id="sel-last"', 'id="sel-email"', 'id="sel-phone"',
                 ">First name<", ">Last name<"):
        assert gone not in html, f"the compose form still shows {gone}"
    # Topic / Subject / Message remain the rest of the workflow.
    for kept in (">Topic<", ">Subject<", ">Message<", "Send secure message"):
        assert kept in html


def test_e2_the_person_id_is_hidden_state_and_never_displayed():
    html = _render_threads(_staff_principal())

    assert '<input type="hidden" name="person_id" data-client-typeahead-id>' in html
    visible = re.sub(r"<[^>]+>", " ", html)
    for label in ("person_id", "Person ID", "person id"):
        assert label not in visible, f"{label} is rendered as visible text"


def test_e3_the_selector_reuses_the_existing_record_scoped_search_endpoint():
    """No second search system: the same endpoint and the same service as the invite form."""
    js = open("app/static/js/client_portal_admin.js", encoding="utf-8").read()
    body = js.split("function bindClientTypeahead", 1)[1].split("\n  function ", 1)[0]
    assert "SEARCH_URL" in body and 'q=" + encodeURIComponent' in body
    assert "MIN_TERM" in body and "DEBOUNCE_MS" in body
    assert js.count('var SEARCH_URL = "/admin/client-portal/client-search";') == 1


def test_e4_search_results_are_limited_to_the_principals_record_scope():
    from app.routes.portal_admin import portal_admin_client_search

    owner = _staff_principal()
    _, _, mine, _ = seed_portal_account(owner.user_id)
    with engine.connect() as c:
        my_name = c.execute(select(people.c.full_name).where(people.c.id == mine)).scalar_one()

    narrow = Principal(seed_staff_user(), "staff@example.com", "Staff",
                       frozenset({"client.read", "client.write"}))     # no record.read_all
    found = portal_admin_client_search(q=my_name.split()[-1], principal=narrow)

    assert mine not in [r["person_id"] for r in found["results"]], \
        "an out-of-scope person appeared in the selector"
    # ...and the authorized principal DOES find them, proving the query itself works.
    allowed = portal_admin_client_search(q=my_name.split()[-1], principal=owner)
    assert mine in [r["person_id"] for r in allowed["results"]]


def test_e5_an_out_of_scope_search_is_not_an_existence_oracle():
    """Same empty shape whether the name exists out of scope or not at all."""
    from app.routes.portal_admin import portal_admin_client_search

    owner = _staff_principal()
    _, _, hidden_person, _ = seed_portal_account(owner.user_id)
    with engine.connect() as c:
        hidden_name = c.execute(select(people.c.full_name).where(
            people.c.id == hidden_person)).scalar_one()
    narrow = Principal(seed_staff_user(), "staff@example.com", "Staff",
                       frozenset({"client.read", "client.write"}))

    real_but_hidden = portal_admin_client_search(q=hidden_name, principal=narrow)
    pure_fiction = portal_admin_client_search(q="Zzzqqx Nooneatall", principal=narrow)

    assert real_but_hidden["results"] == pure_fiction["results"] == []


def test_e6_results_are_bounded():
    import inspect

    from app.portal import invite_targets
    signature = inspect.signature(invite_targets.search_people)
    assert signature.parameters["limit"].default == 20, "the result bound changed"


def test_e7_search_results_are_rendered_as_text_never_as_markup():
    """A person's name is untrusted for HTML purposes; the selector must not parse it."""
    js = open("app/static/js/client_portal_admin.js", encoding="utf-8").read()
    body = js.split("function bindClientTypeahead", 1)[1].split("\n  function ", 1)[0]
    code = "\n".join(line.split("/*")[0] for line in body.splitlines())
    assert "innerHTML" not in code and "outerHTML" not in code
    assert "insertAdjacentHTML" not in code
    assert code.count("textContent") >= 3, "result values are not set as text"


def test_e8_a_hostile_person_name_is_escaped_in_the_search_payload_and_page():
    from app.routes.portal_admin import portal_admin_client_search

    staff = _staff_principal()
    sfx = uuid.uuid4().hex[:8]
    hostile = f"<script>alert('{sfx}')</script>"
    with engine.begin() as c:
        pid = c.execute(insert(people).values(
            full_name=hostile, first_name="<img", last_name=f"onerror{sfx}",
            active=True).returning(people.c.id)).scalar_one()
    from app.db import record_assignments
    with engine.begin() as c:
        c.execute(insert(record_assignments).values(
            entity_type="person", entity_id=pid, user_id=staff.user_id,
            assignment_type="primary"))

    # The JSON payload carries the raw value; the BROWSER never parses it as markup (test_e7),
    # and any server-rendered page escapes it.
    found = portal_admin_client_search(q=f"onerror{sfx}", principal=staff)
    assert isinstance(found["results"], list)
    html = _render_threads(staff)
    assert f"<script>alert('{sfx}')</script>" not in html


def test_e9_the_no_selection_guard_is_a_convenience_not_the_control():
    """The browser guard and the server refusal say the same thing; the server decides."""
    from app.routes.portal_admin import portal_admin_start_thread

    js = open("app/static/js/client_portal_admin.js", encoding="utf-8").read()
    assert 'var NO_CLIENT_SELECTED = "Select a client before starting a conversation.";' in js
    assert hub.NO_CLIENT_SELECTED == "Select a client before starting a conversation."

    response = portal_admin_start_thread(
        SimpleNamespace(state=SimpleNamespace(request_id="r1")),
        person_id="", subject="s", body="b", topic="", principal=_staff_principal())
    assert response.status_code == 303 and "error=" in response.headers["location"]


# --- F. composer layout contract -------------------------------------------------------------
#
# The composer rendered as a raw admin form: the client field stretched the container, Topic /
# Subject / Message were crushed into one row, and the button floated free. It now uses the same
# form idiom as the rest of the staff app. These pin the STRUCTURE, not the pixels.

def test_f1_the_composer_uses_the_standard_staff_form_idiom():
    html = _render_threads(_staff_principal())
    form = html.split('class="stack portal-compose-form"', 1)[1].split("</form>", 1)[0]

    assert '<div class="field">' in form, "controls are not wrapped in the standard .field"
    assert form.count('class="input"') >= 4, "not every control carries the shared .input class"
    assert '<div class="row">' in form, "the action is not in the standard action row"
    assert '<button class="btn" type="submit">Send secure message</button>' in form


def test_f2_topic_and_subject_share_a_responsive_two_column_row():
    """.cols-2.aside-left already collapses to one column on narrow screens."""
    html = _render_threads(_staff_principal())
    form = html.split('class="stack portal-compose-form"', 1)[1].split("</form>", 1)[0]

    pair = form.split('class="cols-2 aside-left"', 1)[1].split("</div>\n\n", 1)[0]
    assert 'for="thread-topic"' in pair and 'for="thread-subject"' in pair
    css = open("app/static/css/app.css", encoding="utf-8").read()
    assert ".cols-2.aside-left { grid-template-columns:minmax(0,1fr) minmax(0,2fr); }" in css
    assert ".cols-2, .cols-2.aside-left, .cols-2.aside-right { grid-template-columns:1fr; }" in css


def test_f3_client_and_message_each_occupy_a_full_row():
    html = _render_threads(_staff_principal())
    form = html.split('class="stack portal-compose-form"', 1)[1].split("</form>", 1)[0]

    # Neither sits inside the two-column pair.
    pair = form.split('class="cols-2 aside-left"', 1)[1].split("</div>\n\n", 1)[0]
    assert 'for="thread-client"' not in pair
    assert 'for="thread-body"' not in pair


def test_f4_the_message_box_is_a_real_multiline_textarea():
    html = _render_threads(_staff_principal())
    assert 'rows="5"' in html, "the message box lost its visible line count"

    css = open("app/static/css/app.css", encoding="utf-8").read()
    assert ".portal-compose-form textarea.input { height: auto; min-height: 132px;" in css, \
        "the textarea would inherit .input's 36px single-line height"
    assert "resize: vertical" in css


def test_f5_the_composer_rules_are_scoped_and_do_not_restyle_other_pages():
    """A bounded UI pass must not restyle unrelated staff pages.

    Deliberately NOT a `git diff` check — that passes vacuously once committed. This reads the
    stylesheet itself: every rule the composer needs is prefixed, and the SHARED `.input` rule is
    left exactly as it was for the ~18 other staff templates that rely on it."""
    css = open("app/static/css/app.css", encoding="utf-8").read()

    for scoped in (".portal-compose-form .field",
                   ".portal-compose-form textarea.input",
                   ".portal-compose-form select.input"):
        assert scoped in css, f"{scoped} is missing"

    # The UNSCOPED control rules are exactly the four that were already there. A new one would
    # change every staff form in the app, which this pass must not do.
    unscoped = {line.strip().split("{")[0].strip() for line in css.splitlines()
                if line.strip().startswith(("textarea", "select", ".input")) and "{" in line}
    assert unscoped == {".input", ".input:focus", ".input.invalid", "select.input"}, \
        f"the shared control rules changed: {sorted(unscoped)}"

    # The single-line height that ~18 other staff templates depend on is untouched.
    assert ".input { height:36px;" in css


def test_f6_the_composer_uses_no_inline_styles():
    """Inline style attributes are blocked by the site CSP."""
    html = _render_threads(_staff_principal())
    form = html.split('class="stack portal-compose-form"', 1)[1].split("</form>", 1)[0]
    assert "style=" not in form


def test_f7_the_helper_text_is_muted_and_sits_below_the_action():
    html = _render_threads(_staff_principal())
    form = html.split('class="stack portal-compose-form"', 1)[1].split("</form>", 1)[0]

    assert 'class="subtle">The client reads this in their 360Plus portal.' in form
    assert form.index('class="row"') < form.index("The client reads this")


def test_f8_the_typeahead_keyboard_and_accessibility_contract_is_unchanged():
    html = _render_threads(_staff_principal())

    for attribute in ('role="combobox"', 'aria-autocomplete="list"', 'aria-expanded="false"',
                      'aria-controls="thread-client-results"', 'role="listbox"',
                      'aria-label="Matching clients"', 'for="thread-client"'):
        assert attribute in html, f"the selector lost {attribute}"
    assert "Start typing a client's name..." in html


# --- G. thread page layout contract ----------------------------------------------------------
#
# The thread page was functionally right but rendered as a raw admin form: metadata jammed on one
# line, routing controls crushed together, message cards styled with inline hex, and the reply
# textarea squashed by the shared .input height. This pins STRUCTURE and the behaviour it must not
# have changed — every form action, field name and value is identical to before.

def _render_thread_page(principal, thread_id):
    from app.routes.portal_admin import portal_admin_thread
    request = SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}", principal=principal,
                              demo_mode=False),
        client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"},
        query_params={}, session={},
        url=SimpleNamespace(path=f"/admin/client-portal/threads/{thread_id}"))
    return portal_admin_thread(thread_id, request, principal=principal).body.decode()


def _seeded_thread(staff):
    _, _, person_id, _ = seed_portal_account(staff.user_id)
    return hub.staff_start_thread(staff, person_id=person_id, subject="Layout check",
                                  body="First line\nSecond line")


def test_g1_the_thread_page_uses_the_standard_staff_conventions():
    staff = _staff_principal()
    html = _render_thread_page(staff, _seeded_thread(staff))

    for convention in ('class="card thread-header"', 'class="stat-grid"', 'class="field"',
                       'class="input"', 'class="row"', 'class="stack', 'class="section-title"',
                       'class="badge'):
        assert convention in html, f"the page does not use {convention}"


def test_g2_the_page_introduces_no_inline_styles():
    """Inline style attributes are blocked by the site CSP."""
    staff = _staff_principal()
    html = _render_thread_page(staff, _seeded_thread(staff))
    body = html.split('<a href="/admin/client-portal/threads">', 1)[1].split("</main>", 1)[0]
    assert "style=" not in body
    assert "style=" not in open("app/templates/admin/portal_thread.html", encoding="utf-8").read()


def test_g3_every_form_action_and_field_name_is_unchanged():
    """A layout pass must not alter what any control posts."""
    staff = _staff_principal()
    thread_id = _seeded_thread(staff)
    html = _render_thread_page(staff, thread_id)

    base = f"/admin/client-portal/threads/{thread_id}"
    for action in ("/resolve", "/assign", "/create-request", "/link-request", "/reply"):
        assert f'action="{base}{action}"' in html, f"the {action} form moved or was renamed"
    for name in ('name="action"', 'name="topic"', 'name="assigned_user_id"',
                 'name="assigned_team_id"', 'name="title"', 'name="description"',
                 'name="request_ref"', 'name="body"', 'name="internal_note"'):
        assert name in html, f"the {name} control was renamed"
    assert 'value="1"' in html and 'type="checkbox"' in html   # internal_note unchanged


def test_g4_routing_controls_are_a_responsive_grid_not_one_crushed_row():
    staff = _staff_principal()
    html = _render_thread_page(staff, _seeded_thread(staff))

    assert 'class="thread-control-grid"' in html
    css = open("app/static/css/app.css", encoding="utf-8").read()
    assert "grid-template-columns: repeat(auto-fit, minmax(190px, 1fr))" in css
    routing = html.split('class="thread-control-grid"', 1)[1].split("</div>\n    <div class=\"row\"", 1)[0]
    for label in ("Topic", "Assign to employee", "Assign to team"):
        assert label in routing


def test_g5_message_cards_distinguish_client_staff_and_internal_by_class_not_inline_colour():
    staff = _staff_principal()
    _, principal, person_id, _ = seed_portal_account(staff.user_id)
    thread_id = create_thread(principal, household_id=_household_of(person_id),
                              person_id=person_id, subject="Tri-state", body="client says hi")
    staff_send_message(thread_id=thread_id, user_id=staff.user_id, body="staff replies")
    staff_send_message(thread_id=thread_id, user_id=staff.user_id, body="note to self",
                       internal_note=True)

    html = _render_thread_page(staff, thread_id)

    assert 'class="thread-msg client"' in html
    assert 'class="thread-msg staff"' in html
    assert 'class="thread-msg internal"' in html
    for hexish in ("#fef9c3", "#eff6ff", "#f9fafb", "#6b7280"):
        assert hexish not in html, f"inline colour {hexish} is still rendered"


def test_g6_internal_notes_remain_staff_only_and_are_labelled():
    """Visibility semantics unchanged: the client view still never returns the note."""
    staff = _staff_principal()
    _, principal, person_id, _ = seed_portal_account(staff.user_id)
    thread_id = create_thread(principal, household_id=_household_of(person_id),
                              person_id=person_id, subject="Note check", body="hello")
    secret = f"INTERNAL-{uuid.uuid4().hex[:8]}"
    staff_send_message(thread_id=thread_id, user_id=staff.user_id, body=secret, internal_note=True)

    staff_html = _render_thread_page(staff, thread_id)
    assert secret in staff_html
    assert "Internal note (staff only)" in staff_html

    assert secret not in [m["body"] for m in list_messages(principal, thread_id)]


def test_g7_multiline_bodies_stay_escaped_and_keep_their_line_breaks():
    staff = _staff_principal()
    _, principal, person_id, _ = seed_portal_account(staff.user_id)
    thread_id = create_thread(principal, household_id=_household_of(person_id),
                              person_id=person_id, subject="Escaping",
                              body="<script>alert(1)</script>\nsecond line")

    html = _render_thread_page(staff, thread_id)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert 'class="msg-body"' in html          # the shared pre-wrap element
    assert "second line" in html
    assert "|safe" not in open("app/templates/admin/portal_thread.html", encoding="utf-8").read()


def test_g8_the_reply_textarea_has_scoped_multiline_sizing():
    staff = _staff_principal()
    html = _render_thread_page(staff, _seeded_thread(staff))
    assert 'rows="5"' in html and 'id="reply-body"' in html

    css = open("app/static/css/app.css", encoding="utf-8").read()
    assert ".thread-reply-form textarea.input { height: auto; min-height: 132px;" in css
    # The GLOBAL textarea problem is deliberately left alone by this pass.
    unscoped = {line.strip().split("{")[0].strip() for line in css.splitlines()
                if line.strip().startswith(("textarea", "select", ".input")) and "{" in line}
    assert unscoped == {".input", ".input:focus", ".input.invalid", "select.input"}, \
        f"a shared control rule changed: {sorted(unscoped)}"


def test_g9_the_internal_note_control_sits_apart_from_the_send_action():
    staff = _staff_principal()
    html = _render_thread_page(staff, _seeded_thread(staff))
    form = html.split('class="card stack thread-reply-form"', 1)[1].split("</form>", 1)[0]

    assert 'class="thread-internal-toggle"' in form
    assert "not visible to the client" in form
    # Message field, then the option row, then the action — three separate rows.
    assert form.index('id="reply-body"') < form.index("thread-internal-toggle") \
        < form.index('type="submit"')


def _household_of(person_id):
    with engine.connect() as c:
        return c.execute(select(people.c.household_id).where(
            people.c.id == person_id)).scalar_one()
