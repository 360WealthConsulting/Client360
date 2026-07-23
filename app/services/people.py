"""Editable canonical person contact/address fields (Sprint 2).

Staff can correct a client's contact and address details on the canonical ``people`` record.
Imports write ``source_contacts`` and never overwrite ``people`` (promotion only inserts new
people), so these staff edits are the durable source of truth for display. Identity fields such
as ``full_name`` are intentionally not editable here.

Each edit records an audit event (changed field NAMES only — no PII values in the audit trail)
and a client timeline event.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db import engine, people
from app.security.audit import write_audit_event
from app.security.service import normalize_email
from app.services.timeline import add_timeline_event

#: The only person columns staff may edit through the profile.
EDITABLE_FIELDS = (
    "primary_email", "primary_phone", "preferred_name",
    "address_line_1", "address_line_2", "city", "state", "postal_code",
)


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def update_person_contact(person_id: int, updates: dict, *, actor_user_id: int | None,
                          request_id: str | None = None, conn=None) -> list[str]:
    """Apply staff edits to a person's contact/address fields. Only fields whose value actually
    changes are written. Returns the sorted list of changed field names ([] if nothing changed).
    Raises ``ValueError`` if the person does not exist. Records timeline + audit when something
    changed (and keeps ``normalized_email``/``normalized_phone`` in sync)."""
    clean = {}
    for field in EDITABLE_FIELDS:
        if field in updates:
            clean[field] = (updates[field] or "").strip() or None

    def _do(c):
        current = c.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
        if current is None:
            raise ValueError("Person not found.")
        changed = {k: v for k, v in clean.items() if current[k] != v}
        if not changed:
            return []
        values = dict(changed)
        if "primary_email" in changed:
            values["normalized_email"] = (
                normalize_email(changed["primary_email"]) if changed["primary_email"] else None
            )
        if "primary_phone" in changed:
            values["normalized_phone"] = _normalize_phone(changed["primary_phone"])
        c.execute(people.update().where(people.c.id == person_id).values(**values))
        _changed = sorted(changed.keys())
        # (D.35) Publish the updated business FACT — field NAMES only (never values), transactional.
        from app.services.events import publisher
        publisher.publish_safe("people.person_updated",
                               {"person_id": person_id, "changed_fields": _changed}, conn=c,
                               producer="people.service", subject_ref=f"person:{person_id}")
        return _changed

    changed_fields = _run(conn, _do)
    if changed_fields:
        add_timeline_event(
            person_id=person_id, source="client360", event_type="person_updated",
            title="Client details updated", summary="Updated: " + ", ".join(changed_fields),
            event_metadata={"fields": changed_fields, "actor_user_id": actor_user_id},
        )
        write_audit_event(
            action="person.updated", entity_type="person", entity_id=person_id,
            actor_user_id=actor_user_id, request_id=request_id or f"person-{uuid.uuid4()}",
            metadata={"fields": changed_fields},
        )
    return changed_fields


def _run(conn, fn):
    if conn is not None:
        return fn(conn)
    with engine.begin() as c:
        return fn(c)
