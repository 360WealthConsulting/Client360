from datetime import datetime, timezone
import uuid
from sqlalchemy import select
from app.db import engine, signature_requests
from app.services.timeline import add_timeline_event

class SignatureProviderRegistry:
    def __init__(self): self._providers = {}
    def register(self, provider): self._providers[provider.key] = provider
    def get(self, key):
        if key not in self._providers: raise ValueError(f"Signature provider '{key}' is not configured")
        return self._providers[key]

registry = SignatureProviderRegistry()

def create_signature_request(*, provider_key, person_id, household_id, requested_by_user_id,
                             documents, recipients, callback_url, workflow_instance_id=None,
                             workflow_step_id=None, metadata=None):
    provider = registry.get(provider_key)
    result = provider.create_request(recipients=recipients, documents=documents, callback_url=callback_url, metadata=metadata or {})
    with engine.begin() as connection:
        request_id = connection.execute(signature_requests.insert().values(provider_key=provider_key, external_id=result.external_id, person_id=person_id, household_id=household_id, workflow_instance_id=workflow_instance_id, workflow_step_id=workflow_step_id, status=result.status, request_payload={"documents": documents, "recipients": recipients, "provider_metadata": result.metadata}, requested_by_user_id=requested_by_user_id).returning(signature_requests.c.id)).scalar_one()
    add_timeline_event(person_id=person_id, household_id=household_id, source="signature_provider", event_type="signature_requested", title="Signature requested", external_id=f"signature-request-{request_id}", event_metadata={"provider": provider_key, "workflow_step_id": workflow_step_id})
    return request_id

def apply_signature_event(provider_key, external_id, status, metadata=None):
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        row = connection.execute(select(signature_requests).where(signature_requests.c.provider_key == provider_key, signature_requests.c.external_id == external_id).with_for_update()).mappings().one_or_none()
        if not row: raise ValueError("Signature request not found")
        connection.execute(signature_requests.update().where(signature_requests.c.id == row["id"]).values(status=status, completed_at=now if status == "completed" else None, updated_at=now))
    add_timeline_event(person_id=row["person_id"], household_id=row["household_id"], source="signature_provider", event_type=f"signature_{status}", title=f"Signature {status}", external_id=f"signature-{row['id']}-{status}", event_metadata=metadata or {})
    return row["id"]
