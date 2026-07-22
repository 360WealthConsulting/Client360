"""Runtime event bus (Phase D.29) — cross-process coordination over the transactional outbox.

Reuses the existing transactional outbox as the sole coordination bus (no second messaging system).
Runtime coordination events are published through ``app.platform.outbox.publish_event`` with a
canonical ``Envelope``, and a dark-launched consumer (``on_runtime_event``, registered only from the
gated outbox-dispatcher block) reacts by converging the local worker. Replay protection is twofold:
the outbox's ``outbox_processed_events`` (per event+consumer) AND the domain-level idempotent
converge-if-behind (reprocessing a stale event when already converged is a no-op). Every worker
converges on the same persisted runtime generation — the single source of truth — whether notified by
this push path or by the heartbeat pull path.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("client360.runtime.coordination")

# The runtime coordination event types published to the outbox.
RUNTIME_EVENT_TYPES = (
    "runtime.snapshot.created",
    "runtime.snapshot.activated",
    "runtime.snapshot.invalidated",
    "runtime.refresh.requested",
    "runtime.refresh.completed",
    "runtime.cache.invalidated",
    "runtime.cache.rebuilt",
    "runtime.override.changed",
)

# Event types whose delivery should trigger a local converge-if-behind.
_CONVERGE_ON = frozenset({
    "runtime.snapshot.activated", "runtime.snapshot.invalidated", "runtime.refresh.requested",
    "runtime.refresh.completed", "runtime.cache.invalidated", "runtime.cache.rebuilt",
    "runtime.override.changed",
})


def publish_runtime_event(conn, event_type: str, payload: dict | None = None, *,
                          correlation_id=None) -> str | None:
    """Publish a runtime coordination event through the EXISTING transactional outbox, within the
    caller's transaction (so it commits atomically with the ledger/snapshot write). Returns the
    outbox event id, or None if publication fails (never raises into the caller)."""
    if event_type not in RUNTIME_EVENT_TYPES:
        return None
    try:
        from app.platform.events import new_event
        from app.platform.outbox import publish_event
        envelope = new_event(event_type, payload=(payload or {}), correlation_id=correlation_id,
                             producer="runtime-coordination")
        publish_event(conn, envelope)
        return envelope.event_id
    except Exception:
        logger.exception("failed to publish runtime coordination event %s", event_type)
        return None


def on_runtime_event(event) -> None:
    """Outbox consumer: converge the local worker in response to a runtime coordination event. The
    outbox delivers this exactly once per (event, consumer); the converge is idempotent, so a replay
    or a heartbeat that already converged is a harmless no-op. Never raises (the dispatcher isolates
    handler failures, but we also guard here)."""
    try:
        name = event.get("name") if isinstance(event, dict) else getattr(event, "event_type", None)
        if name not in _CONVERGE_ON:
            return
        from . import coordination
        coordination.converge_worker()
    except Exception:
        logger.exception("runtime coordination consumer failed")


def register_runtime_consumers() -> None:
    """Subscribe the runtime coordination consumer to every runtime.* event type. Dark-launched —
    called only from the gated outbox-dispatcher block in the scheduler, exactly like the
    notification/workflow consumers, so no subscribers exist until the dispatcher is enabled."""
    from app.platform.outbox import subscribe
    for event_type in RUNTIME_EVENT_TYPES:
        subscribe(event_type, on_runtime_event)
