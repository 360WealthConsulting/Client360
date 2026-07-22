"""Security policies & configuration baselines (Phase D.25) — metadata only.

A unified ``security_policies`` table models every policy variant via ``policy_type`` (security,
session, password, mfa, access, capability, role, api, encryption, key_rotation, authentication,
federation). Policies are deterministic configuration metadata (never secrets) governing the posture
the existing auth/RBAC/crypto/session infrastructure already enforces — creating a policy never
changes runtime login/OAuth/RBAC. Approving a policy requires ``security.execute`` (enforced
in-route). Security configurations are the platform hardening baseline (key/value + applied flag).
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.security_tables import (
    CONFIG_CATEGORIES,
    POLICY_STATUSES,
    POLICY_TYPES,
)
from app.db import engine
from app.db import security_configurations as configs_t
from app.db import security_policies as policies_t

from .common import (
    SecurityError,
    SecurityNotFound,
    now,
    publish_timeline,
    record_event,
    write_audit,
)

# --- policies ----------------------------------------------------------------

def list_policies(*, policy_type=None, status=None):
    with engine.connect() as c:
        stmt = select(policies_t).order_by(policies_t.c.code)
        if policy_type:
            stmt = stmt.where(policies_t.c.policy_type == policy_type)
        if status:
            stmt = stmt.where(policies_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_policy(principal, policy_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(policies_t).where(policies_t.c.id == policy_id)).mappings().first()
        return dict(row) if row else None


def create_policy(principal, *, code, name, policy_type="security", config=None, description=None,
                  actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise SecurityError("code and name are required")
    if policy_type not in POLICY_TYPES:
        raise SecurityError(f"invalid policy_type {policy_type!r}")
    if config and _contains_secret(config):
        raise SecurityError("policy config must not contain secrets (use a secret reference)")
    with engine.begin() as c:
        if c.scalar(select(policies_t.c.id).where(policies_t.c.code == code)) is not None:
            raise SecurityError(f"policy code {code!r} already exists")
        row = c.execute(policies_t.insert().values(
            code=code, name=name.strip(), policy_type=policy_type, status="draft", version=1,
            config=config, description=description, created_by_user_id=actor_user_id)
            .returning(*policies_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="policy", entity_id=row["id"], event_type="policy_created",
                     actor_user_id=actor_user_id, payload={"policy_type": policy_type})
    write_audit("security.policy_created", entity_type="policy", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"policy_type": policy_type})
    return row


def update_policy(principal, policy_id: int, *, config=None, description=None, name=None,
                  actor_user_id=None) -> dict:
    """Update a draft/active policy and increment its version deterministically. Approved policies
    are immutable configuration (create a new version via a new policy) — reject config edits."""
    if config and _contains_secret(config):
        raise SecurityError("policy config must not contain secrets (use a secret reference)")
    with engine.begin() as c:
        pol = c.execute(select(policies_t).where(policies_t.c.id == policy_id)).mappings().first()
        if pol is None:
            raise SecurityNotFound(str(policy_id))
        if pol["status"] == "approved" and config is not None:
            raise SecurityError("an approved policy is immutable; supersede it with a new policy")
        values = {"updated_at": now()}
        if config is not None:
            values["config"] = config
            values["version"] = int(pol["version"]) + 1
        if description is not None:
            values["description"] = description
        if name is not None:
            values["name"] = name.strip()
        row = c.execute(policies_t.update().where(policies_t.c.id == policy_id).values(**values)
                        .returning(*policies_t.c)).mappings().one()
        record_event(c, entity_type="policy", entity_id=policy_id, event_type="policy_updated",
                     actor_user_id=actor_user_id, payload={"version": dict(row)["version"]})
        return dict(row)


def set_policy_status(principal, policy_id: int, status: str, *, actor_user_id=None) -> dict:
    """Approve/activate/retire a policy. ``approved`` stamps the approver + timestamp and publishes a
    guarded timeline event (firm-level policies skip the timeline)."""
    if status not in POLICY_STATUSES:
        raise SecurityError(f"invalid status {status!r}")
    with engine.begin() as c:
        pol = c.execute(select(policies_t).where(policies_t.c.id == policy_id)).mappings().first()
        if pol is None:
            raise SecurityNotFound(str(policy_id))
        values = {"status": status, "updated_at": now()}
        if status == "approved":
            values["approved_by_user_id"] = actor_user_id
            values["approved_at"] = now()
            values["effective_at"] = now()
        row = c.execute(policies_t.update().where(policies_t.c.id == policy_id).values(**values)
                        .returning(*policies_t.c)).mappings().one()
        record_event(c, entity_type="policy", entity_id=policy_id, event_type=f"policy_{status}",
                     from_status=pol["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"security.policy_{status}", entity_type="policy", entity_id=policy_id,
                actor_user_id=actor_user_id)
    if status == "approved":
        publish_timeline(row, "policy_approved", title=f"Policy approved: {row['name']}")
    return row


def _contains_secret(config) -> bool:
    return any(k in str(config).lower() for k in ("password", "secret", "api_key", "private_key",
                                                  "token"))


# --- configurations (hardening baseline) -------------------------------------

def list_configurations(*, category=None, applied=None):
    with engine.connect() as c:
        stmt = select(configs_t).order_by(configs_t.c.config_key)
        if category:
            stmt = stmt.where(configs_t.c.category == category)
        if applied is not None:
            stmt = stmt.where(configs_t.c.applied.is_(bool(applied)))
        return [dict(r) for r in c.execute(stmt).mappings()]


def upsert_configuration(principal, *, config_key, name, category="hardening", value=None,
                         baseline=None, applied=False, description=None, actor_user_id=None) -> dict:
    config_key = (config_key or "").strip()
    if not config_key or not (name or "").strip():
        raise SecurityError("config_key and name are required")
    if category not in CONFIG_CATEGORIES:
        raise SecurityError(f"invalid category {category!r}")
    with engine.begin() as c:
        existing = c.execute(select(configs_t).where(configs_t.c.config_key == config_key)).mappings().first()
        if existing is None:
            row = c.execute(configs_t.insert().values(
                config_key=config_key, name=name.strip(), category=category, value=value,
                baseline=baseline, applied=bool(applied), description=description,
                created_by_user_id=actor_user_id).returning(*configs_t.c)).mappings().one()
        else:
            row = c.execute(configs_t.update().where(configs_t.c.config_key == config_key).values(
                name=name.strip(), category=category, value=value, applied=bool(applied),
                description=description, updated_at=now()).returning(*configs_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="configuration", entity_id=row["id"],
                     event_type="configuration_set", actor_user_id=actor_user_id,
                     payload={"config_key": config_key, "applied": bool(applied)})
        return row


def metrics(principal) -> dict:
    with engine.connect() as c:
        active_policies = c.scalar(select(func.count()).select_from(policies_t)
                                   .where(policies_t.c.status.in_(("active", "approved")))) or 0
        unapplied = c.scalar(select(func.count()).select_from(configs_t)
                             .where(configs_t.c.applied.is_(False))) or 0
    return {"active_policies": active_policies, "unapplied_configurations": unapplied}
