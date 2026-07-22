"""Editions, edition capabilities, license policies & assignments (Phase D.27) — metadata only.

Editions are product tiers; edition capabilities **reference the existing RBAC ``capabilities.code``**
(they never duplicate a capability definition and never grant a capability at runtime — RBAC stays
authoritative). License policies bound users/organizations/features. Edition assignments record which
edition (+ license policy) applies tenant-wide or to a referenced organization
(``organization_profiles.id`` — never owned). Assigning an edition records a guarded lifecycle event.
Managing requires ``configuration.manage``; assigning requires ``configuration.execute``.
"""
from __future__ import annotations

from sqlalchemy import func, select, text

from app.database.configuration_tables import (
    ASSIGNMENT_STATUSES,
    EDITION_STATUSES,
    EDITION_TIERS,
    LICENSE_STATUSES,
    PREFERENCE_SCOPES,
)
from app.db import configuration_edition_assignments as assignments_t
from app.db import configuration_edition_capabilities as edition_caps_t
from app.db import configuration_editions as editions_t
from app.db import configuration_license_policies as licenses_t
from app.db import engine

from .common import (
    ConfigurationError,
    ConfigurationNotFound,
    now,
    publish_timeline,
    record_event,
    require_org_scope_write,
    write_audit,
)

# --- editions ----------------------------------------------------------------

