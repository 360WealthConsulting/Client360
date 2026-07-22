"""Shared helpers for the Runtime Configuration Engine (Phase D.28) — audit, timeline, hashing.

Runtime lifecycle events are firm-level: they record to the append-only ``runtime_events`` ledger +
the shared ``audit_events`` hash-chain. The guarded timeline publish is a no-op for firm-level events
(the timeline requires a person/household anchor) — runtime events are never client-anchored, and
individual evaluations are NEVER recorded (only major lifecycle events). The engine reuses the D.25
audit hash-chain and never bypasses RBAC/scope (routes gate every surface).
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import engine, runtime_events


class RuntimeConfigError(Exception):
    """Validation or runtime-evaluation error (never raised into the request/startup path)."""


class RuntimeNotFound(Exception):
    """Entity not found."""


def now():
    return datetime.now(UTC)


def as_json(payload):
    return json.loads(json.dumps(payload or {}, default=str))


def canonical_hash(payload) -> str:
    """Deterministic sha256 over a canonical (sorted-key) JSON serialization — used for snapshot
    ``config_hash`` and stale-snapshot / drift detection."""
    canonical = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- append-only ledger + shared audit hash-chain ----------------------------

def record_event(c, *, entity_type, entity_id, event_type, from_status=None, to_status=None,
                 actor_user_id=None, payload=None):
    c.execute(runtime_events.insert().values(
        entity_type=entity_type, entity_id=entity_id, event_type=event_type, from_status=from_status,
        to_status=to_status, actor_user_id=actor_user_id, payload=as_json(payload), occurred_at=now()))


def write_audit(action, *, entity_type, entity_id, actor_user_id=None, metadata=None):
    """Record runtime lifecycle actions in the shared tamper-evident audit hash-chain (references
    only — never a resolved configuration value)."""
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type=entity_type, entity_id=str(entity_id),
                          actor_user_id=actor_user_id, request_id=str(uuid.uuid4()),
                          metadata=metadata or {})
    except Exception:
        pass


# Approved runtime lifecycle events (firm-level → ledger only; guarded timeline publish is a no-op).
_TIMELINE_EVENTS = {"runtime_initialized", "snapshot_created", "snapshot_refreshed",
                    "rollout_activated", "rollout_expired", "cache_rebuilt"}


def publish_timeline(kind: str, *, title=None):
    """Runtime lifecycle events are firm-level (no person/household anchor), so the shared timeline —
    which requires an anchor — records nothing here; the event lives in ``runtime_events``. Present
    for parity with the other domains and to document that individual evaluations are never published."""
    return None


def audit_history(principal, *, entity_type, entity_id) -> list[dict]:
    with engine.connect() as c:
        return [dict(e) for e in c.execute(
            select(runtime_events).where(runtime_events.c.entity_type == entity_type,
                                         runtime_events.c.entity_id == entity_id)
            .order_by(runtime_events.c.occurred_at, runtime_events.c.id)).mappings()]
