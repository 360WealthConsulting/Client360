"""Shared helpers for the Workflow Orchestration Engine (Phase D.33) — events, audit, timeline.

Orchestration lifecycle events are recorded to the append-only ``orchestration_events`` ledger (which
makes an instance deterministically replayable) and the shared ``audit_events`` hash-chain (reusing the
D.25 audit). Only MAJOR lifecycle events publish to the client timeline (launched / stage completed /
approval granted / cancelled / compensated / completed / failed) and only when the instance carries a
person/household anchor — routine transition evaluations are never recorded individually. The engine
never bypasses RBAC/scope (routes gate every surface).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import orchestration_events


class OrchestrationError(Exception):
    """Validation or orchestration error (never raised into a background/coordination path)."""


class OrchestrationNotFound(Exception):
    """Entity not found."""


def now():
    return datetime.now(UTC)


def as_json(payload):
    import json
    return json.loads(json.dumps(payload or {}, default=str))


# Major lifecycle events that publish to the client timeline (when the instance is client-anchored).
TIMELINE_EVENTS = {"launched", "stage_completed", "approval_granted", "cancelled", "compensated",
                   "completed", "failed"}


def next_seq(c, instance_id: int) -> int:
    return (c.scalar(select(func.max(orchestration_events.c.seq)).where(
        orchestration_events.c.instance_id == instance_id)) or 0) + 1


def record_event(c, *, instance_id, event_type, from_stage=None, to_stage=None, action=None,
                 policy_decision=None, runtime_snapshot_id=None, payload=None, actor_user_id=None):
    """Append one immutable event to the orchestration ledger (deterministic replay source)."""
    c.execute(orchestration_events.insert().values(
        instance_id=instance_id, seq=next_seq(c, instance_id), event_type=event_type,
        from_stage=from_stage, to_stage=to_stage, action=action,
        policy_decision=as_json(policy_decision) if policy_decision is not None else None,
        runtime_snapshot_id=runtime_snapshot_id, payload=as_json(payload) if payload is not None else None,
        actor_user_id=actor_user_id, occurred_at=now()))


def write_audit(action, *, entity_type="orchestration_instance", entity_id, actor_user_id=None,
                metadata=None):
    """Record an orchestration lifecycle action in the shared tamper-evident audit hash-chain."""
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type=entity_type, entity_id=str(entity_id),
                          actor_user_id=actor_user_id, request_id=str(uuid.uuid4()), metadata=metadata or {})
    except Exception:
        pass


def publish_timeline(instance: dict, event_type: str, *, title=None):
    """Publish a MAJOR orchestration lifecycle event to the shared timeline — only for the approved
    event set and only when the instance carries a person/household anchor (the timeline requires one).
    Routine transition evaluations are never published."""
    if event_type not in TIMELINE_EVENTS:
        return
    if not instance.get("person_id") and not instance.get("household_id"):
        return
    try:
        import uuid

        from app.services.timeline import add_timeline_event
        add_timeline_event(
            source="orchestration", event_type=f"orchestration_{event_type}",
            title=title or f"Workflow {event_type.replace('_', ' ')}",
            person_id=instance.get("person_id"), household_id=instance.get("household_id"),
            external_id=f"orchestration-{instance['id']}-{event_type}-{uuid.uuid4().hex}",
            event_metadata={"definition": instance.get("definition_code"), "event": event_type})
    except Exception:
        pass
