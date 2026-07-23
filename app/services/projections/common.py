"""Shared helpers for the Read Models & Projection Engine (Phase D.36).

In-process counters (events processed, rebuilds, replays, resets, failures, latency) feed
observability/analytics. Upsert helpers write the disposable read-model tables idempotently from event
data (references only). Projections never touch authoritative tables and never affect business
transactions — they run in their own connections and swallow per-event failures (failure isolation).
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

_lock = threading.RLock()
_STATS = {"events_processed": 0, "rebuilds": 0, "replays": 0, "resets": 0, "failed_events": 0,
          "total_process_ms": 0}


class ProjectionError(Exception):
    """Validation / projection error (never raised into a business transaction)."""


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
        return dict(_STATS)


def reset_stats():
    with _lock:
        for k in list(_STATS):
            _STATS[k] = 0


def parse_occurred_at(value):
    """Parse an ISO-8601 envelope ``occurred_at`` into a datetime (best-effort)."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def upsert(conn, table, key_col: str, key, set_values: dict, event: dict, *, insert_extra=None):
    """Insert-or-update a read-model row keyed on ``key_col``. Stamps last_event_type/last_event_at.
    Deterministic + idempotent: replaying the same events yields the same row."""
    occurred = event.get("occurred_at")
    common = {"last_event_type": event.get("event_type"), "last_event_at": occurred, "updated_at": now()}
    values = {key_col: key, **{k: v for k, v in set_values.items() if v is not None},
              **(insert_extra or {}), **common}
    stmt = pg_insert(table).values(**values)
    update = {k: stmt.excluded[k] for k in set_values if set_values[k] is not None}
    update.update({"last_event_type": stmt.excluded.last_event_type,
                   "last_event_at": stmt.excluded.last_event_at, "updated_at": now()})
    conn.execute(stmt.on_conflict_do_update(index_elements=[key_col], set_=update))


def increment(conn, table, key_col: str, key, counter_col: str, event: dict, *, insert_values=None):
    """Insert a row (counter = 1) or increment ``counter_col`` on conflict. Deterministic given the
    event set."""
    occurred = event.get("occurred_at")
    values = {key_col: key, counter_col: 1, "last_event_type": event.get("event_type"),
              "last_event_at": occurred, "updated_at": now(), **(insert_values or {})}
    stmt = pg_insert(table).values(**values)
    set_ = {counter_col: getattr(table.c, counter_col) + 1,
            "last_event_type": stmt.excluded.last_event_type,
            "last_event_at": stmt.excluded.last_event_at, "updated_at": now()}
    conn.execute(stmt.on_conflict_do_update(index_elements=[key_col], set_=set_))
