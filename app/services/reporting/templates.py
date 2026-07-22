"""Report templates, export profiles, scorecards, KPI groups & saved views (Phase D.21).

Firm-level reporting configuration. Templates and export profiles require ``reporting.templates``;
scorecards and KPI groups are saved bundles of Analytics metric keys (composed on read, never
recalculated). Saved views are owner-scoped named filter sets over a dashboard/definition. Export
profiles are **metadata only** — value production is delegated to the Analytics ``export_metrics``
producer; no binary generation is implemented here.
"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.database.reporting_tables import (
    EXPORT_DELIVERY,
    EXPORT_FORMATS,
    REPORT_CATEGORIES,
    REPORT_TYPES,
    SAVED_VIEW_TARGETS,
)
from app.db import engine
from app.db import report_templates as tmpl_t
from app.db import reporting_export_profiles as export_t
from app.db import reporting_kpi_groups as kpi_t
from app.db import reporting_saved_views as views_t
from app.db import reporting_scorecards as scorecard_t

from .common import ReportingError

# --- report templates --------------------------------------------------------

def list_templates(*, active_only: bool = False, category: str | None = None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(tmpl_t).order_by(tmpl_t.c.code)
        if active_only:
            stmt = stmt.where(tmpl_t.c.active.is_(True))
        if category:
            stmt = stmt.where(tmpl_t.c.category == category)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_template(template_id: int | None = None, *, code: str | None = None) -> dict | None:
    with engine.connect() as c:
        if code is not None:
            row = c.execute(select(tmpl_t).where(tmpl_t.c.code == code)).mappings().first()
        else:
            row = c.execute(select(tmpl_t).where(tmpl_t.c.id == template_id)).mappings().first()
        return dict(row) if row else None


def create_template(*, code, name, category="general", report_type="dashboard", description=None,
                    definition=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ReportingError("code and name are required")
    if category not in REPORT_CATEGORIES:
        raise ReportingError(f"invalid category {category!r}")
    if report_type not in REPORT_TYPES:
        raise ReportingError(f"invalid report_type {report_type!r}")
    with engine.begin() as c:
        if c.scalar(select(tmpl_t.c.id).where(tmpl_t.c.code == code)) is not None:
            raise ReportingError(f"template code {code!r} already exists")
        row = c.execute(tmpl_t.insert().values(
            code=code, name=name.strip(), category=category, report_type=report_type,
            description=description, definition=definition, active=True,
            created_by_user_id=actor_user_id).returning(*tmpl_t.c)).mappings().one()
        return dict(row)


# --- export profiles ---------------------------------------------------------

def list_export_profiles(*, active_only: bool = False) -> list[dict]:
    with engine.connect() as c:
        stmt = select(export_t).order_by(export_t.c.code)
        if active_only:
            stmt = stmt.where(export_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_export_profile(*, code, name, export_format="pdf", delivery="download", config=None,
                          actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ReportingError("code and name are required")
    if export_format not in EXPORT_FORMATS:
        raise ReportingError(f"invalid export_format {export_format!r}")
    if delivery not in EXPORT_DELIVERY:
        raise ReportingError(f"invalid delivery {delivery!r}")
    with engine.begin() as c:
        if c.scalar(select(export_t.c.id).where(export_t.c.code == code)) is not None:
            raise ReportingError(f"export profile code {code!r} already exists")
        row = c.execute(export_t.insert().values(
            code=code, name=name.strip(), export_format=export_format, delivery=delivery,
            config=config, active=True, created_by_user_id=actor_user_id)
            .returning(*export_t.c)).mappings().one()
        return dict(row)


# --- scorecards --------------------------------------------------------------

def list_scorecards(*, active_only: bool = False) -> list[dict]:
    with engine.connect() as c:
        stmt = select(scorecard_t).order_by(scorecard_t.c.code)
        if active_only:
            stmt = stmt.where(scorecard_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_scorecard(*, code: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(scorecard_t).where(scorecard_t.c.code == code)).mappings().first()
        return dict(row) if row else None


def create_scorecard(*, code, name, metric_keys, category="general", description=None,
                     executive_only=False, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ReportingError("code and name are required")
    if category not in REPORT_CATEGORIES:
        raise ReportingError(f"invalid category {category!r}")
    if not metric_keys:
        raise ReportingError("a scorecard needs at least one metric key")
    with engine.begin() as c:
        if c.scalar(select(scorecard_t.c.id).where(scorecard_t.c.code == code)) is not None:
            raise ReportingError(f"scorecard code {code!r} already exists")
        row = c.execute(scorecard_t.insert().values(
            code=code, name=name.strip(), category=category, description=description,
            executive_only=bool(executive_only), metric_keys=list(metric_keys), active=True,
            created_by_user_id=actor_user_id).returning(*scorecard_t.c)).mappings().one()
        return dict(row)


# --- KPI groups --------------------------------------------------------------

def list_kpi_groups() -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(kpi_t).order_by(kpi_t.c.sort_order, kpi_t.c.code)).mappings()]


def create_kpi_group(*, code, name, metric_keys, description=None, sort_order=0,
                     actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ReportingError("code and name are required")
    if not metric_keys:
        raise ReportingError("a KPI group needs at least one metric key")
    with engine.begin() as c:
        if c.scalar(select(kpi_t.c.id).where(kpi_t.c.code == code)) is not None:
            raise ReportingError(f"KPI group code {code!r} already exists")
        row = c.execute(kpi_t.insert().values(
            code=code, name=name.strip(), description=description, metric_keys=list(metric_keys),
            sort_order=int(sort_order), created_by_user_id=actor_user_id)
            .returning(*kpi_t.c)).mappings().one()
        return dict(row)


# --- saved views (owner-scoped) ----------------------------------------------

def list_saved_views(principal) -> list[dict]:
    with engine.connect() as c:
        stmt = select(views_t).where(or_(views_t.c.owner_user_id == principal.user_id,
                                         views_t.c.shared.is_(True)))
        return [dict(r) for r in c.execute(stmt.order_by(views_t.c.id.desc())).mappings()]


def create_saved_view(principal, *, name, target_type, target_id, filters=None, shared=False,
                      actor_user_id=None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ReportingError("name is required")
    if target_type not in SAVED_VIEW_TARGETS:
        raise ReportingError(f"invalid target_type {target_type!r}")
    with engine.begin() as c:
        row = c.execute(views_t.insert().values(
            name=name, target_type=target_type, target_id=int(target_id),
            owner_user_id=(actor_user_id or principal.user_id), filters=filters,
            shared=bool(shared)).returning(*views_t.c)).mappings().one()
        return dict(row)


def delete_saved_view(principal, view_id: int) -> bool:
    with engine.begin() as c:
        row = c.execute(select(views_t).where(views_t.c.id == view_id)).mappings().first()
        if row is None:
            return False
        if row["owner_user_id"] != principal.user_id and not principal.can("record.write_all"):
            raise ReportingError("saved view belongs to another user")
        c.execute(views_t.delete().where(views_t.c.id == view_id))
        return True
