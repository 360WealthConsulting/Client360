"""Workflow orchestration service (Phase D.17) — facade over the existing engine + retry/assign.

Reuses the engine's ``launch_workflow`` / ``transition_workflow`` / ``complete_step`` /
``workflow_detail`` (all preserved) and adds record-scoped list/read, per-step retry, and direct
step assignment. Scope: a workflow instance is visible via its person/household anchor (or
``record.read_all``); firm instances (no anchor) are visible to ``workflow.view`` holders. Retry
and assignment append to the immutable ``workflow_events`` ledger.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select

from app.db import engine, people, users, workflow_events, workflow_instances, workflow_steps
from app.security.authorization import accessible_person_ids, record_in_scope
from app.services import workflow_automation as wf

_ACTIVE_INSTANCE_STATES = ("active", "paused")


class WorkflowError(Exception):
    """Validation or lifecycle error."""


class WorkflowNotFound(Exception):
    """Workflow not found or out of scope."""


def _now():
    return datetime.now(UTC)


# --- scope -------------------------------------------------------------------

def _scope_clause(principal, c):
    if principal.can("record.read_all"):
        return None
    ids = accessible_person_ids(c, principal)
    conds = [and_(workflow_instances.c.person_id.is_(None),
                  workflow_instances.c.household_id.is_(None))]   # firm workflows
    if ids:
        conds.append(workflow_instances.c.person_id.in_(tuple(ids)))
        hh = set(c.scalars(select(people.c.household_id).where(
            people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))
        if hh:
            conds.append(workflow_instances.c.household_id.in_(tuple(hh)))
    return or_(*conds)


def _visible(principal, inst, c) -> bool:
    if principal.can("record.read_all"):
        return True
    if inst.get("person_id") and record_in_scope(principal, "person", inst["person_id"], connection=c):
        return True
    if inst.get("household_id") and record_in_scope(principal, "household", inst["household_id"], connection=c):
        return True
    return not (inst.get("person_id") or inst.get("household_id"))


# --- reads -------------------------------------------------------------------

def templates():
    return [dict(t) for t in wf.list_templates()]


def list_instances(principal, *, status=None, workflow_type=None, search=None, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if status:
            conds.append(workflow_instances.c.status == status)
        if workflow_type:
            conds.append(workflow_instances.c.workflow_type == workflow_type)
        if search:
            conds.append(workflow_instances.c.name.ilike(f"%{search.strip()}%"))
        where = and_(*conds) if conds else None
        total = c.scalar(select(func.count()).select_from(workflow_instances).where(where)
                         if where is not None else select(func.count()).select_from(workflow_instances))
        stmt = select(workflow_instances)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(workflow_instances.c.id.desc()).limit(page_size)
            .offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def get_instance(principal, instance_id: int) -> dict | None:
    try:
        detail = wf.workflow_detail(instance_id, principal)
    except (ValueError, PermissionError):
        return None
    return {"workflow": dict(detail["workflow"]), "steps": [dict(s) for s in detail["steps"]],
            "events": [dict(e) for e in detail["events"]]}


def _load_scoped(c, principal, instance_id: int) -> dict:
    inst = c.execute(select(workflow_instances).where(
        workflow_instances.c.id == instance_id)).mappings().first()
    if inst is None or not _visible(principal, dict(inst), c):
        raise WorkflowNotFound(str(instance_id))
    return dict(inst)


def _step_instance(c, step_id: int) -> dict:
    step = c.execute(select(workflow_steps).where(workflow_steps.c.id == step_id)).mappings().first()
    if step is None:
        raise WorkflowNotFound(f"step {step_id}")
    return dict(step)


# --- lifecycle (reuse the engine) --------------------------------------------

def launch(principal, template_code: str, *, actor_user_id, person_id=None, household_id=None,
           priority="normal", context=None, idempotency_key=None) -> dict:
    try:
        instance_id = wf.launch_workflow(template_code, actor_user_id=actor_user_id,
                                         person_id=person_id, household_id=household_id,
                                         priority=priority, context=context or {},
                                         idempotency_key=idempotency_key)
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc
    return get_instance(principal, instance_id) or {"workflow": {"id": instance_id}}


def transition(principal, instance_id: int, action: str, *, actor_user_id, reason=None) -> dict:
    with engine.connect() as c:
        _load_scoped(c, principal, instance_id)
    try:
        wf.transition_workflow(instance_id, action, actor_user_id=actor_user_id, reason=reason)
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc
    return get_instance(principal, instance_id)


def complete_step(principal, step_id: int, *, actor_user_id) -> dict:
    with engine.connect() as c:
        step = _step_instance(c, step_id)
        _load_scoped(c, principal, step["workflow_instance_id"])
    try:
        wf.complete_step(step_id, actor_user_id=actor_user_id)
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc
    return get_instance(principal, step["workflow_instance_id"])


def request_approval(principal, step_id: int, *, requested_by_user_id, approver_user_id=None,
                     approver_team_id=None):
    with engine.connect() as c:
        step = _step_instance(c, step_id)
        _load_scoped(c, principal, step["workflow_instance_id"])
    try:
        return wf.request_approval(step_id, requested_by_user_id=requested_by_user_id,
                                   approver_user_id=approver_user_id, approver_team_id=approver_team_id)
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc


def decide_approval(principal, approval_id: int, *, decision, approver_user_id, note=None):
    try:
        return wf.decide_approval(approval_id, decision=decision, approver_user_id=approver_user_id,
                                  note=note)
    except (ValueError, TypeError) as exc:
        raise WorkflowError(str(exc)) from exc


# --- retry + assignment (new; the engine's gaps) -----------------------------

def retry_step(principal, step_id: int, *, actor_user_id) -> dict:
    """Retry a step within its retry budget. Increments ``retry_count`` (bounded by
    ``max_retries``) and re-activates the step. Deterministic; appends a ``step_retried`` event."""
    with engine.begin() as c:
        step = _step_instance(c, step_id)
        _load_scoped(c, principal, step["workflow_instance_id"])
        if (step.get("retry_count") or 0) >= (step.get("max_retries") or 0):
            raise WorkflowError("retry budget exhausted (increase max_retries)")
        now = _now()
        c.execute(workflow_steps.update().where(workflow_steps.c.id == step_id).values(
            retry_count=(step.get("retry_count") or 0) + 1, status="active", activated_at=now,
            blocked_reason=None, automation_status=None, updated_at=now))
        c.execute(workflow_events.insert().values(
            workflow_instance_id=step["workflow_instance_id"], workflow_step_id=step_id,
            event_type="step_retried", idempotency_key=f"retry:{step_id}:{uuid.uuid4().hex}",
            payload={"retry_count": (step.get("retry_count") or 0) + 1}, actor_user_id=actor_user_id,
            occurred_at=now))
        return dict(c.execute(select(workflow_steps).where(workflow_steps.c.id == step_id)).mappings().one())


def assign_step(principal, step_id: int, user_id: int, *, actor_user_id) -> dict:
    with engine.begin() as c:
        step = _step_instance(c, step_id)
        _load_scoped(c, principal, step["workflow_instance_id"])
        if c.scalar(select(users.c.id).where(users.c.id == user_id)) is None:
            raise WorkflowError("assignee is not a user")
        now = _now()
        c.execute(workflow_steps.update().where(workflow_steps.c.id == step_id)
                  .values(assigned_user_id=user_id, updated_at=now))
        c.execute(workflow_events.insert().values(
            workflow_instance_id=step["workflow_instance_id"], workflow_step_id=step_id,
            event_type="step_assigned", idempotency_key=f"assign:{step_id}:{uuid.uuid4().hex}",
            payload={"assigned_user_id": user_id}, actor_user_id=actor_user_id, occurred_at=now))
        return dict(c.execute(select(workflow_steps).where(workflow_steps.c.id == step_id)).mappings().one())


def audit_history(principal, instance_id: int) -> list[dict]:
    with engine.connect() as c:
        _load_scoped(c, principal, instance_id)
        return [dict(e) for e in c.execute(
            select(workflow_events).where(workflow_events.c.workflow_instance_id == instance_id)
            .order_by(workflow_events.c.occurred_at)).mappings()]


def metrics(principal) -> dict:
    return wf.workflow_metrics()
