"""Shared helpers for the Reporting composition layer (Phase D.21) — scope, audit, timeline.

Reporting is a composition layer: most metadata (dashboards, definitions, templates, scorecards,
KPI groups, schedules, export profiles) is firm-level config gated by the ``reporting.*``
capability, and the KPI VALUES it renders are automatically scoped to the principal's book by the
Analytics ``compute_metric`` layer. Report RUNS may carry an optional client anchor; those enforce
record scope. The audit ledger (``reporting_events``) is append-only and polymorphic. Timeline
publication is guarded — the shared timeline requires a person/household anchor, so firm-level
reporting events record only to the ledger; client-anchored report runs also reach the timeline
(source ``reporting``).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import engine, reporting_events
from app.security.authorization import record_in_scope

# Approved reporting lifecycle events that publish to the shared timeline (client-anchored only).
_TIMELINE_EVENTS = {"report_created": "reporting_report_created",
                    "report_generated": "reporting_scheduled_report_generated"}


class ReportingError(Exception):
    """Validation or lifecycle error."""


class ReportingNotFound(Exception):
    """Entity not found or out of scope."""


def now():
    return datetime.now(UTC)


def as_json(payload):
    return json.loads(json.dumps(payload or {}, default=str))


def report_visible(principal, row: dict) -> bool:
    """A report run with a client anchor requires record scope; firm reports are visible to any
    ``reporting.view`` holder."""
    if principal.can("record.read_all"):
        return True
    if row.get("person_id") and record_in_scope(principal, "person", row["person_id"]):
        return True
    if row.get("household_id") and record_in_scope(principal, "household", row["household_id"]):
        return True
    return not (row.get("person_id") or row.get("household_id"))


def require_anchor_write(principal, *, person_id=None, household_id=None):
    if person_id is not None and not record_in_scope(principal, "person", person_id, write=True):
        raise ReportingError("person not in write scope")
    if household_id is not None and not record_in_scope(principal, "household", household_id, write=True):
        raise ReportingError("household not in write scope")


def record_event(c, *, entity_type, entity_id, event_type, actor_user_id=None, payload=None):
    c.execute(reporting_events.insert().values(
        entity_type=entity_type, entity_id=entity_id, event_type=event_type,
        actor_user_id=actor_user_id, payload=as_json(payload), occurred_at=now()))


def publish_timeline(report_row: dict, kind: str):
    """Publish an approved reporting lifecycle event to the shared timeline — but only when the
    report run carries a client anchor (the timeline requires person_id/household_id)."""
    event_type = _TIMELINE_EVENTS.get(kind)
    if event_type is None:
        return
    if not report_row.get("person_id") and not report_row.get("household_id"):
        return
    try:
        from app.services.timeline import add_timeline_event
        add_timeline_event(
            source="reporting", event_type=event_type,
            title=report_row.get("name") or "Report", summary=(report_row.get("category") or ""),
            person_id=report_row.get("person_id"), household_id=report_row.get("household_id"),
            external_id=f"reporting-{kind}-{report_row['id']}",
            event_metadata={"report_id": report_row["id"], "kind": kind})
    except Exception:
        pass


def audit_history(principal, *, entity_type, entity_id) -> list[dict]:
    with engine.connect() as c:
        return [dict(e) for e in c.execute(
            select(reporting_events).where(reporting_events.c.entity_type == entity_type,
                                           reporting_events.c.entity_id == entity_id)
            .order_by(reporting_events.c.occurred_at, reporting_events.c.id)).mappings()]
