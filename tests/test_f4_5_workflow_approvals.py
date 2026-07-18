"""F4.5 / Epic 4 — Workflow approval engine acceptance tests (ADR-016).

Deterministic approval state transitions, separation-of-duty (SoD) enforcement,
routing, reassignment (with preserved audit history), approval event publication,
approval audit records, and the guarantee that approvals never bypass workflow rules.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import (
    audit_events,
    engine,
    households,
    people,
    roles,
    user_roles,
    users,
    work_approvals,
    workflow_events,
    workflow_steps,
)
from app.platform.outbox import outbox_events
from app.platform.workflow_approval_state import (
    APPROVAL_STATES,
    can_reassign,
    validate_decision,
    validate_reassignable,
)
from app.services.workflow_automation import (
    decide_approval,
    launch_workflow,
    reassign_approval,
    request_approval,
    workflow_detail,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_TEMPLATE = "client_onboarding"


def _users(n=3):
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"F45 {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"F45 {suffix}", active=True).returning(people.c.id)).scalar_one()
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        uids = []
        for i in range(n):
            uid = c.execute(users.insert().values(
                email=f"f45-{i}-{suffix}@e.com", normalized_email=f"f45-{i}-{suffix}@e.com",
                display_name=f"f45-{i}", auth_subject=f"f45-{i}-{suffix}", status="active",
            ).returning(users.c.id)).scalar_one()
            if role_id:
                c.execute(user_roles.insert().values(user_id=uid, role_id=role_id))
            uids.append(uid)
    return (pid, hid, *uids)


def _step_for_new_instance(uid, pid, hid) -> int:
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=uid, person_id=pid, household_id=hid,
                                  idempotency_key=f"f45-{uuid.uuid4()}")
    with engine.connect() as c:
        return c.execute(select(workflow_steps.c.id).where(
            workflow_steps.c.workflow_instance_id == instance_id).order_by(workflow_steps.c.sequence).limit(1)).scalar_one()


def _wf_events(instance_id, event_type):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(workflow_events).where(
            workflow_events.c.workflow_instance_id == instance_id,
            workflow_events.c.event_type == event_type)).scalar_one()


# --- pure specification ------------------------------------------------------

def test_approval_states_and_decisions_are_deterministic():
    assert APPROVAL_STATES == frozenset({"pending", "approved", "rejected"})
    assert validate_decision("approved") == "approved"
    with pytest.raises(ValueError, match="Decision must be approved or rejected"):
        validate_decision("maybe")
    assert can_reassign("pending") is True and can_reassign("approved") is False
    with pytest.raises(ValueError, match="Only a pending approval can be reassigned"):
        validate_reassignable({"status": "approved"})


# --- SoD enforcement ---------------------------------------------------------

def test_sod_request_and_decision_rules():
    pid, hid, requester, approver, other = _users()
    step_id = _step_for_new_instance(requester, pid, hid)
    with pytest.raises(ValueError, match="Independent approval cannot be self-approved"):
        request_approval(step_id, requested_by_user_id=requester, approver_user_id=requester)
    approval_id = request_approval(step_id, requested_by_user_id=requester, approver_user_id=approver)
    with pytest.raises(ValueError, match="Requester cannot approve their own work"):
        decide_approval(approval_id, approver_user_id=requester, decision="approved")
    with pytest.raises(ValueError, match="Approval is assigned to another user"):
        decide_approval(approval_id, approver_user_id=other, decision="approved")
    # the assigned approver decides deterministically
    decide_approval(approval_id, approver_user_id=approver, decision="approved")
    with engine.connect() as c:
        assert c.execute(select(work_approvals.c.status).where(work_approvals.c.id == approval_id)).scalar_one() == "approved"


def test_rejection_is_recorded_and_pending_guard():
    pid, hid, requester, approver, _ = _users()
    step_id = _step_for_new_instance(requester, pid, hid)
    approval_id = request_approval(step_id, requested_by_user_id=requester, approver_user_id=approver)
    decide_approval(approval_id, approver_user_id=approver, decision="rejected", notes="incomplete")
    with engine.connect() as c:
        row = c.execute(select(work_approvals).where(work_approvals.c.id == approval_id)).mappings().one()
    assert row["status"] == "rejected" and row["decision_notes"] == "incomplete"
    # a decided approval cannot be decided again
    with pytest.raises(ValueError, match="Pending approval not found"):
        decide_approval(approval_id, approver_user_id=approver, decision="approved")


# --- reassignment preserves audit history ------------------------------------

def test_reassignment_reroutes_and_preserves_history():
    pid, hid, requester, approver1, approver2 = _users()
    step_id = _step_for_new_instance(requester, pid, hid)
    approval_id = request_approval(step_id, requested_by_user_id=requester, approver_user_id=approver1)
    with engine.connect() as c:
        instance_id = c.execute(select(workflow_steps.c.workflow_instance_id).where(workflow_steps.c.id == step_id)).scalar_one()

    reassign_approval(approval_id, reassigned_by_user_id=requester, new_approver_user_id=approver2, reason="OOO")
    with engine.connect() as c:
        row = c.execute(select(work_approvals).where(work_approvals.c.id == approval_id)).mappings().one()
    assert row["approver_user_id"] == approver2 and row["status"] == "pending"

    # the reassignment cannot route to the requester (SoD)
    with pytest.raises(ValueError, match="Independent approval cannot be self-approved"):
        reassign_approval(approval_id, reassigned_by_user_id=requester, new_approver_user_id=requester)

    # history preserved in the append-only event ledger: requested + reassigned rows
    assert _wf_events(instance_id, "approval_requested") == 1
    assert _wf_events(instance_id, "approval_reassigned") == 1

    # the new approver can now decide; the old approver cannot
    with pytest.raises(ValueError, match="Approval is assigned to another user"):
        decide_approval(approval_id, approver_user_id=approver1, decision="approved")
    decide_approval(approval_id, approver_user_id=approver2, decision="approved")


def test_decided_approval_cannot_be_reassigned():
    pid, hid, requester, approver1, approver2 = _users()
    step_id = _step_for_new_instance(requester, pid, hid)
    approval_id = request_approval(step_id, requested_by_user_id=requester, approver_user_id=approver1)
    decide_approval(approval_id, approver_user_id=approver1, decision="approved")
    with pytest.raises(ValueError, match="Only a pending approval can be reassigned"):
        reassign_approval(approval_id, reassigned_by_user_id=requester, new_approver_user_id=approver2)


# --- approval events + audit -------------------------------------------------

def test_approval_events_and_audit_records_are_published():
    pid, hid, requester, approver, _ = _users()
    step_id = _step_for_new_instance(requester, pid, hid)
    with engine.connect() as c:
        instance_id = c.execute(select(workflow_steps.c.workflow_instance_id).where(workflow_steps.c.id == step_id)).scalar_one()
    approval_id = request_approval(step_id, requested_by_user_id=requester, approver_user_id=approver)
    decide_approval(approval_id, approver_user_id=approver, decision="approved")

    subject = f"workflow_instance:{instance_id}"
    with engine.connect() as c:
        env_names = {r["name"] for r in c.execute(select(outbox_events).where(
            outbox_events.c.name.like("workflow.approval.%"))).mappings().all()
            if r["payload"].get("subject_ref") == subject}
        audit = {r["action"] for r in c.execute(select(audit_events).where(
            audit_events.c.entity_type == "work_approval", audit_events.c.entity_id == str(approval_id))).mappings().all()}
    assert "workflow.approval.requested" in env_names and "workflow.approval.decided" in env_names
    assert {"workflow.approval.requested", "workflow.approval.decided"} <= audit


# --- approvals never bypass workflow rules -----------------------------------

def test_approvals_never_bypass_workflow_rules():
    """A decision records the approval but does NOT complete the step or advance the
    workflow — completion still goes through the engine's approval gate."""
    pid, hid, requester, approver, _ = _users()
    step_id = _step_for_new_instance(requester, pid, hid)
    with engine.connect() as c:
        instance_id = c.execute(select(workflow_steps.c.workflow_instance_id).where(workflow_steps.c.id == step_id)).scalar_one()
    status_before = workflow_detail(instance_id)["workflow"]["status"]
    steps_before = [s["status"] for s in workflow_detail(instance_id)["steps"]]

    approval_id = request_approval(step_id, requested_by_user_id=requester, approver_user_id=approver)
    decide_approval(approval_id, approver_user_id=approver, decision="approved")

    # deciding did not change workflow/step state
    assert workflow_detail(instance_id)["workflow"]["status"] == status_before
    assert [s["status"] for s in workflow_detail(instance_id)["steps"]] == steps_before


def test_approval_module_is_pure_and_documented():
    source = (REPO_ROOT / "app" / "platform" / "workflow_approval_state.py").read_text()
    assert "from app.db" not in source and "sqlalchemy" not in source  # pure
    assert "APIRouter" not in source
    assert (REPO_ROOT / "docs" / "WORKFLOW_APPROVALS.md").is_file()
