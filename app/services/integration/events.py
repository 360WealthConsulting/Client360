"""Event catalog & publication (Phase D.24) — reuses the transactional outbox as the event bus.

Integration owns an event CATALOG (definitions + subscriptions as metadata) and publishes real
events through the EXISTING transactional outbox (``app.platform.outbox`` + the canonical
``Envelope``) — it never duplicates the outbox and adds no external broker. Payloads carry
references only (never PII/secrets), and event ids are deterministic (uuid5), mirroring the
workflow-events pattern. The existing ``dispatch_outbox`` automation job delivers them.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db import engine
from app.db import integration_event_definitions as defs_t
from app.db import integration_event_subscriptions as subs_t

from .common import IntegrationError, IntegrationNotFound, record_event

_EVENT_NAMESPACE = uuid.UUID("6f3a1c2e-9b4d-4e7a-8c1f-2a3b4c5d6e70")


# --- event definitions -------------------------------------------------------

def list_definitions(*, active_only=False):
    with engine.connect() as c:
        stmt = select(defs_t).order_by(defs_t.c.code)
        if active_only:
            stmt = stmt.where(defs_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_definition(*, code):
    with engine.connect() as c:
        row = c.execute(select(defs_t).where(defs_t.c.code == code)).mappings().first()
        return dict(row) if row else None


def create_definition(*, code, name, description=None, category=None, payload_schema=None,
                      actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise IntegrationError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(defs_t.c.id).where(defs_t.c.code == code)) is not None:
            raise IntegrationError(f"event definition code {code!r} already exists")
        row = c.execute(defs_t.insert().values(
            code=code, name=name.strip(), description=description, category=category,
            payload_schema=payload_schema, active=True,
            created_by_user_id=actor_user_id).returning(*defs_t.c)).mappings().one()
        return dict(row)


# --- event subscriptions -----------------------------------------------------

def create_subscription(*, event_definition_id, subscriber, subscriber_type="internal", target_id=None,
                        filter=None, actor_user_id=None) -> dict:
    with engine.begin() as c:
        if c.scalar(select(defs_t.c.id).where(defs_t.c.id == event_definition_id)) is None:
            raise IntegrationError("event definition not found")
        row = c.execute(subs_t.insert().values(
            event_definition_id=event_definition_id, subscriber=subscriber, subscriber_type=subscriber_type,
            target_id=target_id, filter=filter, active=True,
            created_by_user_id=actor_user_id).returning(*subs_t.c)).mappings().one()
        return dict(row)


def list_subscriptions(*, event_definition_id=None):
    with engine.connect() as c:
        stmt = select(subs_t).order_by(subs_t.c.id.desc())
        if event_definition_id is not None:
            stmt = stmt.where(subs_t.c.event_definition_id == event_definition_id)
        return [dict(r) for r in c.execute(stmt).mappings()]


# --- publication (through the EXISTING outbox) -------------------------------

def publish_event(principal, code: str, *, payload=None, subject_ref=None, correlation_id=None,
                  actor_user_id=None) -> str:
    """Publish an integration event through the transactional outbox. The event type must be a
    registered, active definition. Deterministic outbox event id; references only in the payload."""
    definition = get_definition(code=code)
    if definition is None or not definition["active"]:
        raise IntegrationNotFound(f"active event definition {code!r}")
    event_id = str(uuid.uuid5(_EVENT_NAMESPACE, f"integration:{code}:{subject_ref or ''}:{uuid.uuid4()}"))
    with engine.begin() as c:
        try:
            from app.platform.events import new_event
            from app.platform.outbox import publish_event as outbox_publish
            envelope = new_event(code, payload=(payload or {}), event_id=event_id,
                                 subject_ref=subject_ref, correlation_id=correlation_id,
                                 producer="integration")
            outbox_publish(c, envelope)
        except Exception:
            # Fall back to the simple outbox publish if the envelope helper is unavailable.
            from app.platform.outbox import publish as outbox_simple
            outbox_simple(c, code, payload=(payload or {}), event_id=event_id)
        record_event(c, entity_type="event_definition", entity_id=definition["id"],
                     event_type="event_published", actor_user_id=actor_user_id,
                     payload={"code": code, "event_id": event_id})
    return event_id
