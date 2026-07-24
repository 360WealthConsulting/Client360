"""Client Portal consent + electronic-delivery records (Phase D.43).

A thin, append-only-in-spirit service over the ``portal_consents`` ledger: record acceptance/withdrawal
of the versioned portal agreements (terms, privacy, electronic delivery, secure messaging, document
delivery) and answer "has this account accepted version X of consent Y?". Every write delegates the audit
trail to the authoritative audit ledger (references-only). No client PII is stored here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import engine, portal_consents
from app.portal import stats
from app.security.audit import write_audit_event

CONSENT_TYPES = ("portal_terms", "privacy_notice", "electronic_delivery", "secure_messaging",
                 "document_delivery")


def _now():
    return datetime.now(UTC)


def record_consent(account_id, consent_type, version, *, request_id, accepted=True, metadata=None):
    """Record an accept/decline for a versioned consent. Idempotent per (account, type, version):
    a repeat accept returns the existing row rather than duplicating (the unique constraint enforces it).
    Metadata holds only non-PII delivery context (channel, user-agent hash) — never client data."""
    if consent_type not in CONSENT_TYPES:
        raise ValueError("Unknown consent type")
    state = "accepted" if accepted else "declined"
    now = _now()
    with engine.begin() as connection:
        existing = connection.execute(
            select(portal_consents).where(
                portal_consents.c.portal_account_id == account_id,
                portal_consents.c.consent_type == consent_type,
                portal_consents.c.version == version,
            ).with_for_update()
        ).mappings().one_or_none()
        if existing is not None:
            if existing["state"] == state:
                return existing["id"]
            connection.execute(portal_consents.update().where(portal_consents.c.id == existing["id"]).values(
                state=state, accepted_at=now if accepted else existing["accepted_at"]))
            consent_id = existing["id"]
        else:
            consent_id = connection.execute(portal_consents.insert().values(
                consent_uid=str(uuid.uuid4()), portal_account_id=account_id, consent_type=consent_type,
                version=version, state=state, accepted_at=now if accepted else None,
                request_metadata=metadata or {},
            ).returning(portal_consents.c.id)).scalar_one()
    stats.note("consents_accepted" if accepted else "scope_denials")
    write_audit_event(action="portal.consent.recorded", entity_type="portal_consent", entity_id=consent_id,
                      request_id=request_id, outcome="success",
                      metadata={"consent_type": consent_type, "version": version, "state": state})
    return consent_id


def withdraw_consent(account_id, consent_type, *, request_id):
    """Withdraw the most recent accepted consent of a type (electronic delivery, secure messaging).
    Returns the withdrawn row id or None if there was nothing to withdraw."""
    now = _now()
    with engine.begin() as connection:
        row = connection.execute(
            select(portal_consents).where(
                portal_consents.c.portal_account_id == account_id,
                portal_consents.c.consent_type == consent_type,
                portal_consents.c.state == "accepted",
            ).order_by(portal_consents.c.created_at.desc()).limit(1).with_for_update()
        ).mappings().one_or_none()
        if row is None:
            return None
        connection.execute(portal_consents.update().where(portal_consents.c.id == row["id"]).values(
            state="withdrawn", withdrawn_at=now))
    stats.note("consents_withdrawn")
    write_audit_event(action="portal.consent.withdrawn", entity_type="portal_consent", entity_id=row["id"],
                      request_id=request_id, outcome="success",
                      metadata={"consent_type": consent_type})
    return row["id"]


def has_accepted(account_id, consent_type, *, version=None):
    """True if the account currently holds an accepted (not withdrawn) consent of this type,
    optionally of a specific version."""
    with engine.connect() as connection:
        conditions = [
            portal_consents.c.portal_account_id == account_id,
            portal_consents.c.consent_type == consent_type,
            portal_consents.c.state == "accepted",
        ]
        if version is not None:
            conditions.append(portal_consents.c.version == version)
        return connection.execute(select(portal_consents.c.id).where(*conditions).limit(1)).scalar() is not None


def list_consents(account_id):
    """Full consent history for an account (for the security center / preferences surface)."""
    with engine.connect() as connection:
        rows = connection.execute(
            select(portal_consents).where(portal_consents.c.portal_account_id == account_id)
            .order_by(portal_consents.c.created_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def electronic_delivery_active(account_id):
    """Electronic-delivery record check — used before delivering documents/notices electronically."""
    return has_accepted(account_id, "electronic_delivery")
