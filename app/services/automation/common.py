"""Shared helpers for the Automation orchestration domain (Phase D.22).

Automation is firm-level orchestration metadata gated by the ``automation.*`` capability. A run may
carry an optional client anchor; those publish a guarded timeline event. The audit ledger
(``automation_events``) is append-only and polymorphic. Scheduled/system runs execute with a
**system principal** derived from the job's creator holding ``record.read_all`` — firm-level jobs
(snapshot capture, schedule sweeps) require firm-wide reads, and job creation requires
``automation.manage`` (administrator/operations only).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import automation_events, engine
from app.security.models import Principal

# The elevated capability set a scheduled/system automation run executes with.
SYSTEM_CAPS = frozenset({
    "record.read_all", "record.write_all", "automation.execute", "automation.manage",
    "reporting.view", "reporting.manage", "analytics.view", "analytics.executive",
    "workflow.view", "workflow.execute", "communications.view", "communications.send",
    "operations.view", "operations.manage",
})

# Approved execution lifecycle events published to the shared timeline (client-anchored runs only).
_TIMELINE_EVENTS = {"run_started": "automation_job_started",
                    "run_succeeded": "automation_job_completed",
                    "run_failed": "automation_job_failed",
                    "scheduled": "automation_scheduled_execution"}


class AutomationError(Exception):
    """Validation or lifecycle error."""


class AutomationNotFound(Exception):
    """Entity not found."""


def now():
    return datetime.now(UTC)


def as_json(payload):
    return json.loads(json.dumps(payload or {}, default=str))


def system_principal(created_by_user_id: int | None) -> Principal:
    """A system principal for scheduled/system runs (firm-wide reads). Derived from the job's
    creator (an ``automation.manage`` holder)."""
    return Principal(created_by_user_id or 0, "system@automation", "Automation", SYSTEM_CAPS)


def record_event(c, *, entity_type, entity_id, event_type, from_status=None, to_status=None,
                 actor_user_id=None, payload=None):
    c.execute(automation_events.insert().values(
        entity_type=entity_type, entity_id=entity_id, event_type=event_type, from_status=from_status,
        to_status=to_status, actor_user_id=actor_user_id, payload=as_json(payload),
        occurred_at=now()))


def publish_timeline(run_row: dict, kind: str):
    """Publish an approved execution lifecycle event to the shared timeline — but only when the run
    carries a client anchor (the timeline requires person_id/household_id)."""
    event_type = _TIMELINE_EVENTS.get(kind)
    if event_type is None:
        return
    if not run_row.get("person_id") and not run_row.get("household_id"):
        return
    try:
        from app.services.timeline import add_timeline_event
        add_timeline_event(
            source="automation", event_type=event_type,
            title=(run_row.get("job_type") or "Automation job"), summary=kind,
            person_id=run_row.get("person_id"), household_id=run_row.get("household_id"),
            external_id=f"automation-{kind}-{run_row['id']}",
            event_metadata={"run_id": run_row["id"], "job_type": run_row.get("job_type")})
    except Exception:
        pass


def audit_history(principal, *, entity_type, entity_id) -> list[dict]:
    with engine.connect() as c:
        return [dict(e) for e in c.execute(
            select(automation_events).where(automation_events.c.entity_type == entity_type,
                                            automation_events.c.entity_id == entity_id)
            .order_by(automation_events.c.occurred_at, automation_events.c.id)).mappings()]
