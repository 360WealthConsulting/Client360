"""Secret references, rotation & certificate references (Phase D.25) — metadata only, no plaintext.

A ``security_secret_references`` row is either a POINTER to an existing encrypted store
(``reference_kind`` = microsoft_account / integration_credential / external_vault) or holds Fernet
ciphertext (``encrypted_secret``) produced by ``app.security.security_crypto`` — **never plaintext**.
Ciphertext is stripped from every response. Rotation records metadata (last/next rotation) only —
it performs no cryptographic key operation on the underlying store. Certificate references are
metadata only (fingerprint/serial/validity window); no private key/PEM is ever stored.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.database.security_tables import (
    CERT_STATUSES,
    ROTATION_SCHEDULES,
    SECRET_REFERENCE_KINDS,
    SECRET_STATUSES,
)
from app.db import engine
from app.db import security_certificate_references as certs_t
from app.db import security_secret_references as secrets_t

from .common import (
    SecurityError,
    SecurityNotFound,
    encrypt_secret,
    now,
    publish_timeline,
    record_event,
    write_audit,
)

_ROTATION_SECONDS = {"monthly": 2592000, "quarterly": 7776000, "semiannual": 15552000,
                     "annual": 31536000}


def _strip(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "secret_ciphertext"}


# --- secret references (never expose ciphertext) -----------------------------

def list_secret_references(*, status=None, reference_kind=None):
    with engine.connect() as c:
        stmt = select(secrets_t).order_by(secrets_t.c.code)
        if status:
            stmt = stmt.where(secrets_t.c.status == status)
        if reference_kind:
            stmt = stmt.where(secrets_t.c.reference_kind == reference_kind)
        return [_strip(dict(r)) for r in c.execute(stmt).mappings()]


def get_secret_reference(principal, secret_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(secrets_t).where(secrets_t.c.id == secret_id)).mappings().first()
    return _strip(dict(row)) if row else None


def create_secret_reference(principal, *, code, name, reference_kind="encrypted_secret",
                            reference_id=None, secret=None, owner_user_id=None, algorithm=None,
                            storage_reference=None, rotation_schedule="manual", rotation_policy_id=None,
                            expires_at=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise SecurityError("code and name are required")
    if reference_kind not in SECRET_REFERENCE_KINDS:
        raise SecurityError(f"invalid reference_kind {reference_kind!r}")
    if rotation_schedule not in ROTATION_SCHEDULES:
        raise SecurityError(f"invalid rotation_schedule {rotation_schedule!r}")
    # Only an encrypted_secret kind stores ciphertext; every other kind is a pointer (no ciphertext).
    ciphertext = encrypt_secret(secret) if reference_kind == "encrypted_secret" else None
    ts = now()
    next_rotation = _next_rotation(rotation_schedule, ts)
    with engine.begin() as c:
        if c.scalar(select(secrets_t.c.id).where(secrets_t.c.code == code)) is not None:
            raise SecurityError(f"secret reference code {code!r} already exists")
        row = c.execute(secrets_t.insert().values(
            code=code, name=name.strip(), reference_kind=reference_kind, reference_id=reference_id,
            secret_ciphertext=ciphertext, owner_user_id=owner_user_id, algorithm=algorithm,
            storage_reference=storage_reference, rotation_schedule=rotation_schedule,
            rotation_policy_id=rotation_policy_id, next_rotation_at=next_rotation, expires_at=expires_at,
            status="active", created_by_user_id=actor_user_id).returning(*secrets_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="secret", entity_id=row["id"], event_type="secret_created",
                     actor_user_id=actor_user_id, payload={"kind": reference_kind})
    write_audit("security.secret_created", entity_type="secret", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"kind": reference_kind})   # references only
    return _strip(row)


def rotate_secret_reference(principal, secret_id: int, *, secret=None, storage_reference=None,
                            actor_user_id=None) -> dict:
    """Record a rotation: stamps ``last_rotated_at``/``next_rotation_at`` and (for an encrypted_secret)
    replaces the stored ciphertext. Metadata only — never returns the secret."""
    ts = now()
    with engine.begin() as c:
        ref = c.execute(select(secrets_t).where(secrets_t.c.id == secret_id)).mappings().first()
        if ref is None:
            raise SecurityNotFound(str(secret_id))
        ref = dict(ref)
        values = {"last_rotated_at": ts, "next_rotation_at": _next_rotation(ref["rotation_schedule"], ts),
                  "status": "active", "updated_at": ts}
        if storage_reference is not None:
            values["storage_reference"] = storage_reference
        if secret is not None and ref["reference_kind"] == "encrypted_secret":
            values["secret_ciphertext"] = encrypt_secret(secret)
        row = c.execute(secrets_t.update().where(secrets_t.c.id == secret_id).values(**values)
                        .returning(*secrets_t.c)).mappings().one()
        record_event(c, entity_type="secret", entity_id=secret_id, event_type="secret_rotated",
                     actor_user_id=actor_user_id)
        row = dict(row)
    write_audit("security.secret_rotated", entity_type="secret", entity_id=secret_id,
                actor_user_id=actor_user_id)
    publish_timeline(row, "secret_rotated", title=f"Secret rotated: {row['name']}")
    return _strip(row)


def set_secret_status(principal, secret_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in SECRET_STATUSES:
        raise SecurityError(f"invalid status {status!r}")
    with engine.begin() as c:
        ref = c.execute(select(secrets_t).where(secrets_t.c.id == secret_id)).mappings().first()
        if ref is None:
            raise SecurityNotFound(str(secret_id))
        row = c.execute(secrets_t.update().where(secrets_t.c.id == secret_id).values(
            status=status, updated_at=now()).returning(*secrets_t.c)).mappings().one()
        record_event(c, entity_type="secret", entity_id=secret_id, event_type=f"secret_{status}",
                     from_status=ref["status"], to_status=status, actor_user_id=actor_user_id)
        return _strip(dict(row))


def _next_rotation(schedule, base):
    secs = _ROTATION_SECONDS.get(schedule)
    return base + timedelta(seconds=secs) if secs else None


def overdue_rotations(principal) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(select(secrets_t).where(
            secrets_t.c.status == "active", secrets_t.c.next_rotation_at.is_not(None),
            secrets_t.c.next_rotation_at <= now())).mappings()
        return [_strip(dict(r)) for r in rows]


# --- certificate references (metadata only; no private key/PEM) --------------

def list_certificates(*, status=None):
    with engine.connect() as c:
        stmt = select(certs_t).order_by(certs_t.c.code)
        if status:
            stmt = stmt.where(certs_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_certificate_reference(principal, *, code, name, subject=None, issuer=None, serial=None,
                                 fingerprint=None, not_before=None, not_after=None,
                                 storage_reference=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise SecurityError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(certs_t.c.id).where(certs_t.c.code == code)) is not None:
            raise SecurityError(f"certificate code {code!r} already exists")
        row = c.execute(certs_t.insert().values(
            code=code, name=name.strip(), subject=subject, issuer=issuer, serial=serial,
            fingerprint=fingerprint, not_before=not_before, not_after=not_after,
            storage_reference=storage_reference, status="valid",
            created_by_user_id=actor_user_id).returning(*certs_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="certificate", entity_id=row["id"],
                     event_type="certificate_created", actor_user_id=actor_user_id)
        return row


def renew_certificate_reference(principal, cert_id: int, *, not_before=None, not_after=None,
                                fingerprint=None, serial=None, actor_user_id=None) -> dict:
    with engine.begin() as c:
        cert = c.execute(select(certs_t).where(certs_t.c.id == cert_id)).mappings().first()
        if cert is None:
            raise SecurityNotFound(str(cert_id))
        values = {"status": "valid", "last_renewed_at": now(), "updated_at": now()}
        for k, v in (("not_before", not_before), ("not_after", not_after),
                     ("fingerprint", fingerprint), ("serial", serial)):
            if v is not None:
                values[k] = v
        row = c.execute(certs_t.update().where(certs_t.c.id == cert_id).values(**values)
                        .returning(*certs_t.c)).mappings().one()
        record_event(c, entity_type="certificate", entity_id=cert_id,
                     event_type="certificate_renewed", actor_user_id=actor_user_id)
        row = dict(row)
    write_audit("security.certificate_renewed", entity_type="certificate", entity_id=cert_id,
                actor_user_id=actor_user_id)
    publish_timeline(row, "certificate_renewed", title=f"Certificate renewed: {row['name']}")
    return row


def set_certificate_status(principal, cert_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in CERT_STATUSES:
        raise SecurityError(f"invalid status {status!r}")
    with engine.begin() as c:
        cert = c.execute(select(certs_t).where(certs_t.c.id == cert_id)).mappings().first()
        if cert is None:
            raise SecurityNotFound(str(cert_id))
        row = c.execute(certs_t.update().where(certs_t.c.id == cert_id).values(
            status=status, updated_at=now()).returning(*certs_t.c)).mappings().one()
        record_event(c, entity_type="certificate", entity_id=cert_id, event_type=f"certificate_{status}",
                     from_status=cert["status"], to_status=status, actor_user_id=actor_user_id)
        return dict(row)


def metrics(principal) -> dict:
    with engine.connect() as c:
        overdue = c.scalar(select(func.count()).select_from(secrets_t).where(
            secrets_t.c.status == "active", secrets_t.c.next_rotation_at.is_not(None),
            secrets_t.c.next_rotation_at <= now())) or 0
        expired_certs = c.scalar(select(func.count()).select_from(certs_t)
                                 .where(certs_t.c.status.in_(("expired", "revoked")))) or 0
    return {"overdue_secret_rotations": overdue, "expired_certificates": expired_certs}
