"""Feature management (Phase D.27) — groups, flags, rollouts. Governance metadata only.

Feature flags are governance METADATA: rollout percentage, target roles/organizations, activation
window, deprecation, and a replacement-feature reference. A flag's ``enabled`` is governance intent —
there is **no runtime feature-toggle engine**; the runtime toggles remain the ``app.config`` env
functions, which a flag may *reference* via ``runtime_setting_reference``. Feature rollouts model a
staged rollout plan. Activating a feature records a guarded lifecycle event. Managing requires
``configuration.manage``; activating/deprecating requires ``configuration.execute``.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.configuration_tables import FEATURE_STATUSES, ROLLOUT_STATUSES
from app.db import configuration_feature_flags as flags_t
from app.db import configuration_feature_groups as groups_t
from app.db import configuration_feature_rollouts as rollouts_t
from app.db import engine

from .common import (
    ConfigurationError,
    ConfigurationNotFound,
    now,
    publish_timeline,
    record_event,
    write_audit,
)

# --- feature groups ----------------------------------------------------------

def list_groups():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(groups_t).order_by(groups_t.c.code)).mappings()]


def create_group(principal, *, code, name, description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(groups_t.c.id).where(groups_t.c.code == code)) is not None:
            raise ConfigurationError(f"feature group code {code!r} already exists")
        row = c.execute(groups_t.insert().values(
            code=code, name=name.strip(), description=description, created_by_user_id=actor_user_id)
            .returning(*groups_t.c)).mappings().one()
        return dict(row)


# --- feature flags -----------------------------------------------------------

def list_flags(*, feature_group_id=None, status=None):
    with engine.connect() as c:
        stmt = select(flags_t).order_by(flags_t.c.code)
        if feature_group_id is not None:
            stmt = stmt.where(flags_t.c.feature_group_id == feature_group_id)
        if status:
            stmt = stmt.where(flags_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_flag(principal, flag_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(flags_t).where(flags_t.c.id == flag_id)).mappings().first()
        return dict(row) if row else None


def create_flag(principal, *, code, name, feature_group_id=None, rollout_percentage=0,
                target_roles=None, target_organizations=None, runtime_setting_reference=None,
                description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    pct = int(rollout_percentage or 0)
    if pct < 0 or pct > 100:
        raise ConfigurationError("rollout_percentage must be between 0 and 100")
    with engine.begin() as c:
        if c.scalar(select(flags_t.c.id).where(flags_t.c.code == code)) is not None:
            raise ConfigurationError(f"feature flag code {code!r} already exists")
        row = c.execute(flags_t.insert().values(
            code=code, name=name.strip(), feature_group_id=feature_group_id, status="draft",
            enabled=False, rollout_percentage=pct, target_roles=target_roles,
            target_organizations=target_organizations, runtime_setting_reference=runtime_setting_reference,
            description=description, created_by_user_id=actor_user_id).returning(*flags_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="feature_flag", entity_id=row["id"], event_type="feature_created",
                     actor_user_id=actor_user_id)
    write_audit("configuration.feature_created", entity_type="feature_flag", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"code": code})
    return row


def set_flag_status(principal, flag_id: int, status: str, *, actor_user_id=None) -> dict:
    """Transition a flag's lifecycle. ``active`` sets ``enabled`` (governance intent) and stamps the
    activation window; a guarded ``feature_activated`` lifecycle event is recorded."""
    if status not in FEATURE_STATUSES:
        raise ConfigurationError(f"invalid status {status!r}")
    with engine.begin() as c:
        flag = c.execute(select(flags_t).where(flags_t.c.id == flag_id)).mappings().first()
        if flag is None:
            raise ConfigurationNotFound(str(flag_id))
        values = {"status": status, "enabled": (status == "active"), "updated_at": now()}
        if status == "active":
            values["activation_starts_at"] = now()
        if status == "deprecated":
            values["deprecation_at"] = now()
        row = c.execute(flags_t.update().where(flags_t.c.id == flag_id).values(**values)
                        .returning(*flags_t.c)).mappings().one()
        record_event(c, entity_type="feature_flag", entity_id=flag_id, event_type=f"feature_{status}",
                     from_status=flag["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"configuration.feature_{status}", entity_type="feature_flag", entity_id=flag_id,
                actor_user_id=actor_user_id)
    if status == "active":
        publish_timeline(row, "feature_activated", title=f"Feature activated: {row['name']}")
    return row


def update_flag_rollout(principal, flag_id: int, rollout_percentage: int, *, actor_user_id=None) -> dict:
    pct = int(rollout_percentage)
    if pct < 0 or pct > 100:
        raise ConfigurationError("rollout_percentage must be between 0 and 100")
    with engine.begin() as c:
        if c.scalar(select(flags_t.c.id).where(flags_t.c.id == flag_id)) is None:
            raise ConfigurationNotFound(str(flag_id))
        row = c.execute(flags_t.update().where(flags_t.c.id == flag_id).values(
            rollout_percentage=pct, updated_at=now()).returning(*flags_t.c)).mappings().one()
        record_event(c, entity_type="feature_flag", entity_id=flag_id, event_type="feature_rollout_updated",
                     actor_user_id=actor_user_id, payload={"rollout_percentage": pct})
        return dict(row)


# --- feature rollouts --------------------------------------------------------

def create_rollout(principal, feature_flag_id: int, *, stage, percentage=0, starts_at=None,
                   ends_at=None, note=None, actor_user_id=None) -> dict:
    stage = (stage or "").strip()
    if not stage:
        raise ConfigurationError("stage is required")
    pct = int(percentage or 0)
    if pct < 0 or pct > 100:
        raise ConfigurationError("percentage must be between 0 and 100")
    with engine.begin() as c:
        if c.scalar(select(flags_t.c.id).where(flags_t.c.id == feature_flag_id)) is None:
            raise ConfigurationError("feature flag not found")
        row = c.execute(rollouts_t.insert().values(
            feature_flag_id=feature_flag_id, stage=stage, percentage=pct, status="planned",
            starts_at=starts_at, ends_at=ends_at, note=note, created_by_user_id=actor_user_id)
            .returning(*rollouts_t.c)).mappings().one()
        return dict(row)


def set_rollout_status(principal, rollout_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in ROLLOUT_STATUSES:
        raise ConfigurationError(f"invalid status {status!r}")
    with engine.begin() as c:
        ro = c.execute(select(rollouts_t).where(rollouts_t.c.id == rollout_id)).mappings().first()
        if ro is None:
            raise ConfigurationNotFound(str(rollout_id))
        row = c.execute(rollouts_t.update().where(rollouts_t.c.id == rollout_id).values(
            status=status, updated_at=now()).returning(*rollouts_t.c)).mappings().one()
        record_event(c, entity_type="feature_rollout", entity_id=rollout_id, event_type=f"rollout_{status}",
                     from_status=ro["status"], to_status=status, actor_user_id=actor_user_id)
        return dict(row)


def list_rollouts(*, feature_flag_id=None, status=None):
    with engine.connect() as c:
        stmt = select(rollouts_t).order_by(rollouts_t.c.id.desc())
        if feature_flag_id is not None:
            stmt = stmt.where(rollouts_t.c.feature_flag_id == feature_flag_id)
        if status:
            stmt = stmt.where(rollouts_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def metrics(principal) -> dict:
    with engine.connect() as c:
        enabled = c.scalar(select(func.count()).select_from(flags_t)
                           .where(flags_t.c.enabled.is_(True))) or 0
        active_rollouts = c.scalar(select(func.count()).select_from(rollouts_t)
                                   .where(rollouts_t.c.status == "active")) or 0
    return {"enabled_feature_flags": enabled, "active_rollouts": active_rollouts}
