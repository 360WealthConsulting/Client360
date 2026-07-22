"""Configuration hierarchy (Phase D.27) — categories, sets, items, versions, environment overrides.

The configuration hierarchy is governance METADATA: category -> set -> item, with an append version
history and per-environment overrides. Items may *reference* an existing runtime setting
(``runtime_setting_reference`` points at an ``app.config`` function / env var) but never own or change
it. Updating an item's value increments its version and appends a version row (deterministic).
Sensitive item values are withheld from responses unless the caller holds ``configuration.audit``.
Managing requires ``configuration.manage``; approving/archiving requires ``configuration.execute``.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.configuration_tables import (
    ENVIRONMENTS,
    ITEM_STATUSES,
    SET_STATUSES,
    VALUE_TYPES,
)
from app.db import configuration_categories as categories_t
from app.db import configuration_environment_overrides as overrides_t
from app.db import configuration_items as items_t
from app.db import configuration_sets as sets_t
from app.db import configuration_versions as versions_t
from app.db import engine

from .common import (
    ConfigurationError,
    ConfigurationNotFound,
    now,
    publish_timeline,
    record_event,
    write_audit,
)


def _strip_item(row: dict, *, reveal: bool) -> dict:
    if not reveal and row.get("sensitive"):
        row = {**row, "value": None, "default_value": None}
    return row


# --- categories --------------------------------------------------------------

def list_categories():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(categories_t).order_by(categories_t.c.sort_order, categories_t.c.code)).mappings()]


def create_category(principal, *, code, name, description=None, sort_order=0, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(categories_t.c.id).where(categories_t.c.code == code)) is not None:
            raise ConfigurationError(f"category code {code!r} already exists")
        row = c.execute(categories_t.insert().values(
            code=code, name=name.strip(), description=description, sort_order=int(sort_order or 0),
            created_by_user_id=actor_user_id).returning(*categories_t.c)).mappings().one()
        return dict(row)


# --- sets --------------------------------------------------------------------

def list_sets(*, category_id=None, status=None):
    with engine.connect() as c:
        stmt = select(sets_t).order_by(sets_t.c.code)
        if category_id is not None:
            stmt = stmt.where(sets_t.c.category_id == category_id)
        if status:
            stmt = stmt.where(sets_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_set(principal, *, code, name, category_id=None, description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(sets_t.c.id).where(sets_t.c.code == code)) is not None:
            raise ConfigurationError(f"set code {code!r} already exists")
        row = c.execute(sets_t.insert().values(
            code=code, name=name.strip(), category_id=category_id, status="draft",
            description=description, created_by_user_id=actor_user_id).returning(*sets_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="set", entity_id=row["id"], event_type="set_created",
                     actor_user_id=actor_user_id)
        return row


def set_set_status(principal, set_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in SET_STATUSES:
        raise ConfigurationError(f"invalid status {status!r}")
    with engine.begin() as c:
        s = c.execute(select(sets_t).where(sets_t.c.id == set_id)).mappings().first()
        if s is None:
            raise ConfigurationNotFound(str(set_id))
        row = c.execute(sets_t.update().where(sets_t.c.id == set_id).values(
            status=status, updated_at=now()).returning(*sets_t.c)).mappings().one()
        record_event(c, entity_type="set", entity_id=set_id, event_type=f"set_{status}",
                     from_status=s["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"configuration.set_{status}", entity_type="set", entity_id=set_id,
                actor_user_id=actor_user_id)
    if status == "approved":
        publish_timeline(row, "configuration_approved", title=f"Configuration approved: {row['name']}")
    elif status == "archived":
        publish_timeline(row, "configuration_archived", title=f"Configuration archived: {row['name']}")
    return row


# --- items -------------------------------------------------------------------

def list_items(principal, *, set_id=None, status=None):
    reveal = principal.can("configuration.audit")
    with engine.connect() as c:
        stmt = select(items_t).order_by(items_t.c.code)
        if set_id is not None:
            stmt = stmt.where(items_t.c.set_id == set_id)
        if status:
            stmt = stmt.where(items_t.c.status == status)
        return [_strip_item(dict(r), reveal=reveal) for r in c.execute(stmt).mappings()]


def get_item(principal, item_id: int) -> dict | None:
    reveal = principal.can("configuration.audit")
    with engine.connect() as c:
        row = c.execute(select(items_t).where(items_t.c.id == item_id)).mappings().first()
    return _strip_item(dict(row), reveal=reveal) if row else None


def create_item(principal, *, set_id, code, name, value_type="string", value=None, default_value=None,
                sensitive=False, runtime_setting_reference=None, description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    if value_type not in VALUE_TYPES:
        raise ConfigurationError(f"invalid value_type {value_type!r}")
    with engine.begin() as c:
        if c.scalar(select(sets_t.c.id).where(sets_t.c.id == set_id)) is None:
            raise ConfigurationError("set not found")
        if c.scalar(select(items_t.c.id).where(items_t.c.code == code)) is not None:
            raise ConfigurationError(f"item code {code!r} already exists")
        row = c.execute(items_t.insert().values(
            set_id=set_id, code=code, name=name.strip(), value_type=value_type, value=value,
            default_value=default_value, status="draft", version=1, sensitive=bool(sensitive),
            runtime_setting_reference=runtime_setting_reference, description=description,
            created_by_user_id=actor_user_id).returning(*items_t.c)).mappings().one()
        row = dict(row)
        c.execute(versions_t.insert().values(
            configuration_item_id=row["id"], version=1, value=value, note="initial",
            changed_by_user_id=actor_user_id))
        record_event(c, entity_type="item", entity_id=row["id"], event_type="item_created",
                     actor_user_id=actor_user_id)
    write_audit("configuration.item_created", entity_type="item", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"code": code})
    return _strip_item(row, reveal=principal.can("configuration.audit"))


def update_item_value(principal, item_id: int, value, *, note=None, actor_user_id=None) -> dict:
    """Update an item's value, increment its version, and append a version row (deterministic)."""
    with engine.begin() as c:
        item = c.execute(select(items_t).where(items_t.c.id == item_id)).mappings().first()
        if item is None:
            raise ConfigurationNotFound(str(item_id))
        new_version = int(item["version"]) + 1
        row = c.execute(items_t.update().where(items_t.c.id == item_id).values(
            value=value, version=new_version, updated_at=now()).returning(*items_t.c)).mappings().one()
        c.execute(versions_t.insert().values(
            configuration_item_id=item_id, version=new_version, value=value, note=note,
            changed_by_user_id=actor_user_id))
        record_event(c, entity_type="item", entity_id=item_id, event_type="item_updated",
                     actor_user_id=actor_user_id, payload={"version": new_version})
        return _strip_item(dict(row), reveal=principal.can("configuration.audit"))


