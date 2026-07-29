"""Portal client profile — read + audited edit of the fields a client may change.

Phone / email / address live on the ``people`` record; preferred contact method + communication
preferences live on ``portal_accounts``. Every change writes a ``portal.profile.updated`` audit
event capturing exactly which fields changed (values redacted by the audit layer).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.db import engine, people, portal_accounts
from app.security.audit import write_audit_event

# Editable person columns (address is line-1 + city/state/postal).
_PERSON_FIELDS = {"primary_email", "primary_phone", "address_line_1", "city", "state", "postal_code"}
_ACCOUNT_FIELDS = {"preferred_contact_method", "communication_preferences"}
# Friendly aliases accepted from the client payload.
_ALIASES = {"email": "primary_email", "phone": "primary_phone", "address": "address_line_1"}


def get_profile(principal) -> dict:
    with engine.connect() as conn:
        person = conn.execute(select(
            people.c.id, people.c.full_name, people.c.primary_email, people.c.primary_phone,
            people.c.address_line_1, people.c.city, people.c.state, people.c.postal_code)
            .where(people.c.id == principal.person_id)).mappings().first()
        account = conn.execute(select(
            portal_accounts.c.preferred_contact_method, portal_accounts.c.communication_preferences)
            .where(portal_accounts.c.id == principal.account_id)).mappings().first()
    return {**(dict(person) if person else {}), **(dict(account) if account else {})}


def update_profile(principal, changes: dict, *, request_id="portal", ip_address=None) -> dict:
    """Apply a client's profile edits and audit them. Returns the fields that changed."""
    normalized = {_ALIASES.get(k, k): v for k, v in (changes or {}).items() if v is not None}
    person_updates = {k: v for k, v in normalized.items() if k in _PERSON_FIELDS}
    account_updates = {k: v for k, v in normalized.items() if k in _ACCOUNT_FIELDS}

    with engine.begin() as conn:
        if person_updates:
            values = dict(person_updates)
            if "primary_email" in values and values["primary_email"]:
                values["normalized_email"] = values["primary_email"].strip().lower()
            conn.execute(people.update().where(people.c.id == principal.person_id).values(**values))
        if account_updates:
            conn.execute(portal_accounts.update().where(portal_accounts.c.id == principal.account_id).values(
                updated_at=datetime.now(UTC), **account_updates))

    changed = sorted([*person_updates, *account_updates])
    if changed:
        write_audit_event(action="portal.profile.updated", entity_type="portal_account",
                          entity_id=principal.account_id, actor_user_id=None, request_id=request_id,
                          ip_address=ip_address,
                          metadata={"portal_account_id": principal.account_id, "fields": changed})
    return {"changed": changed}
