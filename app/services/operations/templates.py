"""Project templates & operational resources (Phase D.20) — firm-level configuration.

Project templates are reusable deterministic scaffolds (Tax Season, RIA Audit, Office Expansion,
Server Migration, Client360 Release, Marketing Initiative, Hiring, Employee Onboarding, Policy
Rollout, Compliance Initiative) carrying default phases and default tasks. Operational resources
are the staffing/resource catalog (staff/team/contractor/equipment) with department, role, and
per-day capacity. Managing either requires ``operations.templates`` (enforced in-route).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.database.operations_tables import PROJECT_CATEGORIES, RESOURCE_TYPES
from app.db import engine
from app.db import operational_resources as resources
from app.db import project_templates as tmpl

from .common import OperationsError


def _now():
    return datetime.now(UTC)


# --- project templates -------------------------------------------------------

def list_templates(*, active_only: bool = False, category: str | None = None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(tmpl).order_by(tmpl.c.code)
        if active_only:
            stmt = stmt.where(tmpl.c.active.is_(True))
        if category:
            stmt = stmt.where(tmpl.c.category == category)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_template(template_id: int | None = None, *, code: str | None = None) -> dict | None:
    with engine.connect() as c:
        if code is not None:
            row = c.execute(select(tmpl).where(tmpl.c.code == code)).mappings().first()
        else:
            row = c.execute(select(tmpl).where(tmpl.c.id == template_id)).mappings().first()
        return dict(row) if row else None


def create_template(*, code: str, name: str, category: str = "general", description=None,
                    default_phases=None, default_tasks=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise OperationsError("code and name are required")
    if category not in PROJECT_CATEGORIES:
        raise OperationsError(f"invalid category {category!r}")
    with engine.begin() as c:
        if c.scalar(select(tmpl.c.id).where(tmpl.c.code == code)) is not None:
            raise OperationsError(f"template code {code!r} already exists")
        row = c.execute(tmpl.insert().values(
            code=code, name=name.strip(), category=category, description=description,
            default_phases=default_phases, default_tasks=default_tasks, active=True,
            created_by_user_id=actor_user_id).returning(*tmpl.c)).mappings().one()
        return dict(row)


def update_template(template_id: int, *, name=None, category=None, description=None,
                    default_phases=None, default_tasks=None, active=None) -> dict:
    values: dict = {"updated_at": _now()}
    if name is not None:
        values["name"] = name.strip()
    if description is not None:
        values["description"] = description
    if default_phases is not None:
        values["default_phases"] = default_phases
    if default_tasks is not None:
        values["default_tasks"] = default_tasks
    if active is not None:
        values["active"] = bool(active)
    if category is not None:
        if category not in PROJECT_CATEGORIES:
            raise OperationsError(f"invalid category {category!r}")
        values["category"] = category
    with engine.begin() as c:
        if c.scalar(select(tmpl.c.id).where(tmpl.c.id == template_id)) is None:
            raise OperationsError("template not found")
        row = c.execute(tmpl.update().where(tmpl.c.id == template_id)
                        .values(**values).returning(*tmpl.c)).mappings().one()
        return dict(row)


# --- operational resources ---------------------------------------------------

def list_resources(*, active_only: bool = False, department: str | None = None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(resources).order_by(resources.c.code)
        if active_only:
            stmt = stmt.where(resources.c.active.is_(True))
        if department:
            stmt = stmt.where(resources.c.department == department)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_resource(resource_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(resources).where(resources.c.id == resource_id)).mappings().first()
        return dict(row) if row else None


def create_resource(*, code: str, name: str, resource_type: str = "staff", user_id=None,
                    department=None, role_title=None, capacity_minutes_per_day: int = 480) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise OperationsError("code and name are required")
    if resource_type not in RESOURCE_TYPES:
        raise OperationsError(f"invalid resource_type {resource_type!r}")
    with engine.begin() as c:
        if c.scalar(select(resources.c.id).where(resources.c.code == code)) is not None:
            raise OperationsError(f"resource code {code!r} already exists")
        row = c.execute(resources.insert().values(
            code=code, name=name.strip(), resource_type=resource_type, user_id=user_id,
            department=department, role_title=role_title,
            capacity_minutes_per_day=int(capacity_minutes_per_day), active=True)
            .returning(*resources.c)).mappings().one()
        return dict(row)