def set_item_status(principal, item_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in ITEM_STATUSES:
        raise ConfigurationError(f"invalid status {status!r}")
    with engine.begin() as c:
        item = c.execute(select(items_t).where(items_t.c.id == item_id)).mappings().first()
        if item is None:
            raise ConfigurationNotFound(str(item_id))
        row = c.execute(items_t.update().where(items_t.c.id == item_id).values(
            status=status, updated_at=now()).returning(*items_t.c)).mappings().one()
        record_event(c, entity_type="item", entity_id=item_id, event_type=f"item_{status}",
                     from_status=item["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"configuration.item_{status}", entity_type="item", entity_id=item_id,
                actor_user_id=actor_user_id)
    return _strip_item(row, reveal=principal.can("configuration.audit"))


def list_versions(*, configuration_item_id):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(versions_t).where(versions_t.c.configuration_item_id == configuration_item_id)
            .order_by(versions_t.c.version.desc())).mappings()]


# --- environment overrides ---------------------------------------------------

def list_overrides(*, configuration_item_id=None, active_only=False):
    with engine.connect() as c:
        stmt = select(overrides_t).order_by(overrides_t.c.id.desc())
        if configuration_item_id is not None:
            stmt = stmt.where(overrides_t.c.configuration_item_id == configuration_item_id)
        if active_only:
            stmt = stmt.where(overrides_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def set_environment_override(principal, configuration_item_id: int, environment: str, value, *,
                             note=None, actor_user_id=None) -> dict:
    if environment not in ENVIRONMENTS:
        raise ConfigurationError(f"invalid environment {environment!r}")
    with engine.begin() as c:
        if c.scalar(select(items_t.c.id).where(items_t.c.id == configuration_item_id)) is None:
            raise ConfigurationNotFound(str(configuration_item_id))
        existing = c.execute(select(overrides_t).where(
            overrides_t.c.configuration_item_id == configuration_item_id,
            overrides_t.c.environment == environment)).mappings().first()
        if existing is None:
            row = c.execute(overrides_t.insert().values(
                configuration_item_id=configuration_item_id, environment=environment, value=value,
                active=True, note=note, created_by_user_id=actor_user_id)
                .returning(*overrides_t.c)).mappings().one()
        else:
            row = c.execute(overrides_t.update().where(overrides_t.c.id == existing["id"]).values(
                value=value, active=True, note=note, updated_at=now())
                .returning(*overrides_t.c)).mappings().one()
        record_event(c, entity_type="override", entity_id=dict(row)["id"],
                     event_type="override_set", actor_user_id=actor_user_id,
                     payload={"environment": environment})
        return dict(row)


def metrics(principal) -> dict:
    with engine.connect() as c:
        active_overrides = c.scalar(select(func.count()).select_from(overrides_t)
                                    .where(overrides_t.c.active.is_(True))) or 0
        pending_sets = c.scalar(select(func.count()).select_from(sets_t)
                                .where(sets_t.c.status == "draft")) or 0
    return {"active_overrides": active_overrides, "draft_sets": pending_sets}
