"""Tenant / organization / user preferences (Phase D.27) — metadata only.

A unified ``configuration_preferences`` table models tenant-wide, per-organization, and per-user
preferences via ``scope``. Organization-scoped writes enforce ``organization_in_scope`` (record
scope); Configuration references ``organization_profiles.id``/``users.id`` and never owns them.
User-preference rows may carry a ``reference`` pointer to where the real preference lives (e.g. the
communications ``notification_preferences``) — Configuration governs, it does not own the underlying
preference. Managing requires ``configuration.manage``.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.configuration_tables import PREFERENCE_SCOPES
from app.db import configuration_preferences as prefs_t
from app.db import engine

from .common import (
    ConfigurationError,
    ConfigurationNotFound,
    now,
    record_event,
    require_org_scope_write,
)


def list_preferences(principal, *, scope=None, organization_id=None, user_id=None):
    with engine.connect() as c:
        stmt = select(prefs_t).order_by(prefs_t.c.id.desc())
        if scope:
            stmt = stmt.where(prefs_t.c.scope == scope)
        if organization_id is not None:
            stmt = stmt.where(prefs_t.c.organization_id == organization_id)
        if user_id is not None:
            stmt = stmt.where(prefs_t.c.user_id == user_id)
        rows = [dict(r) for r in c.execute(stmt).mappings()]
    # Organization-scoped rows are visible only within the principal's org scope.
    if not principal.can("record.read_all"):
        from .common import org_visible
        rows = [r for r in rows if org_visible(principal, r.get("organization_id"))]
    return rows


def set_preference(principal, *, scope="tenant", preference_key, value=None, organization_id=None,
                   user_id=None, reference=None, description=None, actor_user_id=None) -> dict:
    if scope not in PREFERENCE_SCOPES:
        raise ConfigurationError(f"invalid scope {scope!r}")
    preference_key = (preference_key or "").strip()
    if not preference_key:
        raise ConfigurationError("preference_key is required")
    if scope == "organization" and organization_id is None:
        raise ConfigurationError("organization scope requires organization_id")
    if scope == "user" and user_id is None:
        raise ConfigurationError("user scope requires user_id")
    require_org_scope_write(principal, organization_id)
    with engine.begin() as c:
        existing = c.execute(select(prefs_t).where(
            prefs_t.c.scope == scope,
            prefs_t.c.organization_id.is_(organization_id) if organization_id is None
            else prefs_t.c.organization_id == organization_id,
            prefs_t.c.user_id.is_(user_id) if user_id is None else prefs_t.c.user_id == user_id,
            prefs_t.c.preference_key == preference_key)).mappings().first()
        if existing is None:
            row = c.execute(prefs_t.insert().values(
                scope=scope, organization_id=organization_id, user_id=user_id,
                preference_key=preference_key, value=value, reference=reference,
                description=description, created_by_user_id=actor_user_id)
                .returning(*prefs_t.c)).mappings().one()
        else:
            row = c.execute(prefs_t.update().where(prefs_t.c.id == existing["id"]).values(
                value=value, reference=reference, description=description, updated_at=now())
                .returning(*prefs_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="preference", entity_id=row["id"], event_type="preference_set",
                     actor_user_id=actor_user_id, payload={"scope": scope, "key": preference_key})
        return row


def get_preference(principal, preference_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(prefs_t).where(prefs_t.c.id == preference_id)).mappings().first()
    if row is None:
        return None
    row = dict(row)
    from .common import org_visible
    if not org_visible(principal, row.get("organization_id")):
        raise ConfigurationNotFound(str(preference_id))
    return row


def metrics(principal) -> dict:
    with engine.connect() as c:
        total = c.scalar(select(func.count()).select_from(prefs_t)) or 0
    return {"preferences": total}
