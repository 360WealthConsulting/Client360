"""The Projection Engine (Phase D.36) — deterministic read-model runtime.

Consumes the D.34/D.35 domain events from the transactional outbox (``outbox_events`` — the sole event
bus + log; the engine only READS it) and applies them to the disposable read-model tables. Supports
full rebuild (truncate + replay every event), incremental processing (apply events since the checkpoint),
reset, replay, and validation (rebuild twice + compare — the read model is deterministic given the
events). Per-event failures are isolated (a savepoint per event) and counted — they never affect
business transactions and never touch authoritative tables. The outbox stays authoritative; read models
are disposable and fully rebuildable from events.
"""
from __future__ import annotations

import threading
import time

from sqlalchemy import func, select, text

from app.db import engine as db
from app.db import projection_state

from .common import note, now, parse_occurred_at
from .definitions import get_definition

_lock = threading.RLock()
LAG_THRESHOLD = 500


def _outbox():
    from app.db import metadata
    return metadata.tables["outbox_events"]


def _state_row(c, projection_id) -> dict:
    row = c.execute(select(projection_state).where(
        projection_state.c.projection_id == projection_id)).mappings().first()
    if row is None:
        c.execute(projection_state.insert().values(projection_id=projection_id, health="unbuilt"))
        row = c.execute(select(projection_state).where(
            projection_state.c.projection_id == projection_id)).mappings().first()
    return dict(row)


def state(projection_id) -> dict:
    with db.connect() as c:
        return _state_row(c, projection_id)


def _decode(row) -> dict:
    env = row["payload"] or {}
    return {"outbox_id": row["id"], "event_id": env.get("event_id"), "event_type": row["name"],
            "payload": env.get("payload") or {}, "subject_ref": env.get("subject_ref"),
            "occurred_at": parse_occurred_at(env.get("occurred_at"))}


def _fetch(c, after_id, ev_types, limit):
    oe = _outbox()
    stmt = select(oe).where(oe.c.id > after_id).order_by(oe.c.id).limit(limit)
    if ev_types is not None:
        stmt = stmt.where(oe.c.name.in_(list(ev_types)))
    return [dict(r) for r in c.execute(stmt).mappings()]


def _compute_health(rebuild_count, events_processed, failed_events, lag) -> str:
    if rebuild_count == 0 and events_processed == 0:
        return "unbuilt"
    if events_processed == 0 and failed_events > 0:
        return "failed"
    if lag > LAG_THRESHOLD:
        return "lagging"
    return "healthy"


def process(projection_id, *, incremental=True, batch=2000, max_batches=10000) -> dict:
    """Apply new (incremental) or all events to the projection. Deterministic; per-event failure
    isolation. Never raises into a caller; never touches authoritative tables."""
    d = get_definition(projection_id)
    if d is None:
        raise ValueError(f"unknown projection {projection_id!r}")
    ev_types = None if d.all_events else list(d.subscribed_events)
    with db.connect() as c:
        st = _state_row(c, projection_id)
    cursor = st["last_processed_event_id"] if incremental else 0
    total_proc = total_failed = 0
    last_error = None
    started = time.perf_counter_ns()
    for _ in range(max_batches):
        with db.begin() as c:
            rows = _fetch(c, cursor, ev_types, batch)
            if not rows:
                break
            proc = failed = 0
            for row in rows:
                event = _decode(row)
                sp = c.begin_nested()
                try:
                    d.apply(c, event)
                    sp.commit()
                    proc += 1
                except Exception as exc:            # per-event isolation
                    sp.rollback()
                    failed += 1
                    last_error = str(exc)[:500]
                cursor = row["id"]
            total_proc += proc
            total_failed += failed
            _bump_state(c, projection_id, cursor, proc, failed, last_error)
        if len(rows) < batch:
            break
    dur_ms = (time.perf_counter_ns() - started) // 1_000_000
    note("events_processed", total_proc)
    note("failed_events", total_failed)
    note("total_process_ms", int(dur_ms))
    return {"projection_id": projection_id, "processed": total_proc, "failed": total_failed,
            "last_processed_event_id": cursor, "duration_ms": int(dur_ms)}


def _bump_state(c, projection_id, cursor, proc, failed, last_error):
    st = _state_row(c, projection_id)
    lag_after = _lag(c, get_definition(projection_id), cursor)
    events_total = st["events_processed"] + proc
    failed_total = st["failed_events"] + failed
    health = _compute_health(st["rebuild_count"], events_total, failed_total, lag_after)
    c.execute(projection_state.update().where(projection_state.c.projection_id == projection_id).values(
        last_processed_event_id=cursor, last_processed_at=now(), events_processed=events_total,
        failed_events=failed_total, health=health, last_error=last_error, updated_at=now()))


def _lag(c, definition, cursor) -> int:
    oe = _outbox()
    stmt = select(func.count()).select_from(oe).where(oe.c.id > cursor)
    if not definition.all_events:
        stmt = stmt.where(oe.c.name.in_(list(definition.subscribed_events)))
    return c.scalar(stmt) or 0


def lag(projection_id) -> int:
    d = get_definition(projection_id)
    with db.connect() as c:
        st = _state_row(c, projection_id)
        return _lag(c, d, st["last_processed_event_id"])


