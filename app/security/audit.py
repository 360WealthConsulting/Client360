from __future__ import annotations

from app.db import audit_events, engine
from app.security.redaction import redact_metadata

def write_audit_event(*, action, entity_type, request_id, actor_user_id=None, entity_id=None, outcome="success", ip_address=None, user_agent=None, metadata=None):
    with engine.begin() as connection:
        return connection.execute(audit_events.insert().values(actor_user_id=actor_user_id, action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id is not None else None, outcome=outcome, request_id=request_id, ip_address=ip_address, user_agent=user_agent, metadata=redact_metadata(metadata)).returning(audit_events.c.id)).scalar_one()


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
