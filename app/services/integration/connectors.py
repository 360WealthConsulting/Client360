"""Providers, connectors, credential references & data profiles (Phase D.24).

Providers are the vendor registry (seeded disabled, mirroring the disabled-port pattern). Connectors
are configured instances (config carries NO secrets). Credential references are POINTERS to an
existing encrypted store (``microsoft_accounts``) or Fernet ciphertext — **never plaintext**. Data
profiles are import/export mapping definitions. Managing these requires ``integration.manage``;
connection-status transitions require ``integration.execute`` (enforced in-route).
"""
from __future__ import annotations

from sqlalchemy import select

from app.database.integration_tables import (
    CONNECTION_STATUSES,
    CONNECTOR_DIRECTIONS,
    CREDENTIAL_TYPES,
    DATA_FORMATS,
    PROFILE_TYPES,
    PROVIDER_TYPES,
    REFERENCE_KINDS,
)
from app.db import engine
from app.db import integration_connectors as connectors_t
from app.db import integration_credential_references as creds_t
from app.db import integration_data_profiles as profiles_t
from app.db import integration_providers as providers_t

from .common import (
    IntegrationError,
    IntegrationNotFound,
    encrypt_secret,
    now,
    publish_timeline,
    record_event,
    write_audit,
)


def _create_unique(table, code, values):
    code = (code or "").strip()
    if not code:
        raise IntegrationError("code is required")
    with engine.begin() as c:
        if c.scalar(select(table.c.id).where(table.c.code == code)) is not None:
            raise IntegrationError(f"code {code!r} already exists")
        return dict(c.execute(table.insert().values(code=code, **values).returning(*table.c)).mappings().one())


# --- providers ---------------------------------------------------------------

def list_providers(*, provider_type=None):
    with engine.connect() as c:
        stmt = select(providers_t).order_by(providers_t.c.code)
        if provider_type:
            stmt = stmt.where(providers_t.c.provider_type == provider_type)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_provider(*, code):
    with engine.connect() as c:
        row = c.execute(select(providers_t).where(providers_t.c.code == code)).mappings().first()
        return dict(row) if row else None


def create_provider(*, code, name, provider_type="other", category=None, description=None,
                    actor_user_id=None):
    if not (name or "").strip():
        raise IntegrationError("name is required")
    if provider_type not in PROVIDER_TYPES:
        raise IntegrationError(f"invalid provider_type {provider_type!r}")
    return _create_unique(providers_t, code, {
        "name": name.strip(), "provider_type": provider_type, "category": category,
        "enabled": False, "description": description, "created_by_user_id": actor_user_id})


def set_provider_enabled(principal, provider_id: int, enabled: bool, *, actor_user_id=None) -> dict:
    with engine.begin() as c:
        if c.scalar(select(providers_t.c.id).where(providers_t.c.id == provider_id)) is None:
            raise IntegrationNotFound(str(provider_id))
        row = c.execute(providers_t.update().where(providers_t.c.id == provider_id)
                        .values(enabled=bool(enabled), updated_at=now()).returning(*providers_t.c)).mappings().one()
        record_event(c, entity_type="provider", entity_id=provider_id,
                     event_type=f"provider_{'enabled' if enabled else 'disabled'}", actor_user_id=actor_user_id)
        return dict(row)


# --- credential references (pointers / ciphertext — never plaintext) --------

def list_credentials(*, provider_id=None):
    with engine.connect() as c:
        stmt = select(creds_t).order_by(creds_t.c.code)
        if provider_id is not None:
            stmt = stmt.where(creds_t.c.provider_id == provider_id)
        # Never expose the ciphertext in listings.
        return [{k: v for k, v in dict(r).items() if k != "secret_ciphertext"}
                for r in c.execute(stmt).mappings()]


def create_credential_reference(*, code, credential_type="oauth", reference_kind="microsoft_account",
                                reference_id=None, secret=None, provider_id=None, scopes=None,
                                actor_user_id=None) -> dict:
    if credential_type not in CREDENTIAL_TYPES:
        raise IntegrationError(f"invalid credential_type {credential_type!r}")
    if reference_kind not in REFERENCE_KINDS:
        raise IntegrationError(f"invalid reference_kind {reference_kind!r}")
    ciphertext = encrypt_secret(secret) if reference_kind == "encrypted_secret" else None
    row = _create_unique(creds_t, code, {
        "provider_id": provider_id, "credential_type": credential_type, "reference_kind": reference_kind,
        "reference_id": reference_id, "secret_ciphertext": ciphertext, "scopes": scopes,
        "status": "active", "created_by_user_id": actor_user_id})
    write_audit("integration.credential_created", entity_type="credential_reference", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"kind": reference_kind})   # references only
    return {k: v for k, v in row.items() if k != "secret_ciphertext"}


