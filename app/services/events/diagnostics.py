"""Event diagnostics (Phase D.34) — read-only inspection of the domain-event flow.

Reads the existing transactional-outbox log (``outbox_events`` / ``outbox_dead_letters`` /
``outbox_processed_events``) to report the recent domain events, per-type counts, delivery status,
the dead-letter view, subscriber health, and replay-readiness. Read-only — it never delivers, never
mutates production state, and never bypasses the outbox.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db import engine

from . import registry


def _outbox():
    from app.db import metadata
    return (metadata.tables["outbox_events"], metadata.tables["outbox_dead_letters"],
            metadata.tables["outbox_processed_events"])


def recent_events(*, event_type=None, limit=100) -> list[dict]:
    events_t, _dl, _pr = _outbox()
    known = {c["event_type"] for c in registry.list_contracts()}
    with engine.connect() as c:
        stmt = select(events_t).order_by(events_t.c.id.desc())
        if event_type:
            stmt = stmt.where(events_t.c.name == event_type)
        rows = [dict(r) for r in c.execute(stmt.limit(min(500, max(1, limit)))).mappings()]
    # annotate whether each event's type is a registered domain-event contract
    for r in rows:
        r["registered"] = r["name"] in known
    return rows


def event_counts() -> dict:
    """Per-event-type counts + delivery status from the outbox log."""
    events_t, dl_t, _pr = _outbox()
    with engine.connect() as c:
        by_type = {r["name"]: r["n"] for r in c.execute(select(
            events_t.c.name, func.count().label("n")).group_by(events_t.c.name)).mappings()}
        by_status = {r["status"]: r["n"] for r in c.execute(select(
            events_t.c.status, func.count().label("n")).group_by(events_t.c.status)).mappings()}
        dead = c.scalar(select(func.count()).select_from(dl_t)) or 0
    return {"by_type": by_type, "by_status": by_status, "dead_lettered": dead}


def dead_letters(*, limit=100) -> list[dict]:
    _events, dl_t, _pr = _outbox()
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(dl_t).order_by(dl_t.c.id.desc())
                                           .limit(min(500, max(1, limit)))).mappings()]


def subscriber_health() -> list[dict]:
    """For each active contract, its registered subscribers and whether it has a live consumer."""
    out = []
    for con in registry.list_contracts(status="active"):
        subs = registry.subscribers_of(con["event_type"])
        out.append({"event_type": con["event_type"], "producer": con["producer"],
                    "subscribers": subs, "has_consumer": bool(subs)})
    return out


def event_diagnostics(event_id: str) -> dict:
    """The full delivery view of a single event: the outbox row, which consumers processed it, and its
    replay-readiness. Read-only."""
    events_t, dl_t, pr_t = _outbox()
    with engine.connect() as c:
        row = c.execute(select(events_t).where(events_t.c.event_id == event_id)).mappings().first()
        processed = [r["consumer"] for r in c.execute(select(pr_t.c.consumer).where(
            pr_t.c.event_id == event_id)).mappings()]
        dead = c.execute(select(dl_t).where(dl_t.c.event_id == event_id)).mappings().first()
    if row is None and dead is None:
        return {}
    ev = dict(row) if row else None
    return {"event": ev, "processed_by": processed, "dead_lettered": bool(dead),
            "subscribers": registry.subscribers_of(ev["name"]) if ev else [],
            "replay_readiness": replay_readiness(event_id, event=ev, processed=processed)}


def replay_readiness(event_id: str, *, event=None, processed=None) -> dict:
    """Whether an event can be deterministically replayed: it needs a persisted envelope in the outbox
    log and a registered contract; idempotency (outbox_processed_events) makes replay safe."""
    events_t, _dl, _pr = _outbox()
    if event is None:
        with engine.connect() as c:
            row = c.execute(select(events_t).where(events_t.c.event_id == event_id)).mappings().first()
            event = dict(row) if row else None
    if event is None:
        return {"ready": False, "reason": "event not in outbox log"}
    from app.platform.events import is_envelope
    enveloped = is_envelope(event.get("payload") or {})
    registered = event["name"] in {c["event_type"] for c in registry.list_contracts()}
    return {"ready": bool(enveloped and registered), "is_envelope": enveloped, "registered": registered,
            "already_processed_by": processed or []}