def size(projection_id) -> int:
    d = get_definition(projection_id)
    with db.connect() as c:
        return c.scalar(text(f"SELECT count(*) FROM {d.read_table}")) or 0


def _truncate_and_reset(projection_id, *, health):
    d = get_definition(projection_id)
    with db.begin() as c:
        c.execute(text(f"DELETE FROM {d.read_table}"))          # read models are disposable
        _state_row(c, projection_id)
        c.execute(projection_state.update().where(projection_state.c.projection_id == projection_id).values(
            last_processed_event_id=0, events_processed=0, failed_events=0, health=health,
            last_processed_at=None, last_error=None, updated_at=now()))


def reset(projection_id) -> dict:
    """Delete the read model + reset the checkpoint (unbuilt). The events are untouched."""
    _truncate_and_reset(projection_id, health="unbuilt")
    note("resets")
    return {"projection_id": projection_id, "reset": True}


def rebuild(projection_id, *, as_replay=False) -> dict:
    """Fully rebuild the read model from events: truncate → replay every matching event. Deterministic."""
    _truncate_and_reset(projection_id, health="building")
    started = time.perf_counter_ns()
    result = process(projection_id, incremental=False)
    dur_ms = int((time.perf_counter_ns() - started) // 1_000_000)
    with db.begin() as c:
        st = _state_row(c, projection_id)
        history = list(st.get("rebuild_history") or [])[-9:]
        history.append({"type": "replay" if as_replay else "rebuild", "processed": result["processed"],
                        "failed": result["failed"], "duration_ms": dur_ms, "at": now().isoformat()})
        field = "replay_count" if as_replay else "rebuild_count"
        values = {field: st[field] + 1, "rebuild_history": history, "updated_at": now()}
        if as_replay:
            values["last_replay_at"] = now()
            values["last_replay_duration_ms"] = dur_ms
        else:
            values["last_rebuild_at"] = now()
            values["last_rebuild_duration_ms"] = dur_ms
        health = _compute_health(st["rebuild_count"] + (0 if as_replay else 1), st["events_processed"],
                                 st["failed_events"], _lag(c, get_definition(projection_id),
                                                           st["last_processed_event_id"]))
        values["health"] = health
        c.execute(projection_state.update().where(projection_state.c.projection_id == projection_id)
                  .values(**values))
    note("replays" if as_replay else "rebuilds")
    return {**result, "duration_ms": dur_ms, "type": "replay" if as_replay else "rebuild"}


def replay(projection_id) -> dict:
    """Deterministically replay (rebuild) the projection from the recorded events."""
    return rebuild(projection_id, as_replay=True)


def signature(projection_id) -> tuple:
    """A deterministic content signature of the read model (row count + hash of ordered rows, excluding
    the volatile ``updated_at``) — used to prove replay determinism."""
    import hashlib
    d = get_definition(projection_id)
    with db.connect() as c:
        rows = list(c.execute(text(f"SELECT * FROM {d.read_table} ORDER BY 2")).mappings())
    # exclude the surrogate ``id`` and volatile ``updated_at`` — the deterministic content is the
    # event-derived business columns (the natural key + statuses + event-sourced timestamps).
    volatile = {"id", "updated_at"}
    canon = [tuple((k, str(v)) for k, v in sorted(r.items()) if k not in volatile) for r in rows]
    h = hashlib.sha256(repr(canon).encode()).hexdigest()
    return (len(rows), h)


def validate(projection_id) -> dict:
    """Rebuild the projection twice and compare — the read model must be deterministic given the events.
    Records the outcome on the projection state (``last_validation_ok``)."""
    rebuild(projection_id)
    sig1 = signature(projection_id)
    rebuild(projection_id)
    sig2 = signature(projection_id)
    ok = sig1 == sig2
    with db.begin() as c:
        c.execute(projection_state.update().where(projection_state.c.projection_id == projection_id)
                  .values(last_validation_ok="ok" if ok else "mismatch", updated_at=now()))
    return {"projection_id": projection_id, "deterministic": ok, "rows": sig1[0]}


def tick(*, only_active=True) -> dict:
    """Incrementally process every projection (the scheduler entry point — dark-launched). No-op when
    idle; deterministic; single-instance."""
    from . import registry
    results = []
    for d in registry.list_definitions(status="active" if only_active else None):
        try:
            results.append(process(d["projection_id"], incremental=True))
        except Exception:                            # projection failures never propagate
            continue
    return {"projections": len(results),
            "processed": sum(r["processed"] for r in results),
            "failed": sum(r["failed"] for r in results)}


def stats() -> dict:
    from .common import stats as base_stats
    s = base_stats()
    with db.connect() as c:
        rows = list(c.execute(select(projection_state.c.health, func.count().label("n"))
                              .group_by(projection_state.c.health)).mappings())
    s["by_health"] = {r["health"]: r["n"] for r in rows}
    evals = s["events_processed"]
    s["avg_process_ms"] = round(s["total_process_ms"] / evals, 4) if evals else None
    return s


def reset_stats():
    from .common import reset_stats as base
    base()