def rotate_credential_reference(principal, credential_id: int, *, secret=None, reference_id=None,
                                actor_user_id=None) -> dict:
    with engine.begin() as c:
        cred = c.execute(select(creds_t).where(creds_t.c.id == credential_id)).mappings().first()
        if cred is None:
            raise IntegrationNotFound(str(credential_id))
        values = {"rotated_at": now(), "updated_at": now()}
        if reference_id is not None:
            values["reference_id"] = reference_id
        if secret is not None and cred["reference_kind"] == "encrypted_secret":
            values["secret_ciphertext"] = encrypt_secret(secret)
        c.execute(creds_t.update().where(creds_t.c.id == credential_id).values(**values))
        record_event(c, entity_type="credential_reference", entity_id=credential_id,
                     event_type="credential_rotated", actor_user_id=actor_user_id)
    write_audit("integration.credential_rotated", entity_type="credential_reference",
                entity_id=credential_id, actor_user_id=actor_user_id)
    return {"id": credential_id, "rotated": True}


# --- connectors (instance + config + status) ---------------------------------

def list_connectors(*, provider_id=None, status=None):
    with engine.connect() as c:
        stmt = select(connectors_t).order_by(connectors_t.c.code)
        if provider_id is not None:
            stmt = stmt.where(connectors_t.c.provider_id == provider_id)
        if status:
            stmt = stmt.where(connectors_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_connector(principal, connector_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(connectors_t).where(connectors_t.c.id == connector_id)).mappings().first()
        return dict(row) if row else None


def create_connector(principal, *, provider_id, code, name, direction="inbound", config=None,
                     credential_reference_id=None, actor_user_id=None) -> dict:
    if not (name or "").strip():
        raise IntegrationError("name is required")
    if direction not in CONNECTOR_DIRECTIONS:
        raise IntegrationError(f"invalid direction {direction!r}")
    if config and any(k in str(config).lower() for k in ("password", "secret", "api_key", "token")):
        raise IntegrationError("connector config must not contain secrets (use a credential reference)")
    with engine.begin() as c:
        if c.scalar(select(providers_t.c.id).where(providers_t.c.id == provider_id)) is None:
            raise IntegrationError("provider not found")
    row = _create_unique(connectors_t, code, {
        "provider_id": provider_id, "name": name.strip(), "direction": direction,
        "status": "not_connected", "config": config, "credential_reference_id": credential_reference_id,
        "enabled": False, "created_by_user_id": actor_user_id})
    return row


def configure_connector(principal, connector_id: int, *, config=None, credential_reference_id=None,
                        actor_user_id=None) -> dict:
    if config and any(k in str(config).lower() for k in ("password", "secret", "api_key", "token")):
        raise IntegrationError("connector config must not contain secrets (use a credential reference)")
    with engine.begin() as c:
        if c.scalar(select(connectors_t.c.id).where(connectors_t.c.id == connector_id)) is None:
            raise IntegrationNotFound(str(connector_id))
        values = {"updated_at": now()}
        if config is not None:
            values["config"] = config
        if credential_reference_id is not None:
            values["credential_reference_id"] = credential_reference_id
        row = c.execute(connectors_t.update().where(connectors_t.c.id == connector_id)
                        .values(**values).returning(*connectors_t.c)).mappings().one()
        record_event(c, entity_type="connector", entity_id=connector_id, event_type="connector_configured",
                     actor_user_id=actor_user_id)
        return dict(row)


def set_connector_status(principal, connector_id: int, status: str, *, error=None, actor_user_id=None) -> dict:
    if status not in CONNECTION_STATUSES:
        raise IntegrationError(f"invalid status {status!r}")
    with engine.begin() as c:
        conn = c.execute(select(connectors_t).where(connectors_t.c.id == connector_id)).mappings().first()
        if conn is None:
            raise IntegrationNotFound(str(connector_id))
        row = c.execute(connectors_t.update().where(connectors_t.c.id == connector_id).values(
            status=status, last_status_at=now(), last_error=error, enabled=(status == "connected"),
            updated_at=now()).returning(*connectors_t.c)).mappings().one()
        record_event(c, entity_type="connector", entity_id=connector_id, event_type=f"connector_{status}",
                     from_status=conn["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"integration.connector_{status}", entity_type="connector", entity_id=connector_id,
                actor_user_id=actor_user_id)
    # Connectors are firm-level (no client anchor) -> timeline publication is skipped by the guard.
    publish_timeline(row, "connected" if status == "connected" else "disconnected")
    return row


# --- data profiles (import / export) -----------------------------------------

def list_data_profiles(*, profile_type=None):
    with engine.connect() as c:
        stmt = select(profiles_t).order_by(profiles_t.c.code)
        if profile_type:
            stmt = stmt.where(profiles_t.c.profile_type == profile_type)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_data_profile(*, code, name, profile_type="import", data_format="csv", provider_id=None,
                        mapping=None, transformation=None, delivery=None, actor_user_id=None) -> dict:
    if not (name or "").strip():
        raise IntegrationError("name is required")
    if profile_type not in PROFILE_TYPES:
        raise IntegrationError(f"invalid profile_type {profile_type!r}")
    if data_format not in DATA_FORMATS:
        raise IntegrationError(f"invalid data_format {data_format!r}")
    return _create_unique(profiles_t, code, {
        "name": name.strip(), "profile_type": profile_type, "data_format": data_format,
        "provider_id": provider_id, "mapping": mapping, "transformation": transformation,
        "delivery": delivery, "created_by_user_id": actor_user_id})
