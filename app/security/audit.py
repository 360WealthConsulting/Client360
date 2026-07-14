from __future__ import annotations

from typing import Any
from app.db import audit_events, engine
from app.security.redaction import redact_metadata

def write_audit_event(*, action, entity_type, request_id, actor_user_id=None, entity_id=None, outcome="success", ip_address=None, user_agent=None, metadata=None):
    with engine.begin() as connection:
        return connection.execute(audit_events.insert().values(actor_user_id=actor_user_id, action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id is not None else None, outcome=outcome, request_id=request_id, ip_address=ip_address, user_agent=user_agent, metadata=redact_metadata(metadata)).returning(audit_events.c.id)).scalar_one()
