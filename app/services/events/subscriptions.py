"""Event subscriptions (Phase D.34) — the durable subscription registry + live wiring.

The subscription registry (``domain_event_subscriptions``) records which consumer subscribes to which
event type — the discoverable, governable record (so orphan subscriptions and producers-without-
consumers are detectable). The LIVE subscription (the actual outbox handler) is registered at startup
through the existing consumer-registration mechanism (``app/platform/outbox.py::subscribe``), gated OFF
by default like every other outbox consumer — so runtime behavior is unchanged until the dispatcher is
enabled. This layer manages the registry metadata and provides the observability sink for the new
``orchestration.lifecycle`` event.
"""
from __future__ import annotations

from sqlalchemy import select

from app.database.event_tables import SUBSCRIPTION_STATUSES
from app.db import domain_event_subscriptions, engine

from .common import note, now

# in-process delivery counter for the observability sink (routine events counted, never logged).
_DELIVERED = {"count": 0}


def add_subscription(event_type, consumer, *, owner=None, description=None) -> dict:
    with engine.begin() as c:
        existing = c.execute(select(domain_event_subscriptions).where(
            domain_event_subscriptions.c.event_type == event_type,
            domain_event_subscriptions.c.consumer == consumer)).mappings().first()
        if existing:
            return dict(existing)
        row = dict(c.execute(domain_event_subscriptions.insert().values(
            event_type=event_type, consumer=consumer, status="active", owner=owner,
            description=description).returning(*domain_event_subscriptions.c)).mappings().one())
    return row


def set_status(event_type, consumer, status) -> dict:
    if status not in SUBSCRIPTION_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with engine.begin() as c:
        row = c.execute(domain_event_subscriptions.update().where(
            domain_event_subscriptions.c.event_type == event_type,
            domain_event_subscriptions.c.consumer == consumer).values(
                status=status, updated_at=now()).returning(*domain_event_subscriptions.c)).mappings().first()
        if row is None:
            raise ValueError(f"unknown subscription {event_type!r}/{consumer!r}")
        return dict(row)


def delivered_count() -> int:
    return int(_DELIVERED["count"])


def _observability_sink(event_view: dict) -> None:
    """The observability consumer for orchestration.lifecycle events — records receipt (idempotent;
    the outbox tracks processed events). Content-free: counts delivery for observability/analytics."""
    note("published", 0)   # no-op on publish counters
    _DELIVERED["count"] += 1


def register_event_consumers() -> None:
    """Register the live outbox subscriptions for the domain-event model (dark-launched: called only in
    the scheduler's gated outbox block, so nothing is subscribed until the dispatcher is enabled)."""
    from app.platform.outbox import subscribe
    subscribe("orchestration.lifecycle", _observability_sink)