def list_editions(*, tier=None, status=None):
    with engine.connect() as c:
        stmt = select(editions_t).order_by(editions_t.c.code)
        if tier:
            stmt = stmt.where(editions_t.c.tier == tier)
        if status:
            stmt = stmt.where(editions_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_edition(principal, edition_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(editions_t).where(editions_t.c.id == edition_id)).mappings().first()
        return dict(row) if row else None


def create_edition(principal, *, code, name, tier="standard", description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    if tier not in EDITION_TIERS:
        raise ConfigurationError(f"invalid tier {tier!r}")
    with engine.begin() as c:
        if c.scalar(select(editions_t.c.id).where(editions_t.c.code == code)) is not None:
            raise ConfigurationError(f"edition code {code!r} already exists")
        row = c.execute(editions_t.insert().values(
            code=code, name=name.strip(), tier=tier, status="draft", description=description,
            created_by_user_id=actor_user_id).returning(*editions_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="edition", entity_id=row["id"], event_type="edition_created",
                     actor_user_id=actor_user_id, payload={"tier": tier})
    write_audit("configuration.edition_created", entity_type="edition", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"tier": tier})
    return row


def set_edition_status(principal, edition_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in EDITION_STATUSES:
        raise ConfigurationError(f"invalid status {status!r}")
    with engine.begin() as c:
        ed = c.execute(select(editions_t).where(editions_t.c.id == edition_id)).mappings().first()
        if ed is None:
            raise ConfigurationNotFound(str(edition_id))
        row = c.execute(editions_t.update().where(editions_t.c.id == edition_id).values(
            status=status, updated_at=now()).returning(*editions_t.c)).mappings().one()
        record_event(c, entity_type="edition", entity_id=edition_id, event_type=f"edition_{status}",
                     from_status=ed["status"], to_status=status, actor_user_id=actor_user_id)
        return dict(row)


# --- edition capabilities (reference RBAC capabilities.code) -----------------

def add_edition_capability(principal, edition_id: int, capability_code: str, *, included=True,
                           actor_user_id=None) -> dict:
    capability_code = (capability_code or "").strip()
    if not capability_code:
        raise ConfigurationError("capability_code is required")
    with engine.begin() as c:
        if c.scalar(select(editions_t.c.id).where(editions_t.c.id == edition_id)) is None:
            raise ConfigurationError("edition not found")
        # Validate the capability exists in the authoritative RBAC catalog (never duplicate it).
        if c.execute(text("SELECT 1 FROM capabilities WHERE code = :c"),
                     {"c": capability_code}).scalar() is None:
            raise ConfigurationError(f"unknown capability {capability_code!r}")
        if c.scalar(select(edition_caps_t.c.id).where(
                edition_caps_t.c.edition_id == edition_id,
                edition_caps_t.c.capability_code == capability_code)) is not None:
            raise ConfigurationError("edition capability already exists")
        row = c.execute(edition_caps_t.insert().values(
            edition_id=edition_id, capability_code=capability_code, included=bool(included),
            created_by_user_id=actor_user_id).returning(*edition_caps_t.c)).mappings().one()
        return dict(row)


def list_edition_capabilities(*, edition_id):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(edition_caps_t).where(edition_caps_t.c.edition_id == edition_id)
            .order_by(edition_caps_t.c.capability_code)).mappings()]


# --- license policies --------------------------------------------------------

def list_license_policies(*, edition_id=None, status=None):
    with engine.connect() as c:
        stmt = select(licenses_t).order_by(licenses_t.c.code)
        if edition_id is not None:
            stmt = stmt.where(licenses_t.c.edition_id == edition_id)
        if status:
            stmt = stmt.where(licenses_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_license_policy(principal, *, code, name, edition_id=None, max_users=None,
                          max_organizations=None, features=None, effective_at=None, expires_at=None,
                          actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ConfigurationError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(licenses_t.c.id).where(licenses_t.c.code == code)) is not None:
            raise ConfigurationError(f"license policy code {code!r} already exists")
        row = c.execute(licenses_t.insert().values(
            code=code, name=name.strip(), edition_id=edition_id, max_users=max_users,
            max_organizations=max_organizations, features=features, status="active",
            effective_at=effective_at, expires_at=expires_at, created_by_user_id=actor_user_id)
            .returning(*licenses_t.c)).mappings().one()
        return dict(row)


def set_license_status(principal, license_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in LICENSE_STATUSES:
        raise ConfigurationError(f"invalid status {status!r}")
    with engine.begin() as c:
        lic = c.execute(select(licenses_t).where(licenses_t.c.id == license_id)).mappings().first()
        if lic is None:
            raise ConfigurationNotFound(str(license_id))
        row = c.execute(licenses_t.update().where(licenses_t.c.id == license_id).values(
            status=status, updated_at=now()).returning(*licenses_t.c)).mappings().one()
        record_event(c, entity_type="license_policy", entity_id=license_id, event_type=f"license_{status}",
                     from_status=lic["status"], to_status=status, actor_user_id=actor_user_id)
        return dict(row)


# --- edition assignments (tenant admin) --------------------------------------

def assign_edition(principal, *, edition_id, scope="tenant", organization_id=None,
                   license_policy_id=None, expires_at=None, actor_user_id=None) -> dict:
    if scope not in PREFERENCE_SCOPES:
        raise ConfigurationError(f"invalid scope {scope!r}")
    if scope == "organization" and organization_id is None:
        raise ConfigurationError("organization scope requires organization_id")
    require_org_scope_write(principal, organization_id)
    with engine.begin() as c:
        if c.scalar(select(editions_t.c.id).where(editions_t.c.id == edition_id)) is None:
            raise ConfigurationError("edition not found")
        row = c.execute(assignments_t.insert().values(
            edition_id=edition_id, license_policy_id=license_policy_id, scope=scope,
            organization_id=organization_id, status="active", assigned_at=now(), expires_at=expires_at,
            created_by_user_id=actor_user_id).returning(*assignments_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="edition_assignment", entity_id=row["id"],
                     event_type="edition_assigned", actor_user_id=actor_user_id,
                     payload={"scope": scope, "edition_id": edition_id})
    write_audit("configuration.edition_assigned", entity_type="edition_assignment", entity_id=row["id"],
                actor_user_id=actor_user_id)
    publish_timeline(row, "edition_assigned", title="Edition assigned")
    return row


def set_assignment_status(principal, assignment_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in ASSIGNMENT_STATUSES:
        raise ConfigurationError(f"invalid status {status!r}")
    with engine.begin() as c:
        asg = c.execute(select(assignments_t).where(assignments_t.c.id == assignment_id)).mappings().first()
        if asg is None:
            raise ConfigurationNotFound(str(assignment_id))
        require_org_scope_write(principal, asg["organization_id"])
        row = c.execute(assignments_t.update().where(assignments_t.c.id == assignment_id).values(
            status=status, updated_at=now()).returning(*assignments_t.c)).mappings().one()
        record_event(c, entity_type="edition_assignment", entity_id=assignment_id,
                     event_type=f"assignment_{status}", from_status=asg["status"], to_status=status,
                     actor_user_id=actor_user_id)
        return dict(row)


def list_assignments(*, edition_id=None, scope=None, status=None):
    with engine.connect() as c:
        stmt = select(assignments_t).order_by(assignments_t.c.id.desc())
        if edition_id is not None:
            stmt = stmt.where(assignments_t.c.edition_id == edition_id)
        if scope:
            stmt = stmt.where(assignments_t.c.scope == scope)
        if status:
            stmt = stmt.where(assignments_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def metrics(principal) -> dict:
    with engine.connect() as c:
        active_editions = c.scalar(select(func.count()).select_from(editions_t)
                                   .where(editions_t.c.status == "active")) or 0
        active_assignments = c.scalar(select(func.count()).select_from(assignments_t)
                                      .where(assignments_t.c.status == "active")) or 0
    return {"active_editions": active_editions, "active_edition_assignments": active_assignments}
