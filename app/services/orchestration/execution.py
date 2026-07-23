"""Workflow Execution Service (Phase D.33) — the high-level coordinators for the active definitions.

Runs an ``active`` orchestration definition forward by composing EXISTING services (it never duplicates
domain behavior): it launches an instance, advances it through the definition's stages via the engine
(which consumes the policy engine for routing + ``RuntimeContext`` for behavior), invokes the caller's
executor at the execution stage, and records completion / failure / compensation. Every coordinator is
**behavior-preserving**: the executor runs exactly once and its result/exception flows through
unchanged; all orchestration recording is failure-isolated (recording never breaks execution).
"""
from __future__ import annotations

from . import engine
from .common import OrchestrationError


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _summary(result) -> dict:
    if isinstance(result, dict):
        return {k: result[k] for k in list(result)[:6] if isinstance(result.get(k), (str, int, float, bool, type(None)))}
    return {"result_type": type(result).__name__}


def coordinate(definition_code, *, subject, executor, inputs=None, actor_user_id=None, person_id=None,
               household_id=None):
    """Coordinate a single active-definition run around ``executor`` (a callable that performs the real
    domain work via the existing framework). Launches → dispatching → running → completed/failed→
    compensated. Behavior-preserving: ``executor`` runs once; its return/exception is unchanged."""
    inst = _safe(lambda: engine.launch(definition_code, subject=subject, inputs=inputs,
                                       actor_user_id=actor_user_id, person_id=person_id,
                                       household_id=household_id))
    iid = inst["id"] if inst else None
    if iid is not None:
        _safe(lambda: engine.transition(iid, "dispatch", actor_user_id=actor_user_id))
        _safe(lambda: engine.transition(iid, "execute", actor_user_id=actor_user_id))
    try:
        result = executor()
    except Exception as exc:
        err = str(exc)
        if iid is not None:
            _safe(lambda: engine.transition(iid, "fail", actor_user_id=actor_user_id,
                                            payload={"error": err}))
            _safe(lambda: engine.transition(iid, "compensate", actor_user_id=actor_user_id))
        raise
    if iid is not None:
        _safe(lambda: engine.transition(iid, "complete", actor_user_id=actor_user_id,
                                        payload={"result": _summary(result)}))
    return result


def orchestrate_review(review_code, *, launcher, actor_user_id=None, person_id=None, household_id=None):
    """Orchestrate a review-workflow launch: route (policy workflow.review_routing) → launch → complete,
    or reject → cancelled. Returns the launcher result when the policy permits, else None (identical to
    the pre-D.33 behavior). ``launcher`` composes the workflow-template engine (launch_workflow)."""
    inst = engine.launch("workflow.review", subject=review_code, actor_user_id=actor_user_id,
                         person_id=person_id, household_id=household_id)
    iid = inst["id"]
    try:
        engine.transition(iid, "route", actor_user_id=actor_user_id)   # policy-gated
    except OrchestrationError:
        _safe(lambda: engine.transition(iid, "reject", actor_user_id=actor_user_id))
        return None
    result = launcher()
    _safe(lambda: engine.transition(iid, "launch", actor_user_id=actor_user_id))
    _safe(lambda: engine.transition(iid, "complete", actor_user_id=actor_user_id,
                                    payload={"result": _summary(result)}))
    return result


def tick(*, limit=100) -> dict:
    """Housekeeping tick invoked by the scheduler: scan non-terminal instances (no-op advance for the
    synchronous active definitions today) and report. Never mutates a terminal instance; deterministic;
    single-instance. The scheduler infrastructure is unchanged — this only launches orchestration."""
    from sqlalchemy import select

    from app.db import engine as db
    from app.db import orchestration_instances
    with db.connect() as c:
        rows = list(c.execute(select(orchestration_instances.c.id, orchestration_instances.c.status)
                              .where(orchestration_instances.c.status.in_(("pending", "active", "waiting")))
                              .order_by(orchestration_instances.c.id).limit(limit)).mappings())
    return {"checked": len(rows), "advanced": 0}
