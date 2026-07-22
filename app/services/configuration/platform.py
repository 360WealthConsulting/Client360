"""Platform options, administrative policies, runtime-setting references, snapshots & changes.

Platform options are simple key/value governance settings. Administrative policies govern admin
posture (draft→approved). Runtime-setting references *point at* an existing ``app.config`` function /
env var (governance metadata) — they never store a secret value (only a human note) and never
re-read env. Configuration snapshots capture a point-in-time metadata view. Configuration changes are
a proposed→approved→applied change record (a governance workflow over config edits). Managing requires
``configuration.manage``; approving policies/changes requires ``configuration.execute``. (Phase D.27)
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.configuration_tables import (
    ADMIN_POLICY_STATUSES,
    CHANGE_STATUSES,
    CHANGE_TYPES,
    OPTION_TYPES,
)
from app.db import configuration_administrative_policies as policies_t
from app.db import configuration_changes as changes_t
from app.db import configuration_platform_options as options_t
from app.db import configuration_runtime_setting_references as runtime_t
from app.db import configuration_snapshots as snapshots_t
from app.db import engine

from .common import (
    ConfigurationError,
    ConfigurationNotFound,
    as_json,
    now,
    publish_timeline,
    record_event,
    write_audit,
)

# --- platform options --------------------------------------------------------

def list_options(*, category=None):
    with engine.connect() as c:
        stmt = select(options_t).order_by(options_t.c.code)
        if category:
            stmt = stmt.where(options_t.c.category == category)
        return [dict(r) for r in c.execute(stmt).mappings()]


def upsert_option(principal, *, code, name, option_type="boolean", value=None, category=None,
                  editable=True, description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    if option_type not in OPTION_TYPES:
        raise ConfigurationError(f"invalid option_type {option_type!r}")
    with engine.begin() as c:
        existing = c.execute(select(options_t).where(options_t.c.code == code)).mappings().first()
        if existing is None:
            row = c.execute(options_t.insert().values(
                code=code, name=name.strip(), option_type=option_type, value=value, category=category,
                editable=bool(editable), description=description, created_by_user_id=actor_user_id)
                .returning(*options_t.c)).mappings().one()
        else:
            if not existing["editable"]:
                raise ConfigurationError(f"option {code!r} is not editable")
            row = c.execute(options_t.update().where(options_t.c.code == code).values(
                name=name.strip(), option_type=option_type, value=value, category=category,
                description=description, updated_at=now()).returning(*options_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="option", entity_id=row["id"], event_type="option_set",
                     actor_user_id=actor_user_id, payload={"code": code})
        return row


# --- administrative policies --------------------------------------------------

def list_admin_policies(*, status=None):
    with engine.connect() as c:
        stmt = select(policies_t).order_by(policies_t.c.code)
        if status:
            stmt = stmt.where(policies_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_admin_policy(principal, *, code, name, policy_type=None, config=None, description=None,
                        actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(policies_t.c.id).where(policies_t.c.code == code)) is not None:
            raise ConfigurationError(f"administrative policy code {code!r} already exists")
        row = c.execute(policies_t.insert().values(
            code=code, name=name.strip(), policy_type=policy_type, status="draft", config=config,
            description=description, created_by_user_id=actor_user_id).returning(*policies_t.c)).mappings().one()
        return dict(row)


def set_admin_policy_status(principal, policy_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in ADMIN_POLICY_STATUSES:
        raise ConfigurationError(f"invalid status {status!r}")
    with engine.begin() as c:
        pol = c.execute(select(policies_t).where(policies_t.c.id == policy_id)).mappings().first()
        if pol is None:
            raise ConfigurationNotFound(str(policy_id))
        values = {"status": status, "updated_at": now()}
        if status == "approved":
            values["approved_by_user_id"] = actor_user_id
            values["approved_at"] = now()
        row = c.execute(policies_t.update().where(policies_t.c.id == policy_id).values(**values)
                        .returning(*policies_t.c)).mappings().one()
        record_event(c, entity_type="admin_policy", entity_id=policy_id, event_type=f"admin_policy_{status}",
                     from_status=pol["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"configuration.admin_policy_{status}", entity_type="admin_policy", entity_id=policy_id,
                actor_user_id=actor_user_id)
    return row


# --- runtime setting references (point at app.config; never store a secret) --

def list_runtime_references(*, sensitive=None):
    with engine.connect() as c:
        stmt = select(runtime_t).order_by(runtime_t.c.code)
        if sensitive is not None:
            stmt = stmt.where(runtime_t.c.sensitive.is_(bool(sensitive)))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_runtime_reference(principal, *, code, name, env_var=None, loader_reference=None,
                             value_type="string", current_value_note=None, sensitive=False,
                             description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(runtime_t.c.id).where(runtime_t.c.code == code)) is not None:
            raise ConfigurationError(f"runtime setting reference code {code!r} already exists")
        row = c.execute(runtime_t.insert().values(
            code=code, name=name.strip(), env_var=env_var, loader_reference=loader_reference,
            value_type=value_type, current_value_note=current_value_note, sensitive=bool(sensitive),
            description=description, created_by_user_id=actor_user_id).returning(*runtime_t.c)).mappings().one()
        return dict(row)


# --- snapshots ---------------------------------------------------------------

def capture_snapshot(principal, *, code=None, scope="platform", actor_user_id=None) -> dict:
    """Capture a point-in-time snapshot of the configuration metadata state (counts by area)."""
    code = (code or f"snap-{int(now().timestamp())}").strip()
    with engine.connect() as c:
        from app.db import (
            configuration_editions,
            configuration_feature_flags,
            configuration_items,
        )
        counts = {
            "items": c.scalar(select(func.count()).select_from(configuration_items)) or 0,
            "feature_flags": c.scalar(select(func.count()).select_from(configuration_feature_flags)) or 0,
            "editions": c.scalar(select(func.count()).select_from(configuration_editions)) or 0,
        }
    with engine.begin() as c:
        row = c.execute(snapshots_t.insert().values(
            code=code, scope=scope, captured_at=now(), payload=as_json(counts),
            summary=f"items={counts['items']} flags={counts['feature_flags']} editions={counts['editions']}",
            created_by_user_id=actor_user_id).returning(*snapshots_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="snapshot", entity_id=row["id"], event_type="snapshot_captured",
                     actor_user_id=actor_user_id)
    return row


def list_snapshots(*, limit=50):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(snapshots_t).order_by(snapshots_t.c.id.desc()).limit(min(200, max(1, limit)))).mappings()]


# --- configuration changes (proposed -> approved -> applied) -----------------

def propose_change(principal, *, entity_type, entity_id=None, change_type="update", from_value=None,
                   to_value=None, note=None, actor_user_id=None) -> dict:
    if change_type not in CHANGE_TYPES:
        raise ConfigurationError(f"invalid change_type {change_type!r}")
    with engine.begin() as c:
        row = c.execute(changes_t.insert().values(
            entity_type=entity_type, entity_id=entity_id, change_type=change_type, from_value=from_value,
            to_value=to_value, status="proposed", requested_by_user_id=actor_user_id, note=note,
            created_by_user_id=actor_user_id).returning(*changes_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="change", entity_id=row["id"], event_type="change_proposed",
                     to_status="proposed", actor_user_id=actor_user_id)
        return row


def decide_change(principal, change_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in CHANGE_STATUSES:
        raise ConfigurationError(f"invalid status {status!r}")
    with engine.begin() as c:
        ch = c.execute(select(changes_t).where(changes_t.c.id == change_id)).mappings().first()
        if ch is None:
            raise ConfigurationNotFound(str(change_id))
        values = {"status": status, "updated_at": now()}
        if status == "approved":
            values["approved_by_user_id"] = actor_user_id
            values["approved_at"] = now()
        if status == "applied":
            values["applied_at"] = now()
        row = c.execute(changes_t.update().where(changes_t.c.id == change_id).values(**values)
                        .returning(*changes_t.c)).mappings().one()
        record_event(c, entity_type="change", entity_id=change_id, event_type=f"change_{status}",
                     from_status=ch["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"configuration.change_{status}", entity_type="change", entity_id=change_id,
                actor_user_id=actor_user_id)
    if status == "approved":
        publish_timeline(row, "configuration_approved", title="Configuration change approved")
    return row


def list_changes(*, status=None, entity_type=None):
    with engine.connect() as c:
        stmt = select(changes_t).order_by(changes_t.c.id.desc())
        if status:
            stmt = stmt.where(changes_t.c.status == status)
        if entity_type:
            stmt = stmt.where(changes_t.c.entity_type == entity_type)
        return [dict(r) for r in c.execute(stmt).mappings()]


def metrics(principal) -> dict:
    with engine.connect() as c:
        pending_changes = c.scalar(select(func.count()).select_from(changes_t)
                                   .where(changes_t.c.status == "proposed")) or 0
        options = c.scalar(select(func.count()).select_from(options_t)) or 0
    return {"pending_changes": pending_changes, "platform_options": options}
