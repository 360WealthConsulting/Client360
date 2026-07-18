from __future__ import annotations

from sqlalchemy import select, text

from app.db import audit_events, engine
from app.security.audit_chain import (
    DEFAULT_CHAIN,
    GENESIS_PREV_HASH,
    HASH_VERSION,
    chain_lock_key,
    compute_entry_hash,
    content_from_fields,
)
from app.security.redaction import redact_metadata


def write_audit_event(*, action, entity_type, request_id, actor_user_id=None, entity_id=None, outcome="success", ip_address=None, user_agent=None, metadata=None, chain_id=DEFAULT_CHAIN):
    stored_entity_id = str(entity_id) if entity_id is not None else None
    redacted = redact_metadata(metadata)
    content = content_from_fields(
        actor_user_id=actor_user_id, action=action, entity_type=entity_type,
        entity_id=stored_entity_id, outcome=outcome, request_id=request_id,
        ip_address=ip_address, user_agent=user_agent, metadata=redacted,
    )
    with engine.begin() as connection:
        # Serialize writers on this chain so the hash chain cannot fork (F3.2/ADR-015).
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": chain_lock_key(chain_id)})
        prev = connection.execute(
            select(audit_events.c.entry_hash)
            .where(audit_events.c.chain_id == chain_id, audit_events.c.entry_hash.isnot(None))
            .order_by(audit_events.c.id.desc()).limit(1)
        ).scalar()
        prev_hash = prev or GENESIS_PREV_HASH
        entry_hash = compute_entry_hash(content, prev_hash=prev_hash, chain_id=chain_id, hash_version=HASH_VERSION)
        return connection.execute(
            audit_events.insert().values(
                actor_user_id=actor_user_id, action=action, entity_type=entity_type,
                entity_id=stored_entity_id, outcome=outcome, request_id=request_id,
                ip_address=ip_address, user_agent=user_agent, metadata=redacted,
                prev_hash=prev_hash, entry_hash=entry_hash, hash_version=HASH_VERSION, chain_id=chain_id,
            ).returning(audit_events.c.id)
        ).scalar_one()


def audit_denied(request, *, action, entity_type, entity_id=None, actor_user_id=None, detail=None):
    """Record an immutable audit event for a denied high-risk mutation.

    Tolerates a missing/partial ``request`` (used from tests and non-HTTP call
    sites) and never raises — denial auditing must not turn a 403 into a 500.
    """
    try:
        request_id = getattr(getattr(request, "state", None), "request_id", None) or f"denied-{action}"
        client = getattr(request, "client", None)
        headers = getattr(request, "headers", None)
        return write_audit_event(
            action=action, entity_type=entity_type, entity_id=entity_id,
            actor_user_id=actor_user_id, outcome="denied", request_id=request_id,
            ip_address=getattr(client, "host", None) if client else None,
            user_agent=headers.get("user-agent") if headers is not None else None,
            metadata={"detail": detail} if detail else None,
        )
    except Exception:
        return None
