"""Workflow event publication (F4.3 / Epic 4, ADR-016).

Publishes a canonical F1.4 event ``Envelope`` over the F1.3 transactional outbox
for each workflow **lifecycle transition**, so downstream consumers can observe
state changes. Per ADR-016, events are **notifications of state changes — never
drivers of them**: this module only *emits*; it registers no subscribers, changes
no workflow state, and performs no advancement, automation, or SLA processing.

Design:
- **Emitted inside the engine's transaction.** The engine passes its connection to
  ``emit_transition_event``, so the outbox row commits atomically with the state
  change (the transactional-outbox guarantee) — an event exists iff the transition
  committed.
- **Deterministic, idempotent.** The envelope ``event_id`` is derived
  deterministically from the domain ``workflow_events`` row id, so re-emitting the
  same transition yields the same id. Emission is a no-op if already published, and
  the outbox ``uq_outbox_events_event_id`` constraint is the backstop — exactly one
  event per transition, duplicates prevented.
- **Reference-only.** Payloads/metadata carry references (ids, states, actor id) —
  never PII/return data (Constitution §9).

Scope (F4.3): lifecycle-transition publication only. Step-level, approval, and SLA
event types, and any subscribers/reactions, are deferred (later features). The
``subscribe(...)`` seam of the outbox is the documented extension point for those.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.platform.events import new_event
from app.platform.outbox import outbox_events, publish_event

#: Stable namespace for deriving deterministic workflow envelope event ids.
WORKFLOW_EVENT_NAMESPACE = uuid.UUID("f4030000-0000-4000-8000-000000000001")

EVENT_TYPE_PREFIX = "workflow"

#: Lifecycle action -> canonical event-type suffix (ADR-016 §11 taxonomy).
TRANSITION_EVENT_TYPES: dict[str, str] = {
    "launch": "launched",
    "pause": "paused",
    "resume": "resumed",
    "cancel": "cancelled",
    "complete": "completed",
    "reopen": "reopened",
}


def transition_event_type(action: str) -> str:
    """Canonical event type for a lifecycle action, e.g. ``pause`` -> ``workflow.paused``."""
    return f"{EVENT_TYPE_PREFIX}.{TRANSITION_EVENT_TYPES.get(action, action)}"


def workflow_event_id(domain_event_id: int) -> str:
    """Deterministic envelope ``event_id`` for a ``workflow_events`` row (idempotency)."""
    return str(uuid.uuid5(WORKFLOW_EVENT_NAMESPACE, f"workflow_event:{domain_event_id}"))


def emit_transition_event(
    conn, *, instance_id: int, action: str, domain_event_id: int,
    actor_user_id: int | None = None, correlation_id: str | None = None,
    payload_extra: dict | None = None, metadata_extra: dict | None = None,
) -> str:
    """Publish exactly one F1.4 envelope for a lifecycle transition (idempotent).

    Notification only — never changes workflow state. Uses the caller's ``conn`` so
    the event commits atomically with the transition. Returns the envelope event_id.
    """
    event_id = workflow_event_id(domain_event_id)
    # Idempotent: skip if already published (outbox unique constraint is the backstop).
    if conn.execute(select(outbox_events.c.id).where(outbox_events.c.event_id == event_id)).first():
        return event_id

    payload = {"workflow_instance_id": instance_id, "action": action}
    if payload_extra:
        payload.update(payload_extra)  # references only
    metadata = {"domain_event_id": domain_event_id}
    if actor_user_id is not None:
        metadata["actor_user_id"] = actor_user_id
    if metadata_extra:
        metadata.update(metadata_extra)

    envelope = new_event(
        transition_event_type(action), payload,
        event_id=event_id,
        subject_ref=f"workflow_instance:{instance_id}",
        producer="workflow.execution",
        correlation_id=correlation_id or f"workflow_instance:{instance_id}",
        metadata=metadata,
    )
    return publish_event(conn, envelope)
