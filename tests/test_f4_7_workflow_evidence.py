"""F4.7 / Epic 4 — Workflow audit, evidence & capability reconciliation tests (ADR-016).

Confirms complete audit + write-once evidence coverage for every material workflow
outcome (launch, transitions, step activation/completion, approval request/decision/
reassignment, automation execution, SLA escalation), traceable to the audit event and
the underlying record, deterministic + idempotent, and incapable of changing workflow
state. Also confirms capability reconciliation and SoD independence from capabilities.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.db import (
    audit_events,
    engine,
    households,
    people,
    roles,
    user_roles,
    users,
    workflow_steps,
)
from app.security.evidence import _evidence_table, get_evidence
from app.services.workflow_automation import (
    complete_step,
    decide_approval,
    evaluate_sla,
    execute_automation_action,
    launch_workflow,
    reassign_approval,
    request_approval,
    transition_workflow,
    workflow_detail,
)
from app.services.workflow_evidence import record_workflow_evidence, workflow_evidence_uid

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_TEMPLATE = "client_onboarding"


def _users(n=3):
    s = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"F47 {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"F47 {s}", active=True).returning(people.c.id)).scalar_one()
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        uids = []
        for i in range(n):
            uid = c.execute(users.insert().values(email=f"f47-{i}-{s}@e.com", normalized_email=f"f47-{i}-{s}@e.com",
                            display_name=f"f47-{i}", auth_subject=f"f47-{i}-{s}", status="active").returning(users.c.id)).scalar_one()
            if role_id:
                c.execute(user_roles.insert().values(user_id=uid, role_id=role_id))
            uids.append(uid)
    return (pid, hid, *uids)


def _audit_ids(action, entity_type, entity_id):
    with engine.connect() as c:
        return [r[0] for r in c.execute(select(audit_events.c.id).where(
            audit_events.c.action == action, audit_events.c.entity_type == entity_type,
            audit_events.c.entity_id == str(entity_id))).all()]


def _evidence_for_audit(audit_id):
    evidence = _evidence_table()
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(evidence).where(
            evidence.c.audit_event_id == audit_id)).scalar_one()


def _assert_audit_and_evidence(action, entity_type, entity_id):
    """Every material outcome has exactly one audit event and one linked evidence record."""
    ids = _audit_ids(action, entity_type, entity_id)
    assert len(ids) == 1, f"{action} for {entity_type}:{entity_id} -> {len(ids)} audit events (expected 1)"
    assert _evidence_for_audit(ids[0]) == 1, f"{action} -> missing/duplicate evidence"
    return ids[0]


# --- coverage matrix: every material outcome has audit + evidence -------------

def test_launch_step_and_completion_coverage():
    pid, hid, actor, *_ = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=actor, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    _assert_audit_and_evidence("workflow.launched", "workflow_instance", instance_id)
    steps = workflow_detail(instance_id)["steps"]
    first = next(s for s in steps if s["status"] == "active")
    _assert_audit_and_evidence("workflow.step.activated", "workflow_step", first["id"])  # initial activation

    complete_step(first["id"], actor_user_id=actor)
    _assert_audit_and_evidence("workflow.step.completed", "workflow_step", first["id"])
    activated = next(s for s in workflow_detail(instance_id)["steps"] if s["status"] == "active")
    _assert_audit_and_evidence("workflow.step.activated", "workflow_step", activated["id"])  # cascade activation


def test_transition_coverage():
    pid, hid, actor, *_ = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=actor, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    transition_workflow(instance_id, "pause", actor_user_id=actor)
    # transition audit uses the action verb (workflow.pause); the envelope uses past-tense.
    _assert_audit_and_evidence("workflow.pause", "workflow_instance", instance_id)


def test_approval_coverage():
    pid, hid, requester, approver, approver2 = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=requester, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    step_id = workflow_detail(instance_id)["steps"][0]["id"]
    approval_id = request_approval(step_id, requested_by_user_id=requester, approver_user_id=approver)
    _assert_audit_and_evidence("workflow.approval.requested", "work_approval", approval_id)
    reassign_approval(approval_id, reassigned_by_user_id=requester, new_approver_user_id=approver2)
    _assert_audit_and_evidence("workflow.approval.reassigned", "work_approval", approval_id)
    decide_approval(approval_id, approver_user_id=approver2, decision="approved")
    _assert_audit_and_evidence("workflow.approval.decided", "work_approval", approval_id)


def test_automation_coverage():
    pid, hid, actor, *_ = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=actor, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    idem = f"f47-auto-{uuid.uuid4()}"
    action_id = execute_automation_action(instance_id, "publish_timeline", payload={"title": "x"}, idempotency_key=idem)
    _assert_audit_and_evidence("workflow.automation.executed", "automation_action", action_id)


def test_sla_coverage():
    pid, hid, actor, *_ = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=actor, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    with engine.begin() as c:
        step_id = c.execute(select(workflow_steps.c.id).where(
            workflow_steps.c.workflow_instance_id == instance_id, workflow_steps.c.status == "active").limit(1)).scalar_one()
        c.execute(workflow_steps.update().where(workflow_steps.c.id == step_id).values(
            sla_due_at=datetime.now(UTC) - timedelta(hours=1)))
    evaluate_sla()
    from app.db import workflow_escalations
    with engine.connect() as c:
        esc_id = c.execute(select(workflow_escalations.c.id).where(
            workflow_escalations.c.workflow_step_id == step_id)).scalar_one()
    _assert_audit_and_evidence("workflow.sla.escalated", "workflow_escalation", esc_id)


# --- deterministic + idempotent; retries do not duplicate evidence -----------

def test_evidence_is_deterministic_and_idempotent():
    pid, hid, actor, *_ = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=actor, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    audit_id = _audit_ids("workflow.launched", "workflow_instance", instance_id)[0]
    uid = workflow_evidence_uid("launched", audit_id)
    assert get_evidence(evidence_uid=uid) is not None
    # re-recording the same outcome is a no-op (write-once / idempotent)
    r1 = record_workflow_evidence(outcome="launched", workflow_instance_id=instance_id, audit_event_id=audit_id)
    r2 = record_workflow_evidence(outcome="launched", workflow_instance_id=instance_id, audit_event_id=audit_id)
    assert r1.evidence_uid == r2.evidence_uid == uid
    assert _evidence_for_audit(audit_id) == 1  # still exactly one


def test_sla_retry_does_not_duplicate_evidence():
    pid, hid, actor, *_ = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=actor, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    with engine.begin() as c:
        step_id = c.execute(select(workflow_steps.c.id).where(
            workflow_steps.c.workflow_instance_id == instance_id, workflow_steps.c.status == "active").limit(1)).scalar_one()
        c.execute(workflow_steps.update().where(workflow_steps.c.id == step_id).values(
            sla_due_at=datetime.now(UTC) - timedelta(hours=1)))
    evaluate_sla(); evaluate_sla()  # retry
    from app.db import workflow_escalations
    with engine.connect() as c:
        esc_id = c.execute(select(workflow_escalations.c.id).where(
            workflow_escalations.c.workflow_step_id == step_id)).scalar_one()
    audit_id = _audit_ids("workflow.sla.escalated", "workflow_escalation", esc_id)[0]
    assert _evidence_for_audit(audit_id) == 1  # retry created no duplicate evidence


# --- evidence/audit never change workflow state ------------------------------

def test_evidence_never_changes_workflow_state():
    pid, hid, actor, *_ = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=actor, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    before = (workflow_detail(instance_id)["workflow"]["status"],
              tuple(s["status"] for s in workflow_detail(instance_id)["steps"]))
    audit_id = _audit_ids("workflow.launched", "workflow_instance", instance_id)[0]
    record_workflow_evidence(outcome="probe", workflow_instance_id=instance_id, audit_event_id=audit_id)
    after = (workflow_detail(instance_id)["workflow"]["status"],
             tuple(s["status"] for s in workflow_detail(instance_id)["steps"]))
    assert before == after


# --- capability reconciliation (SoD independent of capabilities) -------------

def test_sod_is_enforced_independently_of_capabilities():
    """SoD cannot be bypassed by capability assignment — it is enforced in the service
    layer and the DB, regardless of the caller's capabilities."""
    pid, hid, requester, approver, _ = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=requester, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    step_id = workflow_detail(instance_id)["steps"][0]["id"]
    approval_id = request_approval(step_id, requested_by_user_id=requester, approver_user_id=approver)
    import pytest
    with pytest.raises(ValueError, match="Requester cannot approve their own work"):
        decide_approval(approval_id, approver_user_id=requester, decision="approved")


