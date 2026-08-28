"""360Plus Communication Hub — behavior + comprehensive security/adversarial tests.

Covers topic/routing, relationship-owned read state, staff work-queue filtering, request/document
linkage, the unified timeline, internal-note protection, feature-control integration, audit, and the
12 required security scenarios (cross-client isolation, business org scoping, internal-note & internal
document protection, disabled-feature enforcement, staff RBAC, forged IDs, audit).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select

from app.db import (
    audit_events,
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
