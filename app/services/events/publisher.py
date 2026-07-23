"""The standardized domain-event publishing service (Phase D.34).

``publish(event_type, payload, …)`` is the single entry point for emitting a domain event. It validates
the event against its typed contract (the event type must be registered; the payload must conform to the
contract's references-only schema), wraps it in the canonical versioned ``Envelope``
(``app/platform/events.py``), and writes it to the existing transactional outbox
(``app/platform/outbox.py``) — reusing the one bus, its delivery guarantees, idempotency, dead-letter,
and envelope versioning. No second event table is introduced. When a caller supplies its own ``conn``,
the event commits atomically with the caller's business change (the transactional-outbox guarantee).
Every publish is counted in-process for observability/analytics; routine events are never individually
logged. Publishing never bypasses RBAC — the capability check stays at the call site.
"""
from __future__ import annotations

from app.platform.events import new_event
from app.platform.outbox import publish_event

from . import contracts
from .common import EventError, note


def publish(event_type: str, payload: dict | None = None, *, conn=None, producer=None, subject_ref=None,
            correlation_id=None, causation_id=None, metadata=None) -> str:
    """Publish a domain event through the standardized model. Validates against the typed contract,
    builds a versioned envelope, and writes it to the transactional outbox. Returns the event id. Raises
    :class:`EventError` for an unregistered event type or a payload that violates the contract."""
    payload = payload or {}
    contract = contracts.get_contract(event_type)
    if contract is None:
        note("publish_failures")
        raise EventError(f"unregistered domain event {event_type!r}")
    # References-only enforcement (D.35): reject a payload carrying a prohibited (PII / secret /
    # financial / health / tax / document-content) field before it can reach the outbox.
    from .payload_safety import sensitive_fields
    prohibited = sensitive_fields(payload.keys())
    if prohibited:
        note("publish_failures")
        raise EventError(f"payload for {event_type!r} carries prohibited sensitive fields: {prohibited}")
    problems = contract.validate_payload(payload)
    if problems:
        note("publish_failures")
        raise EventError(f"payload violates {event_type!r} contract: {problems}")
    envelope = new_event(event_type, payload, producer=producer or contract.producer,
                         subject_ref=subject_ref, correlation_id=correlation_id, causation_id=causation_id,
                         schema_version=contract.schema_version, metadata=metadata or {})
    if conn is not None:
        event_id = publish_event(conn, envelope)
    else:
        from app.db import engine
        with engine.begin() as c:
            event_id = publish_event(c, envelope)
    note("published")
    return event_id


def publish_safe(event_type: str, payload: dict | None = None, **kwargs) -> str | None:
    """Best-effort publish for additive adoption sites: a publish failure is swallowed so it can never
    corrupt the caller's business mutation. The failure is always COUNTED (``publish_failures``) so it
    is observable via diagnostics/analytics. Returns the event id, or None on failure."""
    try:
        return publish(event_type, payload, **kwargs)
    except EventError:
        return None   # already counted by publish() (unregistered / invalid / sensitive)
    except Exception:
        note("publish_failures")
        return None