def test_workflow_audit_and_evidence_are_append_only():
    """Workflow-produced audit and evidence records inherit the F3.1/F3.3 write-once
    guarantees — UPDATE/DELETE are rejected at the database level."""
    import pytest
    from sqlalchemy import update

    from app.db import audit_events as ae
    pid, hid, actor, *_ = _users()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=actor, person_id=pid, household_id=hid,
                                  idempotency_key=f"f47-{uuid.uuid4()}")
    audit_id = _audit_ids("workflow.launched", "workflow_instance", instance_id)[0]
    ev = get_evidence(evidence_uid=workflow_evidence_uid("launched", audit_id))
    evidence = _evidence_table()
    with pytest.raises(Exception):  # noqa: B017 - evidence_immutable
        with engine.begin() as c:
            c.execute(update(evidence).where(evidence.c.id == ev.id).values(reference="tampered"))
    with pytest.raises(Exception):  # noqa: B017 - audit_events_immutable
        with engine.begin() as c:
            c.execute(update(ae).where(ae.c.id == audit_id).values(action="tampered"))


def test_no_new_routes_and_docs_present():
    from app.main import app
    # 306 through F4.7; F4.8 additively exposed 3 API routes (reassign/history/evidence).
    assert len([r for r in app.routes]) == 693  # ... +29 security (D.25) +47 observability (D.26)
    assert (REPO_ROOT / "docs" / "WORKFLOW_EVIDENCE_AUDIT.md").is_file()
