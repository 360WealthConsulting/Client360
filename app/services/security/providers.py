"""Authentication / identity / federation providers (Phase D.25) — metadata only.

Models authentication-provider, identity-provider, and identity-federation METADATA. Providers are
seeded/created **disabled** and never replace the live authentication (``app.security.service``) or
Microsoft 365 OAuth (``app.services.microsoft_identity``) — enabling a provider records intent
metadata only. Provider ``config`` carries NO secrets; a secret is referenced via a
``security_secret_references`` row (pointer or Fernet ciphertext). Managing providers requires
``security.manage``; enabling/disabling requires ``security.execute`` (enforced in-route).
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.security_tables import (
    PROVIDER_KINDS,
    PROVIDER_PROTOCOLS,
    PROVIDER_STATUSES,
)
from app.db import engine
from app.db import security_identity_providers as providers_t

from .common import (
    SecurityError,
    SecurityNotFound,
    now,
    record_event,
    write_audit,
)


def list_providers(*, provider_kind=None, enabled=None):
    with engine.connect() as c:
        stmt = select(providers_t).order_by(providers_t.c.code)
        if provider_kind:
            stmt = stmt.where(providers_t.c.provider_kind == provider_kind)
        if enabled is not None:
            stmt = stmt.where(providers_t.c.enabled.is_(bool(enabled)))
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_provider(principal, provider_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(providers_t).where(providers_t.c.id == provider_id)).mappings().first()
        return dict(row) if row else None


def create_provider(principal, *, code, name, provider_kind="authentication", protocol="oauth2",
                    config=None, credential_reference_id=None, microsoft_account_reference=False,
                    description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise SecurityError("code and name are required")
    if provider_kind not in PROVIDER_KINDS:
        raise SecurityError(f"invalid provider_kind {provider_kind!r}")
    if protocol not in PROVIDER_PROTOCOLS:
        raise SecurityError(f"invalid protocol {protocol!r}")
    if config and any(k in str(config).lower() for k in ("password", "secret", "api_key", "token")):
        raise SecurityError("provider config must not contain secrets (use a secret reference)")
    with engine.begin() as c:
        if c.scalar(select(providers_t.c.id).where(providers_t.c.code == code)) is not None:
            raise SecurityError(f"provider code {code!r} already exists")
        row = c.execute(providers_t.insert().values(
            code=code, name=name.strip(), provider_kind=provider_kind, protocol=protocol,
            status="configured", enabled=False, microsoft_account_reference=bool(microsoft_account_reference),
            config=config, credential_reference_id=credential_reference_id, description=description,
            created_by_user_id=actor_user_id).returning(*providers_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="provider", entity_id=row["id"], event_type="provider_created",
                     actor_user_id=actor_user_id, payload={"provider_kind": provider_kind})
    write_audit("security.provider_created", entity_type="provider", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"kind": provider_kind})
    return row


def set_provider_status(principal, provider_id: int, status: str, *, actor_user_id=None) -> dict:
    """Set a provider's status (configured/enabled/disabled). This records metadata only; the live
    authentication and Microsoft 365 OAuth are never altered by this call."""
    if status not in PROVIDER_STATUSES:
        raise SecurityError(f"invalid status {status!r}")
    with engine.begin() as c:
        prov = c.execute(select(providers_t).where(providers_t.c.id == provider_id)).mappings().first()
        if prov is None:
            raise SecurityNotFound(str(provider_id))
        row = c.execute(providers_t.update().where(providers_t.c.id == provider_id).values(
            status=status, enabled=(status == "enabled"), updated_at=now())
            .returning(*providers_t.c)).mappings().one()
        record_event(c, entity_type="provider", entity_id=provider_id, event_type=f"provider_{status}",
                     from_status=prov["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"security.provider_{status}", entity_type="provider", entity_id=provider_id,
                actor_user_id=actor_user_id)
    return row


def configure_provider(principal, provider_id: int, *, config=None, credential_reference_id=None,
                       actor_user_id=None) -> dict:
    if config and any(k in str(config).lower() for k in ("password", "secret", "api_key", "token")):
        raise SecurityError("provider config must not contain secrets (use a secret reference)")
    with engine.begin() as c:
        if c.scalar(select(providers_t.c.id).where(providers_t.c.id == provider_id)) is None:
            raise SecurityNotFound(str(provider_id))
        values = {"updated_at": now()}
        if config is not None:
            values["config"] = config
        if credential_reference_id is not None:
            values["credential_reference_id"] = credential_reference_id
        row = c.execute(providers_t.update().where(providers_t.c.id == provider_id).values(**values)
                        .returning(*providers_t.c)).mappings().one()
        record_event(c, entity_type="provider", entity_id=provider_id, event_type="provider_configured",
                     actor_user_id=actor_user_id)
        return dict(row)


def metrics(principal) -> dict:
    with engine.connect() as c:
        enabled = c.scalar(select(func.count()).select_from(providers_t)
                           .where(providers_t.c.enabled.is_(True))) or 0
        total = c.scalar(select(func.count()).select_from(providers_t)) or 0
    return {"enabled_providers": enabled, "total_providers": total}
