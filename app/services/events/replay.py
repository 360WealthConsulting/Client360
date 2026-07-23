"""Event replay (Phase D.34) — deterministic reconstruction + controlled re-dispatch.

Replays a persisted domain event from the transactional-outbox log. By default replay is a **pure
read**: it reconstructs the canonical envelope from the stored ``outbox_events`` row, reports which
consumers are subscribed and which have already processed it, and never mutates production state. An
explicit, capability-gated re-dispatch (``deliver=True``) re-delivers the reconstructed envelope to the
live outbox subscribers — idempotently (the outbox ``outbox_processed_events`` ledger prevents a
consumer from re-processing), so replay is safe. Replay reuses the one outbox; it introduces no new
transport and no new event table.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import engine

from .common import EventNotFound, note


def _events_table():
    from app.db import metadata
    return metadata.tables["outbox_events"]


def replay(event_id: str, *, deliver: bool = False) -> dict:
    """Replay an event. Reconstructs the envelope deterministically from the outbox log (a pure read).
    When ``deliver=True``, re-dispatches it to the live subscribers idempotently. Returns the
    reconstructed envelope, the subscribers, and (if delivered) the delivery outcome."""
    note("replays")
    events_t = _events_table()
    with engine.connect() as c:
        row = c.execute(select(events_t).where(events_t.c.event_id == event_id)).mappings().first()
    if row is None:
        raise EventNotFound(f"event {event_id} not in the outbox log")
    row = dict(row)

    from app.platform.events import Envelope, is_envelope
    payload = row.get("payload") or {}
    envelope = Envelope.from_dict(payload).to_dict() if is_envelope(payload) else None

    from app.platform import outbox
    subscribers = [_consumer_name(h) for h in outbox._subscribers.get(row["name"], [])]

    result = {"event_id": event_id, "event_type": row["name"], "reconstructed_envelope": envelope,
              "is_envelope": envelope is not None, "subscribers": subscribers,
              "delivered": False, "modified_production_state": False}

    if deliver and subscribers:
        delivered = _redispatch(row)
        result["delivered"] = True
        result["delivered_to"] = delivered
        result["modified_production_state"] = bool(delivered)
    return result


def _consumer_name(handler) -> str:
    from app.platform.outbox import _consumer_name as cn
    return cn(handler)


def _redispatch(row: dict) -> list[str]:
    """Re-deliver a reconstructed event to its live subscribers, idempotently (skip a consumer that has
    already processed it). Returns the consumers that were (re-)invoked."""
    from app.platform import outbox
    view = {"event_id": row["event_id"], "name": row["name"], "payload": row.get("payload") or {}}
    invoked = []
    for handler in outbox._subscribers.get(row["name"], []):
        consumer = _consumer_name(handler)
        with engine.begin() as c:
            if outbox.already_processed(c, row["event_id"], consumer):
                continue
            handler(view)
            outbox._mark_processed(c, row["event_id"], consumer)
        invoked.append(consumer)
    return invoked
