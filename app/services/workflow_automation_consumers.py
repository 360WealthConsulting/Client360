"""Workflow automation consumers (F4.4 / Epic 4, ADR-016).

Event-driven automation: subscribers consume the workflow **lifecycle events**
published by F4.3 (via the F1.3 outbox) and execute **configured automation
actions**. This is the first *consumer* layer of Epic 4.

Direction is strictly one-way (ADR-016): **automation consumes workflow events;
workflow events never consume automation.** Automation actions run through the
existing idempotent ledger (``execute_automation_action``) and **never change
workflow state** — they do not launch, transition, complete, or advance workflows.
Workflow execution remains the source of truth.

Guarantees:
- **Event-driven:** actions run only in response to a published lifecycle event.
- **Exactly once / duplicate-safe:** the outbox records ``(event_id, consumer)`` in
  ``outbox_processed_events`` (a handler is never re-run for an event it already
  processed), and each action uses a deterministic ``idempotency_key`` so a redelivery
  cannot re-execute it — two independent idempotency layers.
- **Retry-safe:** a failing action raises; the outbox backs off and retries, then
  dead-letters after ``MAX_ATTEMPTS`` (operator-visible). Because actions are
  idempotent, retries never double-execute.
- **Independent of state transitions:** no workflow status is read for control flow
  and none is written.

Activation is **dark-launched**: consumers are registered only when the outbox
dispatcher is enabled (see ``app/jobs/scheduler.py``), so default runtime behavior —
and F4.3's "no subscribers registered" guarantee — is unchanged until explicitly
enabled.

Out of scope (later features): approval routing, SLA processing, UI, workflow state
transitions, and new capabilities.
"""
from __future__ import annotations

import logging

from app.platform.events import Envelope
from app.platform.outbox import subscribe
from app.platform.workflow_events import TRANSITION_EVENT_TYPES, transition_event_type
from app.services.workflow_automation import execute_automation_action

logger = logging.getLogger("client360.workflow.automation")

#: All workflow lifecycle event types (e.g. ``workflow.launched``, ``workflow.completed``).
WORKFLOW_EVENT_TYPES: tuple[str, ...] = tuple(transition_event_type(a) for a in TRANSITION_EVENT_TYPES)

# event_type -> list of {"action_type": str, "payload": dict}. The configuration seam
# (extension point) for wiring automation to lifecycle events. Empty by default: F4.4
# delivers the mechanism, not domain automations.
_AUTOMATION_REGISTRY: dict[str, list[dict]] = {}


def register_automation(event_type: str, action_type: str, *, payload: dict | None = None) -> None:
    """Configure an automation action to run when ``event_type`` is observed."""
    _AUTOMATION_REGISTRY.setdefault(event_type, []).append(
        {"action_type": action_type, "payload": payload or {}}
    )


def clear_automation_registry() -> None:
    """Remove all configured automations (test/support helper)."""
    _AUTOMATION_REGISTRY.clear()


def configured_actions(event_type: str) -> list[dict]:
    """The automation actions configured for an event type (a copy)."""
    return [dict(spec) for spec in _AUTOMATION_REGISTRY.get(event_type, [])]


def on_workflow_event(event: Envelope) -> None:
    """Idempotent, retry-safe consumer: run configured actions for a lifecycle event.

    Never changes workflow state. Exceptions propagate so the outbox can retry and,
    ultimately, dead-letter — actions are idempotent, so retries never double-run.
    """
    actions = configured_actions(event.event_type)
    if not actions:
        return
    instance_id = event.payload.get("workflow_instance_id")
    if instance_id is None:
        return
    for index, spec in enumerate(actions):
        action_type = spec["action_type"]
        idempotency_key = f"auto:{event.event_id}:{index}:{action_type}"
        execute_automation_action(
            instance_id, action_type, payload=spec["payload"], idempotency_key=idempotency_key
        )
        logger.info(
            "workflow automation executed",
            extra={"event_id": event.event_id, "event_type": event.event_type,
                   "action_type": action_type, "workflow_instance_id": instance_id},
        )


def register_workflow_consumers() -> None:
    """Subscribe the automation consumer to every workflow lifecycle event type.

    Idempotent (the outbox subscribe registry de-duplicates). Called from the
    scheduler only when the outbox dispatcher is enabled (dark launch).
    """
    for event_type in WORKFLOW_EVENT_TYPES:
        subscribe(event_type, on_workflow_event)
