"""The Workflow Orchestration Engine (Phase D.33) — deterministic state-driven coordination.

The single place an orchestration instance is launched and transitioned. It resolves every transition
through the pure state manager, **consumes the Runtime Policy Engine** for routing (a transition may
declare a ``policy`` code — the engine evaluates it and records the decision) and **consumes
``RuntimeContext``** for behavior (never evaluating runtime configuration directly — the runtime engine
remains the sole evaluator; never making business decisions — the policy engine remains the sole
decision engine). Every launch/transition appends to the append-only ``orchestration_events`` ledger
(deterministic replay), records the runtime snapshot, and publishes only MAJOR lifecycle events to the
timeline. In-process counters feed observability/analytics; routine transition evaluations are never
logged individually.
"""
from __future__ import annotations

import threading

from sqlalchemy import select

from app.db import engine as db
from app.db import orchestration_instances

from . import state
from .common import (
    OrchestrationError,
    OrchestrationNotFound,
    now,
    publish_timeline,
    record_event,
)
from .context import WorkflowContext
from .definitions import get_definition

_lock = threading.RLock()
_STATS = {"launches": 0, "transitions": 0, "completions": 0, "failures": 0, "cancellations": 0,
          "compensations": 0, "retries": 0, "blocked": 0, "replays": 0, "simulations": 0,
          "total_duration_ms": 0}


