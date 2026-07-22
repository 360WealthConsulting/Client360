"""Shared helpers for the Enterprise Observability domain (Phase D.26) — scope, audit, timeline.

Observability owns observability metadata gated by ``observability.*``. Most items are firm-level; a
reliability incident may carry an optional client anchor (``person_id``/``household_id``) that
enforces record scope and can publish a guarded timeline event. Firm-level items are visible to
``observability.view`` holders and record only to the append-only ``observability_events`` ledger +
the shared ``audit_events`` hash-chain. Observability never mutates a canonical record and stores no
secrets (sensitive diagnostic detail stays server-side, gated by capability).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from app.db import engine, observability_events, people, record_assignments
from app.security.authorization import (
    accessible_person_ids,
    organization_in_scope,
    record_in_scope,
    team_ids,
)

# Approved observability lifecycle events published to the shared timeline (client-anchored only).
_TIMELINE_EVENTS = {"service_degraded": "observability_service_degraded",
                    "service_restored": "observability_service_restored",
                    "alert_acknowledged": "observability_alert_acknowledged",
                    "maintenance_started": "observability_maintenance_started",
                    "incident_opened": "observability_incident_opened",
                    "incident_resolved": "observability_incident_resolved"}


class ObservabilityError(Exception):
    """Validation or lifecycle error."""


class ObservabilityNotFound(Exception):
    """Entity not found or out of scope."""


def now():
    return datetime.now(UTC)


def as_json(payload):
    return json.loads(json.dumps(payload or {}, default=str))


# --- scope (optional client anchor; firm-level items visible to observability.view) -----------

def accessible_org_ids(c, principal) -> set[int]:
    tids = team_ids(c, principal)
    conds = [record_assignments.c.user_id == principal.user_id]
    if tids:
        conds.append(record_assignments.c.team_id.in_(tuple(tids)))
    rows = c.scalars(select(record_assignments.c.entity_id).where(
        record_assignments.c.entity_type == "organization", or_(*conds)))
    return {r for r in rows if r is not None}


def scope_clause(table, principal, c):
    """SQL predicate limiting ``table`` (with person_id/household_id) to the principal's scope.
    ``None`` == unrestricted (record.read_all)."""
    if principal.can("record.read_all"):
        return None
    conds = [and_(table.c.person_id.is_(None), table.c.household_id.is_(None))]   # firm-level items
    ids = accessible_person_ids(c, principal)
    if ids:
        conds.append(table.c.person_id.in_(tuple(ids)))
        hh = set(c.scalars(select(people.c.household_id).where(
            people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))
        if hh:
            conds.append(table.c.household_id.in_(tuple(hh)))
    return or_(*conds)


def visible(principal, row: dict) -> bool:
    if principal.can("record.read_all"):
        return True
    if row.get("person_id") and record_in_scope(principal, "person", row["person_id"]):
        return True
    if row.get("household_id") and record_in_scope(principal, "household", row["household_id"]):
        return True
    return not (row.get("person_id") or row.get("household_id"))


def require_anchor_write(principal, *, person_id=None, household_id=None, organization_id=None):
    if person_id is not None and not record_in_scope(principal, "person", person_id, write=True):
        raise ObservabilityError("person not in write scope")
    if household_id is not None and not record_in_scope(principal, "household", household_id, write=True):
        raise ObservabilityError("household not in write scope")
    if organization_id is not None and not organization_in_scope(principal, organization_id, write=True):
        raise ObservabilityError("organization not in write scope")


# --- audit ledger + shared audit hash-chain ----------------------------------

def record_event(c, *, entity_type, entity_id, event_type, from_status=None, to_status=None,
                 actor_user_id=None, payload=None):
    c.execute(observability_events.insert().values(
        entity_type=entity_type, entity_id=entity_id, event_type=event_type, from_status=from_status,
        to_status=to_status, actor_user_id=actor_user_id, payload=as_json(payload), occurred_at=now()))


def write_audit(action, *, entity_type, entity_id, actor_user_id=None, metadata=None):
    """Also record observability actions in the shared tamper-evident audit hash-chain (references
    only — never sensitive diagnostic detail)."""
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type=entity_type, entity_id=str(entity_id),
                          actor_user_id=actor_user_id, request_id=str(uuid.uuid4()),
                          metadata=metadata or {})
    except Exception:
        pass


def publish_timeline(row: dict, kind: str, *, title=None, summary=None):
    """Publish an approved observability lifecycle event — only when the item carries a client anchor
    (the timeline requires person_id/household_id); firm-level observability events skip it (they
    record to the ledger only). Never emitted per health check."""
    event_type = _TIMELINE_EVENTS.get(kind)
    if event_type is None:
        return
    if not row.get("person_id") and not row.get("household_id"):
        return
    try:
        from app.services.timeline import add_timeline_event
        add_timeline_event(source="observability", event_type=event_type,
                           title=title or kind.replace("_", " ").title(), summary=summary or "",
                           person_id=row.get("person_id"), household_id=row.get("household_id"),
                           external_id=f"observability-{kind}-{row['id']}",
                           event_metadata={"kind": kind, "id": row["id"]})
    except Exception:
        pass


def audit_history(principal, *, entity_type, entity_id) -> list[dict]:
    with engine.connect() as c:
        return [dict(e) for e in c.execute(
            select(observability_events).where(observability_events.c.entity_type == entity_type,
                                               observability_events.c.entity_id == entity_id)
            .order_by(observability_events.c.occurred_at, observability_events.c.id)).mappings()]
