"""Shared helpers for the Operations domain (Phase D.20) — scope, audit ledger, timeline.

Projects and operational tasks share the same optional-anchor scope model: a firm-level item (no
person/household/organization anchor) is visible to ``operations.view`` holders; a client-anchored
item additionally requires record scope. The audit ledger (``operations_events``) is append-only
and polymorphic. Timeline publication is guarded — the shared timeline requires a person/household
anchor, so firm-only items skip it (source ``operations``).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from app.db import engine, operations_events, people, record_assignments
from app.security.authorization import (
    accessible_person_ids,
    organization_in_scope,
    record_in_scope,
    team_ids,
)

# Approved operational lifecycle events that publish to the shared timeline (client-anchored only).
_TIMELINE_EVENTS = {"project_created": "operations_project_created",
                    "project_completed": "operations_project_completed",
                    "task_completed": "operations_task_completed",
                    "milestone_reached": "operations_milestone_reached"}


class OperationsError(Exception):
    """Validation or lifecycle error."""


class OperationsNotFound(Exception):
    """Entity not found or out of scope."""


def now():
    return datetime.now(UTC)


def as_json(payload):
    return json.loads(json.dumps(payload or {}, default=str))


def accessible_org_ids(c, principal) -> set[int]:
    tids = team_ids(c, principal)
    conds = [record_assignments.c.user_id == principal.user_id]
    if tids:
        conds.append(record_assignments.c.team_id.in_(tuple(tids)))
    rows = c.scalars(select(record_assignments.c.entity_id).where(
        record_assignments.c.entity_type == "organization", or_(*conds)))
    return {r for r in rows if r is not None}


def scope_clause(table, principal, c):
    """SQL predicate limiting ``table`` (which has person_id/household_id/organization_id) to the
    principal's scope. ``None`` == unrestricted (record.read_all)."""
    if principal.can("record.read_all"):
        return None
    conds = [and_(table.c.person_id.is_(None), table.c.household_id.is_(None),
                  table.c.organization_id.is_(None))]        # firm-level items
    ids = accessible_person_ids(c, principal)
    if ids:
        conds.append(table.c.person_id.in_(tuple(ids)))
        hh = set(c.scalars(select(people.c.household_id).where(
            people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))
        if hh:
            conds.append(table.c.household_id.in_(tuple(hh)))
    orgs = accessible_org_ids(c, principal)
    if orgs:
        conds.append(table.c.organization_id.in_(tuple(orgs)))
    return or_(*conds)


def visible(principal, row: dict, c) -> bool:
    if principal.can("record.read_all"):
        return True
    if row.get("person_id") and record_in_scope(principal, "person", row["person_id"], connection=c):
        return True
    if row.get("household_id") and record_in_scope(principal, "household", row["household_id"], connection=c):
        return True
    if row.get("organization_id") and organization_in_scope(principal, row["organization_id"], connection=c):
        return True
    return not (row.get("person_id") or row.get("household_id") or row.get("organization_id"))


def can_write(principal, row: dict, c) -> bool:
    if principal.can("record.write_all") or principal.can("record.read_all"):
        return True
    if row.get("person_id") and record_in_scope(principal, "person", row["person_id"], write=True, connection=c):
        return True
    if row.get("household_id") and record_in_scope(principal, "household", row["household_id"], write=True, connection=c):
        return True
    if row.get("organization_id") and organization_in_scope(principal, row["organization_id"], write=True, connection=c):
        return True
    return not (row.get("person_id") or row.get("household_id") or row.get("organization_id"))


def require_anchor_write(principal, *, person_id=None, household_id=None, organization_id=None):
    """A caller may only anchor a firm item to a client record they can write."""
    if person_id is not None and not record_in_scope(principal, "person", person_id, write=True):
        raise OperationsError("person not in write scope")
    if household_id is not None and not record_in_scope(principal, "household", household_id, write=True):
        raise OperationsError("household not in write scope")
    if organization_id is not None and not organization_in_scope(principal, organization_id, write=True):
        raise OperationsError("organization not in write scope")


def record_event(c, *, entity_type, entity_id, event_type, project_id=None, from_status=None,
                 to_status=None, actor_user_id=None, payload=None):
    c.execute(operations_events.insert().values(
        entity_type=entity_type, entity_id=entity_id, project_id=project_id, event_type=event_type,
        from_status=from_status, to_status=to_status, actor_user_id=actor_user_id,
        payload=as_json(payload), occurred_at=now()))


def publish_timeline(row: dict, kind: str):
    """Publish an approved operational lifecycle event to the shared timeline — but only when the
    item carries a client anchor (the timeline requires person_id/household_id)."""
    event_type = _TIMELINE_EVENTS.get(kind)
    if event_type is None:
        return
    if not row.get("person_id") and not row.get("household_id"):
        return
    try:
        from app.services.timeline import add_timeline_event
        add_timeline_event(
            source="operations", event_type=event_type,
            title=row.get("name") or row.get("title") or "Operations",
            summary=(row.get("category") or ""), person_id=row.get("person_id"),
            household_id=row.get("household_id"),
            external_id=f"operations-{kind}-{row['id']}",
            event_metadata={"kind": kind, "id": row["id"]})
    except Exception:
        pass


def audit_history(principal, *, entity_type, entity_id) -> list[dict]:
    with engine.connect() as c:
        return [dict(e) for e in c.execute(
            select(operations_events).where(operations_events.c.entity_type == entity_type,
                                            operations_events.c.entity_id == entity_id)
            .order_by(operations_events.c.occurred_at, operations_events.c.id)).mappings()]
