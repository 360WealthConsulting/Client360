"""Shared helpers for the Enterprise Domain Event Model (Phase D.34) — counters, audit, types.

In-process counters (published / delivered / failed / dead-lettered / replayed) feed observability +
analytics; routine successful events are counted, never individually logged. Major lifecycle events
(contract deprecated / retired, governance validated) record to the shared D.25 audit hash-chain. The
event model reuses the existing transactional outbox as the bus and never bypasses RBAC (routes gate
every surface).
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime

_lock = threading.RLock()
_STATS = {"published": 0, "publish_failures": 0, "replays": 0}


class EventError(Exception):
    """Validation error (an unregistered/retired event type, or a payload violating the contract)."""


class EventNotFound(Exception):
    """Entity not found."""


def now():
    return datetime.now(UTC)


def as_json(payload):
    import json
    return json.loads(json.dumps(payload or {}, default=str))


def note(kind: str, n: int = 1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + n


def stats() -> dict:
    with _lock:
        s = dict(_STATS)
    total = s["published"] + s["publish_failures"]
    s["publish_total"] = total
    s["publish_success_rate"] = round(s["published"] / total, 4) if total else None
    return s


def reset_stats():
    with _lock:
        for k in list(_STATS):
            _STATS[k] = 0


def write_audit(action, *, entity_type="domain_event", entity_id=0, actor_user_id=None, metadata=None):
    """Record a major event-model action in the shared tamper-evident audit hash-chain (low frequency:
    contract lifecycle + governance — never per published event)."""
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type=entity_type, entity_id=str(entity_id),
                          actor_user_id=actor_user_id, request_id=str(uuid.uuid4()), metadata=metadata or {})
    except Exception:
        pass