def _note(kind: str, n: int = 1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + n


def _instance(c, instance_id, *, lock=False):
    stmt = select(orchestration_instances).where(orchestration_instances.c.id == instance_id)
    if lock:
        stmt = stmt.with_for_update()
    row = c.execute(stmt).mappings().first()
    if row is None:
        raise OrchestrationNotFound(f"orchestration instance {instance_id}")
    return dict(row)


def launch(definition_code, *, subject=None, inputs=None, runtime=None, person_id=None,
           household_id=None, actor_user_id=None, idempotency_key=None) -> dict:
    """Launch an orchestration instance for a definition. Deterministic; binds the runtime snapshot;
    records a ``launched`` event. Idempotent on ``idempotency_key``."""
    definition = get_definition(definition_code)
    if definition is None:
        raise OrchestrationError(f"unknown orchestration definition {definition_code!r}")
    if definition.status not in ("active",):
        raise OrchestrationError(
            f"definition {definition_code!r} is {definition.status} — its lifecycle is authoritative "
            f"in its owning domain and is not driven by the engine")
    wctx = WorkflowContext.build(definition_code, subject=subject, inputs=inputs, runtime=runtime,
                                 person_id=person_id, household_id=household_id, actor_user_id=actor_user_id)
    initial_kind = state.stage_kind(definition, definition.initial_stage) or "pending"
    with db.begin() as c:
        if idempotency_key:
            existing = c.execute(select(orchestration_instances).where(
                orchestration_instances.c.idempotency_key == idempotency_key)).mappings().first()
            if existing:
                return dict(existing)
        row = dict(c.execute(orchestration_instances.insert().values(
            definition_code=definition_code, subject=subject, status=initial_kind,
            current_stage=definition.initial_stage, runtime_snapshot_id=wctx.runtime_snapshot_id,
            context=wctx.to_dict(), person_id=person_id, household_id=household_id,
            idempotency_key=idempotency_key, launched_by_user_id=actor_user_id).returning(
                *orchestration_instances.c)).mappings().one())
        record_event(c, instance_id=row["id"], event_type="launched", to_stage=definition.initial_stage,
                     action="launch", runtime_snapshot_id=wctx.runtime_snapshot_id,
                     payload={"subject": subject}, actor_user_id=actor_user_id)
    _note("launches")
    # The append-only orchestration_events ledger (written above) is the lifecycle record; the shared
    # audit hash-chain is reserved for low-frequency admin/governance actions (registry lifecycle +
    # governance validation) — routine launches/transitions are never individually written to it (the
    # same posture as the automation framework), avoiding audit-volume + user-FK coupling.
    # (D.34) Publish a domain event so downstream modules can react asynchronously through the event bus
    # rather than the engine invoking them directly. Additive + best-effort (the outbox dispatcher is
    # gated off by default, so behavior is unchanged until a consumer is enabled).
    _publish_domain_event(row, "launched")
    publish_timeline({**row, "definition_code": definition_code}, "launched",
                     title=f"{definition.name} launched")
    return row


def _publish_domain_event(inst: dict, event: str):
    """Emit the ``orchestration.lifecycle`` domain event (best-effort; never breaks the engine)."""
    from app.services.events import publisher
    publisher.publish_safe(
        "orchestration.lifecycle",
        {"instance_id": inst["id"], "definition": inst.get("definition_code") or "",
         "event": event, "stage": inst.get("current_stage") or ""},
        producer="orchestration.engine", subject_ref=f"orchestration:{inst['id']}")


def _evaluate_route(definition, transition, wctx) -> tuple[bool, dict | None]:
    """Consume the Runtime Policy Engine for a policy-gated transition; return (permitted, decision)."""
    policy_code = transition.get("policy")
    if not policy_code:
        return True, None
    from app.services.policy import evaluate as policy_evaluate
    result = policy_evaluate(policy_code, subject=wctx.subject, context=wctx.runtime)
    return bool(result.decision), result.to_dict()


def transition(instance_id, action, *, actor_user_id=None, payload=None, runtime=None) -> dict:
    """Advance an instance by an action. Resolves the transition through the state manager, consumes the
    policy engine for a policy-gated route, records the event (+ policy decision), and publishes major
    lifecycle events. Raises ``OrchestrationError`` on an invalid or policy-denied transition."""
    blocked = None
    with db.begin() as c:
        inst = _instance(c, instance_id, lock=True)
        definition = get_definition(inst["definition_code"])
        if definition is None:
            raise OrchestrationError(f"unknown definition {inst['definition_code']!r}")
        from_stage = inst["current_stage"]
        tr = state.transition_for(definition, from_stage, action)
        if tr is None:
            raise OrchestrationError(f"cannot {action!r} from stage {from_stage!r} in {definition.code!r}")
        wctx = WorkflowContext.build(definition.code, subject=inst.get("subject"), runtime=runtime,
                                     person_id=inst.get("person_id"), household_id=inst.get("household_id"),
                                     actor_user_id=actor_user_id)
        permitted, decision = _evaluate_route(definition, tr, wctx)
        if not permitted:
            # exit this transaction WITHOUT mutating (no change was made); record the block durably in a
            # separate transaction below so the block survives the raise (a raise here would roll it back).
            blocked = (from_stage, action, tr.get("policy"), decision, wctx.runtime_snapshot_id)
            updated = inst
        else:
            to_stage = tr["to"]
            to_kind = state.stage_kind(definition, to_stage) or "active"
            values = {"status": to_kind, "current_stage": to_stage, "updated_at": now()}
            terminal = to_kind in state.TERMINAL_STATES or to_kind == "failed"
            if terminal:
                values["completed_at"] = now()
                created = inst.get("created_at")
                if created is not None:
                    _note("total_duration_ms", max(0, int((now() - created).total_seconds() * 1000)))
            if payload and payload.get("error"):
                values["last_error"] = str(payload["error"])[:2000]
            c.execute(orchestration_instances.update().where(
                orchestration_instances.c.id == instance_id).values(**values))
            event_type = _event_type_for(to_kind, to_stage, definition)
            record_event(c, instance_id=instance_id, event_type=event_type, from_stage=from_stage,
                         to_stage=to_stage, action=action, policy_decision=decision,
                         runtime_snapshot_id=wctx.runtime_snapshot_id, payload=payload, actor_user_id=actor_user_id)
            updated = _instance(c, instance_id)
    if blocked is not None:
        b_from, b_action, b_policy, b_decision, b_snap = blocked
        with db.begin() as c:
            record_event(c, instance_id=instance_id, event_type="transition_blocked", from_stage=b_from,
                         action=b_action, policy_decision=b_decision, runtime_snapshot_id=b_snap,
                         actor_user_id=actor_user_id)
        _note("blocked")
        raise OrchestrationError(f"policy {b_policy!r} denied the {b_action!r} transition")
    return _finish_transition(definition, updated, from_stage, actor_user_id)


def _finish_transition(definition, updated, from_stage, actor_user_id):
    """Post-commit bookkeeping for a successful transition: counters, audit, and the major-event
    timeline publish (routine transitions are not published)."""
    to_stage = updated["current_stage"]
    to_kind = updated["status"]
    event_type = _event_type_for(to_kind, to_stage, definition)
    _note("transitions")
    if to_kind == "completed":
        _note("completions")
    elif to_kind == "failed":
        _note("failures")
    elif to_kind == "cancelled":
        _note("cancellations")
    elif to_kind == "compensated":
        _note("compensations")
    # The append-only orchestration_events ledger is the lifecycle record; routine transitions are not
    # written to the shared audit hash-chain individually (see launch()). Only major lifecycle events
    # publish to the client timeline (and only for client-anchored instances).
    # (D.34) On a terminal outcome, publish the orchestration.lifecycle domain event (best-effort).
    if to_kind in ("completed", "failed", "cancelled", "compensated"):
        _publish_domain_event({**updated, "definition_code": definition.code}, event_type)
    publish_timeline({**updated, "definition_code": definition.code}, event_type,
                     title=f"{definition.name}: {to_stage}")
    return updated


def _event_type_for(to_kind, to_stage, definition) -> str:
    if to_kind == "completed":
        return "completed"
    if to_kind == "cancelled":
        return "cancelled"
    if to_kind == "failed":
        return "failed"
    if to_kind == "compensated":
        return "compensated"
    if to_stage in (definition.completion_stages or ()):
        return "stage_completed"
    return "stage_completed" if to_kind == "active" else f"entered_{to_kind}"


def get_instance(instance_id) -> dict | None:
    with db.connect() as c:
        row = c.execute(select(orchestration_instances).where(
            orchestration_instances.c.id == instance_id)).mappings().first()
        return dict(row) if row else None


def list_instances(*, definition_code=None, status=None, limit=100) -> list[dict]:
    with db.connect() as c:
        stmt = select(orchestration_instances).order_by(orchestration_instances.c.id.desc())
        if definition_code:
            stmt = stmt.where(orchestration_instances.c.definition_code == definition_code)
        if status:
            stmt = stmt.where(orchestration_instances.c.status == status)
        return [dict(r) for r in c.execute(stmt.limit(min(500, max(1, limit)))).mappings()]


def note_retry():
    _note("retries")


def note_replay():
    _note("replays")


def note_simulation():
    _note("simulations")


def stats() -> dict:
    with _lock:
        s = dict(_STATS)
    total = s["completions"] + s["failures"] + s["cancellations"] + s["compensations"]
    s["terminal_total"] = total
    s["avg_duration_ms"] = round(s["total_duration_ms"] / total, 2) if total else None
    s["success_rate"] = round(s["completions"] / total, 4) if total else None
    return s


def reset_stats():
    with _lock:
        for k in list(_STATS):
            _STATS[k] = 0
