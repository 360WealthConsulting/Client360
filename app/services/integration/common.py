"""Shared helpers for the Enterprise Integration domain (Phase D.24).

Integration owns integration metadata gated by ``integration.*``. Most items are firm-level; a sync
run may carry an optional client anchor for guarded timeline publication. Firm-level lifecycle events
record only to the append-only ``integration_events`` ledger + the shared ``audit_events`` hash-chain.
Secrets are NEVER stored in plaintext — credential/webhook secrets are Fernet ciphertext via
``integration_crypto`` (or a pointer to an existing encrypted store).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import engine, integration_events

# Approved integration lifecycle events published to the shared timeline (client-anchored only).
_TIMELINE_EVENTS = {"connected": "integration_connected", "disconnected": "integration_disconnected",
                    "sync_completed": "integration_sync_completed",
                    "sync_failed": "integration_sync_failed", "webhook_verified": "integration_webhook_verified"}


class IntegrationError(Exception):
    """Validation or lifecycle error."""


class IntegrationNotFound(Exception):
    """Entity not found."""


def now():
    return datetime.now(UTC)


def as_json(payload):
    return json.loads(json.dumps(payload or {}, default=str))


def encrypt_secret(plaintext: str | None) -> str | None:
    """Fernet-encrypt a secret for storage — NEVER plaintext. Returns None for empty input."""
    if not plaintext:
        return None
    from app.security.integration_crypto import encrypt
    return encrypt(plaintext)


def record_event(c, *, entity_type, entity_id, event_type, from_status=None, to_status=None,
                 actor_user_id=None, payload=None):
    c.execute(integration_events.insert().values(
        entity_type=entity_type, entity_id=entity_id, event_type=event_type, from_status=from_status,
        to_status=to_status, actor_user_id=actor_user_id, payload=as_json(payload), occurred_at=now()))


def write_audit(action, *, entity_type, entity_id, actor_user_id=None, metadata=None):
    """Record integration actions in the shared tamper-evident audit hash-chain (references only —
    never credentials/payloads)."""
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type=entity_type, entity_id=str(entity_id),
                          actor_user_id=actor_user_id, request_id=str(uuid.uuid4()),
                          metadata=metadata or {})
    except Exception:
        pass


def publish_timeline(row: dict, kind: str, *, title=None, summary=None):
    """Publish an approved integration lifecycle event — only when the item carries a client anchor
    (the timeline requires person_id/household_id); firm-level integration events skip it."""
    event_type = _TIMELINE_EVENTS.get(kind)
    if event_type is None:
        return
    if not row.get("person_id") and not row.get("household_id"):
        return
    try:
        from app.services.timeline import add_timeline_event
        add_timeline_event(source="integration", event_type=event_type,
                           title=title or kind.replace("_", " ").title(), summary=summary or "",
                           person_id=row.get("person_id"), household_id=row.get("household_id"),
                           external_id=f"integration-{kind}-{row['id']}",
                           event_metadata={"kind": kind, "id": row["id"]})
    except Exception:
        pass


def audit_history(principal, *, entity_type, entity_id) -> list[dict]:
    with engine.connect() as c:
        return [dict(e) for e in c.execute(
            select(integration_events).where(integration_events.c.entity_type == entity_type,
                                             integration_events.c.entity_id == entity_id)
            .order_by(integration_events.c.occurred_at, integration_events.c.id)).mappings()]
