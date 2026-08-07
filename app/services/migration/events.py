"""Ingestion stage events + a pluggable publisher / subscriber seam (Event Publishing service).

Every completed ingestion stage publishes a :class:`StageEvent`. Downstream systems — OCR, AI extraction,
indexing, workflow automation, notifications, search, compliance, audit — SUBSCRIBE here and react
without modifying the ingestion engine.

Two publisher implementations, one contract:
  * :class:`CollectingEventPublisher` (the default) keeps events in memory and fans them out to registered
    subscribers. It writes NOTHING to the database — safe for read-only preview.
  * An ``OutboxEventPublisher`` (added with APPLY) will additionally persist each stage event to
    Client360's domain-event outbox via ``app.services.events.publisher.publish`` (with registered event
    contracts). The outbox remains the single durable bus — this seam never becomes a parallel bus.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Stage(StrEnum):
    """The independent ingestion stages. Each runs as its own step and publishes on completion."""

    DISCOVERY = "discovery"
    NORMALIZATION = "normalization"
    TRANSFORMATION = "transformation"
    CANONICAL_MATCHING = "canonical_matching"
    VALIDATION = "validation"
    PREVIEW = "preview"
    APPLY = "apply"
    RECONCILIATION = "reconciliation"
    RETIREMENT = "retirement"
    EVENT_PUBLISHING = "event_publishing"


@dataclass
class StageEvent:
    stage: Stage
    source_system: str
    status: str = "completed"
    counts: dict = field(default_factory=dict)
    at: str = ""


class EventPublisher(ABC):
    @abstractmethod
    def publish(self, event: StageEvent) -> None:
        ...


Subscriber = Callable[[StageEvent], None]
_SUBSCRIBERS: list[Subscriber] = []


def subscribe(fn: Subscriber) -> None:
    """Register a downstream reactor (OCR / AI / indexing / search / compliance / audit / …)."""
    _SUBSCRIBERS.append(fn)


def clear_subscribers() -> None:
    _SUBSCRIBERS.clear()


class CollectingEventPublisher(EventPublisher):
    """Default publisher: records events in memory and fans out to subscribers. No database writes."""

    def __init__(self):
        self.events: list[StageEvent] = []

    def publish(self, event: StageEvent) -> None:
        if not event.at:
            event.at = datetime.now(UTC).isoformat(timespec="seconds")
        self.events.append(event)
        for fn in list(_SUBSCRIBERS):
            try:
                fn(event)
            except Exception:  # noqa: BLE001 — a subscriber must never break ingestion
                pass


class OutboxEventPublisher(EventPublisher):
    """Durable publisher (CONTRACT ONLY — lands with APPLY).

    Persists each completed-stage event to Client360's EXISTING domain-event outbox via
    ``app.services.events.publisher.publish`` under a registered event contract. Client360's outbox stays
    the single durable bus; this is a bridge onto it, never a competing/parallel bus. It is not wired into
    the read-only preview (which uses :class:`CollectingEventPublisher`) and requires the stage-event
    contracts to be registered first, so ``publish`` is intentionally unimplemented in this phase."""

    def publish(self, event: StageEvent) -> None:
        raise NotImplementedError(
            "OutboxEventPublisher lands with APPLY: it persists via app.services.events.publisher.publish "
            "under registered stage-event contracts (the existing outbox is the single durable bus).")
