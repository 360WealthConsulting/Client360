"""Meeting templates & scheduling resources (Phase D.19) — firm-level configuration.

Meeting templates are reusable, deterministic defaults (meeting type, category, duration, location
type, agenda, preparation checklist) — e.g. prospect meeting, discovery, tax planning, annual
review, insurance/retirement review, business owner planning, compliance review, client
onboarding, internal. Scheduling resources are bookable rooms/equipment/virtual/staff. Managing
either requires ``scheduling.templates`` (enforced in-route); reads are side-effect-free.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.database.scheduling_tables import (
    LOCATION_TYPES,
    MEETING_CATEGORIES,
    MEETING_TYPES,
    RESOURCE_TYPES,
)
from app.db import engine
from app.db import meeting_templates as tmpl
from app.db import scheduling_resources as resources


class TemplateError(Exception):
    """Validation error for a meeting template or scheduling resource."""


def _now():
    return datetime.now(UTC)


# --- meeting templates -------------------------------------------------------

def list_templates(*, active_only: bool = False, meeting_type: str | None = None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(tmpl).order_by(tmpl.c.code)
        if active_only:
            stmt = stmt.where(tmpl.c.active.is_(True))
        if meeting_type:
            stmt = stmt.where(tmpl.c.meeting_type == meeting_type)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_template(template_id: int | None = None, *, code: str | None = None) -> dict | None:
    with engine.connect() as c:
        if code is not None:
            row = c.execute(select(tmpl).where(tmpl.c.code == code)).mappings().first()
        else:
            row = c.execute(select(tmpl).where(tmpl.c.id == template_id)).mappings().first()
        return dict(row) if row else None


def create_template(*, code: str, name: str, meeting_type: str = "general",
                    category: str = "general", default_duration_minutes: int = 60,
                    default_location_type: str = "virtual", agenda=None,
                    preparation_checklist=None, description: str | None = None,
                    actor_user_id: int | None = None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise TemplateError("code and name are required")
    if meeting_type not in MEETING_TYPES:
        raise TemplateError(f"invalid meeting_type {meeting_type!r}")
    if category not in MEETING_CATEGORIES:
        raise TemplateError(f"invalid category {category!r}")
    if default_location_type not in LOCATION_TYPES:
        raise TemplateError(f"invalid location type {default_location_type!r}")
    with engine.begin() as c:
        if c.scalar(select(tmpl.c.id).where(tmpl.c.code == code)) is not None:
            raise TemplateError(f"template code {code!r} already exists")
        row = c.execute(tmpl.insert().values(
            code=code, name=name.strip(), meeting_type=meeting_type, category=category,
            default_duration_minutes=int(default_duration_minutes),
            default_location_type=default_location_type, agenda=agenda,
            preparation_checklist=preparation_checklist, description=description, active=True,
            created_by_user_id=actor_user_id).returning(*tmpl.c)).mappings().one()
        return dict(row)


def update_template(template_id: int, *, name=None, meeting_type=None, category=None,
                    default_duration_minutes=None, default_location_type=None, agenda=None,
                    preparation_checklist=None, description=None, active=None) -> dict:
    values: dict = {"updated_at": _now()}
    if name is not None:
        values["name"] = name.strip()
    if default_duration_minutes is not None:
        values["default_duration_minutes"] = int(default_duration_minutes)
    if agenda is not None:
        values["agenda"] = agenda
    if preparation_checklist is not None:
        values["preparation_checklist"] = preparation_checklist
    if description is not None:
        values["description"] = description
    if active is not None:
        values["active"] = bool(active)
    if meeting_type is not None:
        if meeting_type not in MEETING_TYPES:
            raise TemplateError(f"invalid meeting_type {meeting_type!r}")
        values["meeting_type"] = meeting_type
    if category is not None:
        if category not in MEETING_CATEGORIES:
            raise TemplateError(f"invalid category {category!r}")
        values["category"] = category
    if default_location_type is not None:
        if default_location_type not in LOCATION_TYPES:
            raise TemplateError(f"invalid location type {default_location_type!r}")
        values["default_location_type"] = default_location_type
    with engine.begin() as c:
        if c.scalar(select(tmpl.c.id).where(tmpl.c.id == template_id)) is None:
            raise TemplateError("template not found")
        row = c.execute(tmpl.update().where(tmpl.c.id == template_id)
                        .values(**values).returning(*tmpl.c)).mappings().one()
        return dict(row)


# --- scheduling resources ----------------------------------------------------

def list_resources(*, active_only: bool = False, resource_type: str | None = None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(resources).order_by(resources.c.code)
        if active_only:
            stmt = stmt.where(resources.c.active.is_(True))
        if resource_type:
            stmt = stmt.where(resources.c.resource_type == resource_type)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_resource(*, code: str, name: str, resource_type: str = "room", capacity=None,
                    location: str | None = None, description: str | None = None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise TemplateError("code and name are required")
    if resource_type not in RESOURCE_TYPES:
        raise TemplateError(f"invalid resource_type {resource_type!r}")
    with engine.begin() as c:
        if c.scalar(select(resources.c.id).where(resources.c.code == code)) is not None:
            raise TemplateError(f"resource code {code!r} already exists")
        row = c.execute(resources.insert().values(
            code=code, name=name.strip(), resource_type=resource_type,
            capacity=(int(capacity) if capacity else None), location=location,
            description=description, active=True).returning(*resources.c)).mappings().one()
        return dict(row)
