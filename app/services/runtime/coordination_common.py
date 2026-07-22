"""Shared helpers for the Phase D.29 distributed runtime coordination layer.

Coordination lifecycle events record to the append-only ``runtime_coordination_events`` ledger + the
shared ``audit_events`` hash-chain. Coordination is firm-level (no client anchor), so nothing is
published to the client timeline; individual worker heartbeats are NEVER recorded as events (only
major lifecycle events). This layer owns no configuration metadata and performs no evaluation.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import engine, runtime_coordination_events

# Major coordination lifecycle events (recorded to the ledger; never individual heartbeats).
COORDINATION_EVENTS = ("cluster_initialized", "worker_joined", "worker_removed", "refresh_requested",
                       "refresh_completed", "convergence_achieved", "emergency_synchronization",
                       "snapshot_activated", "cache_invalidated")


def now():
    return datetime.now(UTC)


def as_json(payload):
    return json.loads(json.dumps(payload or {}, default=str))


def record_coordination_event(c, *, entity_type, entity_id, event_type, from_status=None,
                              to_status=None, worker_uid=None, actor_user_id=None, payload=None):
    c.execute(runtime_coordination_events.insert().values(
        entity_type=entity_type, entity_id=entity_id, event_type=event_type, from_status=from_status,
        to_status=to_status, worker_uid=worker_uid, actor_user_id=actor_user_id,
        payload=as_json(payload), occurred_at=now()))


def write_audit(action, *, entity_type, entity_id, actor_user_id=None, metadata=None):
    """Record coordination actions in the shared tamper-evident audit hash-chain (references only)."""
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type=entity_type, entity_id=str(entity_id),
                          actor_user_id=actor_user_id, request_id=str(uuid.uuid4()),
                          metadata=metadata or {})
    except Exception:
        pass


def coordination_history(principal=None, *, entity_type=None, event_type=None, limit=100) -> list[dict]:
    with engine.connect() as c:
        stmt = select(runtime_coordination_events).order_by(runtime_coordination_events.c.id.desc())
        if entity_type:
            stmt = stmt.where(runtime_coordination_events.c.entity_type == entity_type)
        if event_type:
            stmt = stmt.where(runtime_coordination_events.c.event_type == event_type)
        return [dict(r) for r in c.execute(stmt.limit(min(500, max(1, limit)))).mappings()]
