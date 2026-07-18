"""E1.6 / F1.3 — Transactional outbox & dispatcher acceptance tests.

Exercises the mechanism directly against the (disposable) test database: atomic
publish, at-least-once delivery, idempotency, backoff/retry, and dead-lettering.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select

from app.config import outbox_dispatcher_enabled
from app.db import engine
from app.platform.outbox import (
    clear_subscribers,
    dispatch_pending,
    outbox_dead_letters,
    outbox_events,
    outbox_processed_events,
    publish,
    subscribe,
)


@pytest.fixture(autouse=True)
def _clean_outbox():
    """Isolate each test: no subscribers, no leftover outbox rows."""
    def _wipe():
        with engine.begin() as conn:
            conn.execute(delete(outbox_processed_events))
            conn.execute(delete(outbox_dead_letters))
            conn.execute(delete(outbox_events))
    clear_subscribers()
    _wipe()
    yield
    clear_subscribers()
    _wipe()


def _count(name: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(outbox_events).where(outbox_events.c.name == name)
        ).scalar_one()


def _row(event_id: str):
    with engine.connect() as conn:
        return conn.execute(
            select(outbox_events).where(outbox_events.c.event_id == event_id)
        ).mappings().first()


def test_outbox_tables_declared_and_present():
    from app.database.schema import metadata

    for name in ("outbox_events", "outbox_dead_letters", "outbox_processed_events"):
        assert name in metadata.tables


def test_publish_is_atomic_with_caller_transaction():
    # Rolled-back transaction => no event persisted.
    with engine.connect() as conn:
        trans = conn.begin()
        publish(conn, "e1_6.rollback", {"k": 1})
        trans.rollback()
    assert _count("e1_6.rollback") == 0

    # Committed transaction => exactly one pending event.
    with engine.begin() as conn:
        event_id = publish(conn, "e1_6.commit", {"k": 2})
    assert _count("e1_6.commit") == 1
    assert _row(event_id)["status"] == "pending"


def test_dispatch_delivers_and_marks_dispatched():
    seen = []
    subscribe("e1_6.deliver", lambda event: seen.append(event))
    with engine.begin() as conn:
        event_id = publish(conn, "e1_6.deliver", {"account": 7})

    summary = dispatch_pending()

    assert summary["dispatched"] == 1
    assert seen == [{"event_id": event_id, "name": "e1_6.deliver", "payload": {"account": 7}}]
    assert _row(event_id)["status"] == "dispatched"
    with engine.connect() as conn:
        processed = conn.execute(
            select(func.count()).select_from(outbox_processed_events).where(
                outbox_processed_events.c.event_id == event_id
            )
        ).scalar_one()
    assert processed == 1


def test_idempotent_no_double_delivery():
    calls = []
    subscribe("e1_6.idem", lambda event: calls.append(event["event_id"]))
    with engine.begin() as conn:
        publish(conn, "e1_6.idem", {})

    first = dispatch_pending()
    second = dispatch_pending()  # nothing pending now

    assert first["dispatched"] == 1
    assert second == {"dispatched": 0, "failed": 0, "dead_lettered": 0}
    assert len(calls) == 1


def test_failure_retries_with_backoff():
    def boom(event):
        raise RuntimeError("handler failed")

    subscribe("e1_6.retry", boom)
    with engine.begin() as conn:
        event_id = publish(conn, "e1_6.retry", {})

    summary = dispatch_pending(max_attempts=5)

    assert summary == {"dispatched": 0, "failed": 1, "dead_lettered": 0}
    row = _row(event_id)
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert row["last_error"] and "handler failed" in row["last_error"]
    # Backed off into the future — not immediately re-dispatchable.
    with engine.connect() as conn:
        due_now = conn.execute(
            select(func.count()).select_from(outbox_events).where(
                outbox_events.c.status == "pending",
                outbox_events.c.available_at <= func.now(),
            )
        ).scalar_one()
    assert due_now == 0


def test_dead_letter_after_max_attempts():
    subscribe("e1_6.dead", lambda event: (_ for _ in ()).throw(ValueError("nope")))
    with engine.begin() as conn:
        event_id = publish(conn, "e1_6.dead", {})

    # max_attempts=1 => the first failure dead-letters immediately.
    summary = dispatch_pending(max_attempts=1)

    assert summary["dead_lettered"] == 1
    assert _row(event_id)["status"] == "dead"
    with engine.connect() as conn:
        dead = conn.execute(
            select(outbox_dead_letters).where(outbox_dead_letters.c.event_id == event_id)
        ).mappings().first()
    assert dead is not None
    assert dead["attempts"] == 1


def test_dispatcher_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OUTBOX_DISPATCHER_ENABLED", raising=False)
    assert outbox_dispatcher_enabled() is False
    for value in ("true", "1", "on", "yes"):
        monkeypatch.setenv("OUTBOX_DISPATCHER_ENABLED", value)
        assert outbox_dispatcher_enabled() is True
