"""F4.6 / Epic 4 — Workflow SLA & escalation engine acceptance tests (ADR-016).

Deterministic SLA deadline evaluation, exactly-once escalation (idempotent,
retry-safe), escalation event publication + audit records, and the guarantee that
SLA processing observes but never drives workflow execution.
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
    users,
    workflow_escalations,
    workflow_events,
    workflow_instances,
    workflow_steps,
)
from app.platform.outbox import outbox_events
from app.platform.workflow_sla import (
    DEFAULT_ESCALATION_LEVEL,
    ESCALATION_TYPE_SLA_BREACH,
    evaluate_escalation,
    is_overdue,
)
from app.services.workflow_automation import evaluate_sla, launch_workflow, workflow_detail

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_TEMPLATE = "client_onboarding"


def _instance():
    s = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"F46 {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"F46 {s}", active=True).returning(people.c.id)).scalar_one()
        uid = c.execute(users.insert().values(email=f"f46-{s}@e.com", normalized_email=f"f46-{s}@e.com",
                        display_name="f46", auth_subject=f"f46-{s}", status="active").returning(users.c.id)).scalar_one()
    return launch_workflow(DB_TEMPLATE, actor_user_id=uid, person_id=pid, household_id=hid, idempotency_key=f"f46-{s}")


def _force_overdue(instance_id) -> int:
    """Make the active step overdue (SLA in the past). Returns the step id."""
    past = datetime.now(UTC) - timedelta(hours=1)
    with engine.begin() as c:
        step_id = c.execute(select(workflow_steps.c.id).where(
            workflow_steps.c.workflow_instance_id == instance_id,
            workflow_steps.c.status == "active").order_by(workflow_steps.c.sequence).limit(1)).scalar_one()
        c.execute(workflow_steps.update().where(workflow_steps.c.id == step_id).values(sla_due_at=past))
    return step_id


# --- pure specification ------------------------------------------------------

def test_sla_policy_is_deterministic():
    now = datetime.now(UTC)
    assert is_overdue(now - timedelta(minutes=1), now) is True
    assert is_overdue(now + timedelta(minutes=1), now) is False
    assert is_overdue(None, now) is False
    assert evaluate_escalation(now - timedelta(hours=1), now) == {
        "escalation_type": ESCALATION_TYPE_SLA_BREACH, "level": DEFAULT_ESCALATION_LEVEL}
    assert evaluate_escalation(now + timedelta(hours=1), now) is None
    assert evaluate_escalation(None, now) is None


# --- exactly once / retry-safe -----------------------------------------------

def test_escalation_executes_exactly_once_and_is_retry_safe():
    instance_id = _instance()
    step_id = _force_overdue(instance_id)

    created = evaluate_sla()  # returns created escalation ids
    with engine.connect() as c:
        esc_id = c.execute(select(workflow_escalations.c.id).where(
            workflow_escalations.c.workflow_step_id == step_id,
            workflow_escalations.c.escalation_type == "sla_breach",
            workflow_escalations.c.level == 1)).scalar_one()
    assert esc_id in created                        # our overdue step escalated
    again = evaluate_sla()                           # re-run (retry) — idempotent
    assert esc_id not in again                       # not recreated
    with engine.connect() as c:
        n = c.execute(select(func.count()).select_from(workflow_escalations).where(
            workflow_escalations.c.workflow_step_id == step_id,
            workflow_escalations.c.escalation_type == "sla_breach",
            workflow_escalations.c.level == 1)).scalar_one()
        events = c.execute(select(func.count()).select_from(workflow_events).where(
            workflow_events.c.workflow_step_id == step_id,
            workflow_events.c.event_type == "sla_escalated")).scalar_one()
    assert n == 1 and events == 1                   # exactly one escalation + one domain event


def test_not_overdue_does_not_escalate():
    instance_id = _instance()  # fresh: active step has a future SLA
    with engine.connect() as c:
        step_id = c.execute(select(workflow_steps.c.id).where(
            workflow_steps.c.workflow_instance_id == instance_id,
            workflow_steps.c.status == "active").limit(1)).scalar_one()
    evaluate_sla()
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(workflow_escalations).where(
            workflow_escalations.c.workflow_step_id == step_id)).scalar_one() == 0


# --- escalation events + audit -----------------------------------------------

def test_escalation_event_and_audit_are_published_exactly_once():
    instance_id = _instance()
    step_id = _force_overdue(instance_id)
    evaluate_sla()
    evaluate_sla()  # retry — must not duplicate the envelope or audit

    subject = f"workflow_instance:{instance_id}"
    with engine.connect() as c:
        envs = [r for r in c.execute(select(outbox_events).where(
            outbox_events.c.name == "workflow.sla.escalated")).mappings().all()
            if r["payload"].get("subject_ref") == subject]
        esc_id = c.execute(select(workflow_escalations.c.id).where(
            workflow_escalations.c.workflow_step_id == step_id)).scalar_one()
        audit = c.execute(select(func.count()).select_from(audit_events).where(
            audit_events.c.entity_type == "workflow_escalation",
            audit_events.c.entity_id == str(esc_id),
            audit_events.c.action == "workflow.sla.escalated")).scalar_one()
    assert len(envs) == 1 and envs[0]["payload"]["payload"]["level"] == 1
    assert audit == 1


# --- SLA never changes workflow state ----------------------------------------

def test_sla_never_changes_workflow_state():
    instance_id = _instance()
    _force_overdue(instance_id)
    status_before = workflow_detail(instance_id)["workflow"]["status"]
    steps_before = {s["id"]: s["status"] for s in workflow_detail(instance_id)["steps"]}
    evaluate_sla()
    with engine.connect() as c:
        status_after = c.execute(select(workflow_instances.c.status).where(
            workflow_instances.c.id == instance_id)).scalar_one()
    steps_after = {s["id"]: s["status"] for s in workflow_detail(instance_id)["steps"]}
    assert status_after == status_before and steps_after == steps_before  # no state mutation


def test_sla_module_is_pure_and_documented():
    source = (REPO_ROOT / "app" / "platform" / "workflow_sla.py").read_text()
    assert "from app.db" not in source and "sqlalchemy" not in source  # pure
    assert "APIRouter" not in source
    assert (REPO_ROOT / "docs" / "WORKFLOW_SLA.md").is_file()
