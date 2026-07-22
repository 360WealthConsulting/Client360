"""Shared helpers for the Enterprise Configuration domain (Phase D.27) — scope, audit, timeline.

Configuration owns configuration governance metadata gated by ``configuration.*``. Configuration is
firm-level; organization-scoped preferences and edition assignments enforce ``organization_in_scope``
on write. Firm-level lifecycle events record only to the append-only ``configuration_events`` ledger
+ the shared ``audit_events`` hash-chain (they carry no person/household anchor, so the guarded
timeline publish skips them). Configuration never mutates a canonical record and stores no secrets
(sensitive configuration metadata stays server-side, gated by capability).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import or_, select

from app.db import configuration_events, engine, record_assignments
from app.security.authorization import organization_in_scope, team_ids

# Approved configuration lifecycle events published to the shared timeline (client-anchored only).
# Configuration items are firm-level, so these are recorded to the ledger and the timeline guard
# skips them unless a change ever carries a person/household anchor (none do today).
_TIMELINE_EVENTS = {"configuration_approved": "configuration_approved",
                    "feature_activated": "configuration_feature_activated",
                    "edition_assigned": "configuration_edition_assigned",
                    "configuration_archived": "configuration_archived"}


class ConfigurationError(Exception):
    """Validation or lifecycle error."""


class ConfigurationNotFound(Exception):
    """Entity not found or out of scope."""


def now():
    return datetime.now(UTC)


def as_json(payload):
    return json.loads(json.dumps(payload or {}, default=str))


# --- scope (organization-scoped writes enforce organization_in_scope) -------------------------

def accessible_org_ids(c, principal) -> set[int]:
    tids = team_ids(c, principal)
    conds = [record_assignments.c.user_id == principal.user_id]
    if tids:
        conds.append(record_assignments.c.team_id.in_(tuple(tids)))
    rows = c.scalars(select(record_assignments.c.entity_id).where(
        record_assignments.c.entity_type == "organization", or_(*conds)))
    return {r for r in rows if r is not None}


def require_org_scope_write(principal, organization_id):
    if organization_id is not None and not principal.can("record.write_all") \
            and not organization_in_scope(principal, organization_id, write=True):
        raise ConfigurationError("organization not in write scope")


def org_visible(principal, organization_id) -> bool:
    if organization_id is None or principal.can("record.read_all"):
        return True
    return organization_in_scope(principal, organization_id)


# --- audit ledger + shared audit hash-chain ----------------------------------

def record_event(c, *, entity_type, entity_id, event_type, from_status=None, to_status=None,
                 actor_user_id=None, payload=None):
    c.execute(configuration_events.insert().values(
        entity_type=entity_type, entity_id=entity_id, event_type=event_type, from_status=from_status,
        to_status=to_status, actor_user_id=actor_user_id, payload=as_json(payload), occurred_at=now()))


def write_audit(action, *, entity_type, entity_id, actor_user_id=None, metadata=None):
    """Also record configuration actions in the shared tamper-evident audit hash-chain (references
    only — never a sensitive configuration value)."""
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type=entity_type, entity_id=str(entity_id),
                          actor_user_id=actor_user_id, request_id=str(uuid.uuid4()),
                          metadata=metadata or {})
    except Exception:
        pass


def publish_timeline(row: dict, kind: str, *, title=None, summary=None):
    """Publish an approved configuration lifecycle event — only when the item carries a client anchor
    (the timeline requires person_id/household_id). Configuration items are firm-level, so this is a
    guarded no-op today; firm-level events record to the ``configuration_events`` ledger only. Never
    emitted per setting update."""
    event_type = _TIMELINE_EVENTS.get(kind)
    if event_type is None:
        return
    if not row.get("person_id") and not row.get("household_id"):
        return
    try:
        from app.services.timeline import add_timeline_event
        add_timeline_event(source="configuration", event_type=event_type,
                           title=title or kind.replace("_", " ").title(), summary=summary or "",
                           person_id=row.get("person_id"), household_id=row.get("household_id"),
                           external_id=f"configuration-{kind}-{row['id']}",
                           event_metadata={"kind": kind, "id": row["id"]})
    except Exception:
        pass


def audit_history(principal, *, entity_type, entity_id) -> list[dict]:
    with engine.connect() as c:
        return [dict(e) for e in c.execute(
            select(configuration_events).where(configuration_events.c.entity_type == entity_type,
                                               configuration_events.c.entity_id == entity_id)
            .order_by(configuration_events.c.occurred_at, configuration_events.c.id)).mappings()]
