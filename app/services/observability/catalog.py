"""Service inventory, dependency graph, environment & deployment references (Phase D.26).

The service catalog is observability METADATA: a service is an entry in the inventory that may
*reference* an existing domain object (e.g. an integration connector) but never owns it. Dependencies
form a directed graph between services. Environment profiles and deployment references are lightweight
registries (deployment/version metadata is greenfield — nothing else in the platform records it).
Managing the catalog requires ``observability.manage``; setting a service's status requires
``observability.execute`` (enforced in-route).
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.observability_tables import (
    CRITICALITIES,
    DEPENDENCY_TYPES,
    ENVIRONMENTS,
    SERVICE_STATUSES,
    SERVICE_TYPES,
)
from app.db import engine
from app.db import observability_deployment_references as deploys_t
from app.db import observability_environment_profiles as envs_t
from app.db import observability_service_dependencies as deps_t
from app.db import observability_services as services_t

from .common import (
    ObservabilityError,
    ObservabilityNotFound,
    now,
    publish_timeline,
    record_event,
    write_audit,
)

# --- services ----------------------------------------------------------------

def list_services(*, service_type=None, status=None):
    with engine.connect() as c:
        stmt = select(services_t).order_by(services_t.c.code)
        if service_type:
            stmt = stmt.where(services_t.c.service_type == service_type)
        if status:
            stmt = stmt.where(services_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_service(principal, service_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(services_t).where(services_t.c.id == service_id)).mappings().first()
        return dict(row) if row else None


def create_service(principal, *, code, name, service_type="application", criticality="medium",
                   reference_type=None, reference_id=None, description=None, owner_user_id=None,
                   actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ObservabilityError("code and name are required")
    if service_type not in SERVICE_TYPES:
        raise ObservabilityError(f"invalid service_type {service_type!r}")
    if criticality not in CRITICALITIES:
        raise ObservabilityError(f"invalid criticality {criticality!r}")
    with engine.begin() as c:
        if c.scalar(select(services_t.c.id).where(services_t.c.code == code)) is not None:
            raise ObservabilityError(f"service code {code!r} already exists")
        row = c.execute(services_t.insert().values(
            code=code, name=name.strip(), service_type=service_type, status="unknown",
            criticality=criticality, reference_type=reference_type, reference_id=reference_id,
            description=description, owner_user_id=owner_user_id, created_by_user_id=actor_user_id)
            .returning(*services_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="service", entity_id=row["id"], event_type="service_created",
                     actor_user_id=actor_user_id, payload={"service_type": service_type})
    write_audit("observability.service_created", entity_type="service", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"service_type": service_type})
    return row


def set_service_status(principal, service_id: int, status: str, *, detail=None, actor_user_id=None) -> dict:
    """Record a service's operational status. A transition into ``degraded``/``down`` and back to
    ``operational`` records a guarded timeline event (firm-level services skip the timeline)."""
    if status not in SERVICE_STATUSES:
        raise ObservabilityError(f"invalid status {status!r}")
    with engine.begin() as c:
        svc = c.execute(select(services_t).where(services_t.c.id == service_id)).mappings().first()
        if svc is None:
            raise ObservabilityNotFound(str(service_id))
        row = c.execute(services_t.update().where(services_t.c.id == service_id).values(
            status=status, last_status_at=now(), updated_at=now()).returning(*services_t.c)).mappings().one()
        record_event(c, entity_type="service", entity_id=service_id, event_type=f"service_{status}",
                     from_status=svc["status"], to_status=status, actor_user_id=actor_user_id,
                     payload={"detail": detail} if detail else None)
        row = dict(row)
    write_audit(f"observability.service_{status}", entity_type="service", entity_id=service_id,
                actor_user_id=actor_user_id)
    # Services are firm-level (no client anchor) -> timeline publication is skipped by the guard.
    if status in ("degraded", "down"):
        publish_timeline(row, "service_degraded", title=f"Service degraded: {row['name']}")
    elif status == "operational":
        publish_timeline(row, "service_restored", title=f"Service restored: {row['name']}")
    return row


# --- dependencies ------------------------------------------------------------

def add_dependency(principal, service_id: int, depends_on_service_id: int, *, dependency_type="hard",
                   description=None, actor_user_id=None) -> dict:
    if dependency_type not in DEPENDENCY_TYPES:
        raise ObservabilityError(f"invalid dependency_type {dependency_type!r}")
    if service_id == depends_on_service_id:
        raise ObservabilityError("a service cannot depend on itself")
    with engine.begin() as c:
        for sid in (service_id, depends_on_service_id):
            if c.scalar(select(services_t.c.id).where(services_t.c.id == sid)) is None:
                raise ObservabilityError(f"service {sid} not found")
        if c.scalar(select(deps_t.c.id).where(deps_t.c.service_id == service_id,
                                              deps_t.c.depends_on_service_id == depends_on_service_id)) is not None:
            raise ObservabilityError("dependency already exists")
        row = c.execute(deps_t.insert().values(
            service_id=service_id, depends_on_service_id=depends_on_service_id,
            dependency_type=dependency_type, description=description, created_by_user_id=actor_user_id)
            .returning(*deps_t.c)).mappings().one()
        return dict(row)


def list_dependencies(*, service_id=None):
    with engine.connect() as c:
        stmt = select(deps_t).order_by(deps_t.c.id)
        if service_id is not None:
            stmt = stmt.where(deps_t.c.service_id == service_id)
        return [dict(r) for r in c.execute(stmt).mappings()]


# --- environment profiles / deployment references ----------------------------

def list_environment_profiles(*, active_only=False):
    with engine.connect() as c:
        stmt = select(envs_t).order_by(envs_t.c.code)
        if active_only:
            stmt = stmt.where(envs_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_environment_profile(principal, *, code, name, environment="production", region=None,
                               description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ObservabilityError("code and name are required")
    if environment not in ENVIRONMENTS:
        raise ObservabilityError(f"invalid environment {environment!r}")
    with engine.begin() as c:
        if c.scalar(select(envs_t.c.id).where(envs_t.c.code == code)) is not None:
            raise ObservabilityError(f"environment profile code {code!r} already exists")
        row = c.execute(envs_t.insert().values(
            code=code, name=name.strip(), environment=environment, region=region,
            description=description, created_by_user_id=actor_user_id).returning(*envs_t.c)).mappings().one()
        return dict(row)


def list_deployment_references(*, environment_profile_id=None):
    with engine.connect() as c:
        stmt = select(deploys_t).order_by(deploys_t.c.id.desc())
        if environment_profile_id is not None:
            stmt = stmt.where(deploys_t.c.environment_profile_id == environment_profile_id)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_deployment_reference(principal, *, code, version, migration_head=None,
                                environment_profile_id=None, released_at=None, notes=None,
                                actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (version or "").strip():
        raise ObservabilityError("code and version are required")
    with engine.begin() as c:
        if c.scalar(select(deploys_t.c.id).where(deploys_t.c.code == code)) is not None:
            raise ObservabilityError(f"deployment reference code {code!r} already exists")
        row = c.execute(deploys_t.insert().values(
            code=code, version=version.strip(), migration_head=migration_head,
            environment_profile_id=environment_profile_id, released_at=released_at, notes=notes,
            created_by_user_id=actor_user_id).returning(*deploys_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="deployment", entity_id=row["id"], event_type="deployment_recorded",
                     actor_user_id=actor_user_id, payload={"version": version})
        return row


def metrics(principal) -> dict:
    with engine.connect() as c:
        operational = c.scalar(select(func.count()).select_from(services_t)
                               .where(services_t.c.status == "operational")) or 0
        total = c.scalar(select(func.count()).select_from(services_t)) or 0
        degraded = c.scalar(select(func.count()).select_from(services_t)
                            .where(services_t.c.status.in_(("degraded", "down")))) or 0
    return {"operational_services": operational, "total_services": total, "degraded_services": degraded}
