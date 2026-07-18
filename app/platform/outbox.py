"""Transactional outbox & dispatcher (E1.6 / Backlog F1.3).

A domain-agnostic primitive for reliable event publication:

  * ``publish(conn, name, payload)`` writes an event **in the caller's
    transaction**, so an event is persisted iff the business change commits
    (atomicity — the transactional-outbox guarantee).
  * ``dispatch_pending()`` polls committed pending events and delivers each to
    its subscribed handlers, at-least-once, with idempotency
    (``outbox_processed_events``), exponential backoff, and a dead-letter table
    after ``MAX_ATTEMPTS``.

This is infrastructure only: nothing in the application publishes events yet, so
the dispatcher is a no-op until producers and subscribers are added (later
backlog items). It complements — does not replace — the existing domain event
and automation tables (see docs/OUTBOX.md).

Reconciliation (ADR-013): the dispatcher is designed to run as an APScheduler job
in the existing scheduler, gated OFF by default so runtime behavior is unchanged
until explicitly enabled.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection, Engine

from app.database.schema import metadata

logger = logging.getLogger("client360.outbox")

outbox_events = metadata.tables["outbox_events"]
outbox_dead_letters = metadata.tables["outbox_dead_letters"]
outbox_processed_events = metadata.tables["outbox_processed_events"]

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30

# name -> handlers. A handler is Callable[[dict], None]; it receives an event view
# {"event_id", "name", "payload"} and must be idempotent (at-least-once delivery).
Handler = Callable[[dict], None]
_subscribers: dict[str, list[Handler]] = {}


def _now() -> datetime:
    return datetime.now(UTC)


def _engine(engine: Engine | None) -> Engine:
    if engine is not None:
        return engine
    from app.db import engine as default_engine  # lazy: avoids import-time DB coupling

    return default_engine


def _consumer_name(handler: Handler) -> str:
    module = getattr(handler, "__module__", "?")
    qualname = getattr(handler, "__qualname__", getattr(handler, "__name__", repr(handler)))
    return f"{module}.{qualname}"


def subscribe(event_name: str, handler: Handler) -> None:
    """Register a handler for an event name (idempotent registration)."""
    handlers = _subscribers.setdefault(event_name, [])
    if handler not in handlers:
        handlers.append(handler)


def clear_subscribers() -> None:
    """Remove all subscribers (test/support helper)."""
    _subscribers.clear()


def publish(conn: Connection, name: str, payload: dict | None = None, *, event_id: str | None = None) -> str:
    """Write an event into the outbox using the caller's connection/transaction.

    Because it uses ``conn``, the event commits atomically with the caller's
    business change. Returns the event_id.
    """
    event_id = event_id or str(uuid.uuid4())
    conn.execute(
        insert(outbox_events).values(event_id=event_id, name=name, payload=payload or {})
    )
    return event_id


def already_processed(conn: Connection, event_id: str, consumer: str) -> bool:
    row = conn.execute(
        select(outbox_processed_events.c.event_id).where(
            outbox_processed_events.c.event_id == event_id,
            outbox_processed_events.c.consumer == consumer,
        )
    ).first()
    return row is not None


def _mark_processed(conn: Connection, event_id: str, consumer: str) -> None:
    conn.execute(
        insert(outbox_processed_events).values(event_id=event_id, consumer=consumer)
    )


def dispatch_pending(
    engine: Engine | None = None, *, batch_size: int = 100, max_attempts: int = MAX_ATTEMPTS
) -> dict:
    """Deliver a batch of due, pending events. Returns a summary dict."""
    engine = _engine(engine)
    summary = {"dispatched": 0, "failed": 0, "dead_lettered": 0}

    with engine.connect() as conn:
        rows = conn.execute(
            select(outbox_events)
            .where(
                outbox_events.c.status == "pending",
                outbox_events.c.available_at <= func.now(),
            )
            .order_by(outbox_events.c.id)
            .limit(batch_size)
        ).fetchall()

    for row in rows:
        _dispatch_one(engine, row, max_attempts, summary)
    if summary["dispatched"] or summary["failed"] or summary["dead_lettered"]:
        logger.info("outbox dispatch", extra=summary)
    return summary


def _dispatch_one(engine: Engine, row, max_attempts: int, summary: dict) -> None:
    event_view = {"event_id": row.event_id, "name": row.name, "payload": row.payload}
    handlers = _subscribers.get(row.name, [])

    for handler in handlers:
        consumer = _consumer_name(handler)
        try:
            with engine.begin() as conn:
                if already_processed(conn, row.event_id, consumer):
                    continue
                handler(event_view)
                # Committed with the handler's success, so a later failure does not
                # replay this consumer on the next attempt.
                _mark_processed(conn, row.event_id, consumer)
        except Exception as exc:  # a handler failed — retry the event later
            logger.exception("outbox handler failed for event %s (%s)", row.event_id, row.name)
            _handle_failure(engine, row, max_attempts, exc, summary)
            return

    with engine.begin() as conn:
        conn.execute(
            update(outbox_events)
            .where(outbox_events.c.id == row.id)
            .values(status="dispatched", dispatched_at=_now())
        )
    summary["dispatched"] += 1


def _handle_failure(engine: Engine, row, max_attempts: int, exc: Exception, summary: dict) -> None:
    attempts = row.attempts + 1
    error = str(exc)[:2000]
    if attempts >= max_attempts:
        with engine.begin() as conn:
            conn.execute(
                insert(outbox_dead_letters).values(
                    event_id=row.event_id, name=row.name, payload=row.payload,
                    attempts=attempts, error=error,
                )
            )
            conn.execute(
                update(outbox_events)
                .where(outbox_events.c.id == row.id)
                .values(status="dead", attempts=attempts, last_error=error)
            )
        summary["dead_lettered"] += 1
    else:
        backoff = BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
        with engine.begin() as conn:
            conn.execute(
                update(outbox_events)
                .where(outbox_events.c.id == row.id)
                .values(
                    attempts=attempts,
                    available_at=_now() + timedelta(seconds=backoff),
                    last_error=error,
                )
            )
        summary["failed"] += 1
