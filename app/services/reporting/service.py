"""Reporting service (Phase D.21) — the composition-layer facade.

Owns reporting metadata (dashboards, widgets, report definitions, report runs) and composes KPI
values from the Analytics read-model via ``render``. It is a composition layer: it never owns
business data, never recalculates KPIs, and is never a source of truth. Firm-level config is gated
by the ``reporting.*`` capability; a report run may carry an optional client anchor and enforces
record scope. Point-in-time capture reuses ``analytics_snapshots`` through the analytics service.
Approved lifecycle events publish to the shared timeline only for client-anchored report runs;
firm-level events record to the append-only ``reporting_events`` ledger.
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select

from app.database.reporting_tables import (
    DASHBOARD_STATUSES,
    REPORT_CATEGORIES,
    REPORT_TYPES,
    VIZ_TYPES,
    WIDGET_TYPES,
)
from app.db import engine, people
from app.db import report_definitions as definitions_t
from app.db import reporting_dashboards as dashboards_t
from app.db import reporting_widgets as widgets_t
from app.db import reports as reports_t
from app.security.authorization import accessible_person_ids

from . import render
from .common import (
    ReportingError,
    ReportingNotFound,
    audit_history,
    now,
    publish_timeline,
    record_event,
    report_visible,
    require_anchor_write,
)

# --- dashboards --------------------------------------------------------------

def list_dashboards(principal, *, category=None, status=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(dashboards_t).where(dashboards_t.c.active.is_(True))
        if category:
            stmt = stmt.where(dashboards_t.c.category == category)
        if status:
            stmt = stmt.where(dashboards_t.c.status == status)
        return [dict(r) for r in c.execute(stmt.order_by(dashboards_t.c.code)).mappings()]


def _load_dashboard(c, dashboard_id: int | None = None, *, code: str | None = None) -> dict:
    if code is not None:
        row = c.execute(select(dashboards_t).where(dashboards_t.c.code == code)).mappings().first()
    else:
        row = c.execute(select(dashboards_t).where(dashboards_t.c.id == dashboard_id)).mappings().first()
    if row is None:
        raise ReportingNotFound(str(code or dashboard_id))
    return dict(row)


def get_dashboard(principal, dashboard_id: int) -> dict | None:
    with engine.connect() as c:
        try:
            d = _load_dashboard(c, dashboard_id)
        except ReportingNotFound:
            return None
        d["widgets"] = [dict(w) for w in c.execute(
            select(widgets_t).where(widgets_t.c.dashboard_id == dashboard_id)
            .order_by(widgets_t.c.sort_order, widgets_t.c.id)).mappings()]
    return d


def render_dashboard(principal, dashboard_id: int | None = None, *, code: str | None = None) -> dict | None:
    with engine.connect() as c:
        try:
            d = _load_dashboard(c, dashboard_id, code=code)
        except ReportingNotFound:
            return None
        widgets = [dict(w) for w in c.execute(
            select(widgets_t).where(widgets_t.c.dashboard_id == d["id"])
            .order_by(widgets_t.c.sort_order, widgets_t.c.id)).mappings()]
    return render.render_dashboard(principal, d, widgets)


def create_dashboard(principal, *, code, name, category="general", description=None,
                     executive_only=False, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ReportingError("code and name are required")
    if category not in REPORT_CATEGORIES:
        raise ReportingError(f"invalid category {category!r}")
    with engine.begin() as c:
        if c.scalar(select(dashboards_t.c.id).where(dashboards_t.c.code == code)) is not None:
            raise ReportingError(f"dashboard code {code!r} already exists")
        d = c.execute(dashboards_t.insert().values(
            code=code, name=name.strip(), category=category, description=description,
            status="draft", executive_only=bool(executive_only), owner_user_id=actor_user_id,
            created_by_user_id=actor_user_id).returning(*dashboards_t.c)).mappings().one()
        d = dict(d)
        record_event(c, entity_type="dashboard", entity_id=d["id"], event_type="dashboard_created",
                     actor_user_id=actor_user_id, payload={"category": category})
        return d


def update_dashboard(principal, dashboard_id: int, *, actor_user_id=None, **fields) -> dict:
    allowed = {"name", "category", "description", "layout", "tags", "dashboard_metadata",
               "executive_only"}
    values = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "category" in values and values["category"] not in REPORT_CATEGORIES:
        raise ReportingError("invalid category")
    if not values:
        raise ReportingError("no updatable fields provided")
    with engine.begin() as c:
        _load_dashboard(c, dashboard_id)
        values["updated_at"] = now()
        d = c.execute(dashboards_t.update().where(dashboards_t.c.id == dashboard_id)
                      .values(**values).returning(*dashboards_t.c)).mappings().one()
        record_event(c, entity_type="dashboard", entity_id=dashboard_id,
                     event_type="dashboard_updated", actor_user_id=actor_user_id,
                     payload={"fields": sorted(values.keys())})
        return dict(d)


def set_dashboard_status(principal, dashboard_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in DASHBOARD_STATUSES:
        raise ReportingError(f"invalid status {status!r}")
    with engine.begin() as c:
        _load_dashboard(c, dashboard_id)
        values = {"status": status, "updated_at": now()}
        if status == "published":
            values["published_at"] = now()
        d = c.execute(dashboards_t.update().where(dashboards_t.c.id == dashboard_id)
                      .values(**values).returning(*dashboards_t.c)).mappings().one()
        record_event(c, entity_type="dashboard", entity_id=dashboard_id,
                     event_type=f"dashboard_{status}", actor_user_id=actor_user_id)
        return dict(d)


def add_widget(principal, dashboard_id: int, *, title, widget_type="metric", metric_key=None,
               kpi_group_id=None, viz_type="card", dimension_key=None, sort_order=0,
               config=None, actor_user_id=None) -> dict:
    title = (title or "").strip()
    if not title:
        raise ReportingError("widget title is required")
    if widget_type not in WIDGET_TYPES:
        raise ReportingError(f"invalid widget_type {widget_type!r}")
    if viz_type not in VIZ_TYPES:
        raise ReportingError(f"invalid viz_type {viz_type!r}")
    with engine.begin() as c:
        _load_dashboard(c, dashboard_id)
        row = c.execute(widgets_t.insert().values(
            dashboard_id=dashboard_id, title=title, widget_type=widget_type, metric_key=metric_key,
            kpi_group_id=kpi_group_id, viz_type=viz_type, dimension_key=dimension_key,
            sort_order=int(sort_order), config=config).returning(*widgets_t.c)).mappings().one()
        record_event(c, entity_type="dashboard", entity_id=dashboard_id, event_type="widget_added",
                     actor_user_id=actor_user_id, payload={"widget_type": widget_type})
        return dict(row)


# --- report definitions ------------------------------------------------------

def list_definitions(principal, *, report_type=None, category=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(definitions_t).where(definitions_t.c.active.is_(True))
        if report_type:
            stmt = stmt.where(definitions_t.c.report_type == report_type)
        if category:
            stmt = stmt.where(definitions_t.c.category == category)
        rows = [dict(r) for r in c.execute(stmt.order_by(definitions_t.c.id.desc())).mappings()]
    # (D.30) Optional report modules are gated through the runtime engine — behavior-preserving: a
    # definition is included unless a runtime feature ``reporting.module.<code>`` is defined AND
    # disabled (edition/rollout restrictions on optional reports). With no runtime feature defined,
    # the legacy default (included) is used, so the report list is unchanged.
    from app.services.runtime import consumption
    ctx = consumption.runtime_context()
    return [r for r in rows
            if consumption.feature_enabled(f"reporting.module.{r['id']}", context=ctx, default=True, shim=True)]


def get_definition(principal, definition_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(definitions_t)
                        .where(definitions_t.c.id == definition_id)).mappings().first()
        return dict(row) if row else None


def create_definition(principal, *, name, report_type="dashboard", category="general",
                      template_id=None, kpi_group_id=None, definition=None, executive_only=False,
                      actor_user_id=None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ReportingError("name is required")
    if report_type not in REPORT_TYPES:
        raise ReportingError(f"invalid report_type {report_type!r}")
    if category not in REPORT_CATEGORIES:
        raise ReportingError(f"invalid category {category!r}")
    with engine.begin() as c:
        d = c.execute(definitions_t.insert().values(
            name=name, report_type=report_type, category=category, template_id=template_id,
            kpi_group_id=kpi_group_id, definition=definition, executive_only=bool(executive_only),
            owner_user_id=actor_user_id, created_by_user_id=actor_user_id)
            .returning(*definitions_t.c)).mappings().one()
        d = dict(d)
        record_event(c, entity_type="definition", entity_id=d["id"],
                     event_type="definition_created", actor_user_id=actor_user_id,
                     payload={"report_type": report_type})
        return d


def compose_definition(principal, definition_id: int) -> dict | None:
    d = get_definition(principal, definition_id)
    if d is None:
        return None
    return render.compose_definition(principal, d)


# --- reports (runs) ----------------------------------------------------------

def _reports_scope_clause(principal, c):
    if principal.can("record.read_all"):
        return None
    conds = [and_(reports_t.c.person_id.is_(None), reports_t.c.household_id.is_(None))]
    ids = accessible_person_ids(c, principal)
    if ids:
        conds.append(reports_t.c.person_id.in_(tuple(ids)))
        hh = set(c.scalars(select(people.c.household_id).where(
            people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))
        if hh:
            conds.append(reports_t.c.household_id.in_(tuple(hh)))
    return or_(*conds)


def list_reports(principal, *, status=None, report_type=None, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = _reports_scope_clause(principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if status:
            conds.append(reports_t.c.status == status)
        if report_type:
            conds.append(reports_t.c.report_type == report_type)
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(reports_t)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(reports_t)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(reports_t.c.id.desc()).limit(page_size)
            .offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def get_report(principal, report_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(reports_t).where(reports_t.c.id == report_id)).mappings().first()
    if row is None or not report_visible(principal, dict(row)):
        return None
    return dict(row)


def create_report(principal, *, name, report_definition_id=None, dashboard_id=None,
                  report_type="dashboard", category="general", period_key=None, person_id=None,
                  household_id=None, export_profile_id=None, actor_user_id=None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ReportingError("name is required")
    if report_type not in REPORT_TYPES:
        raise ReportingError(f"invalid report_type {report_type!r}")
    if category not in REPORT_CATEGORIES:
        raise ReportingError(f"invalid category {category!r}")
    require_anchor_write(principal, person_id=person_id, household_id=household_id)
    with engine.begin() as c:
        r = c.execute(reports_t.insert().values(
            name=name, report_definition_id=report_definition_id, dashboard_id=dashboard_id,
            report_type=report_type, category=category, period_key=period_key, status="draft",
            person_id=person_id, household_id=household_id, export_profile_id=export_profile_id,
            created_by_user_id=actor_user_id).returning(*reports_t.c)).mappings().one()
        r = dict(r)
        record_event(c, entity_type="report", entity_id=r["id"], event_type="report_created",
                     actor_user_id=actor_user_id, payload={"report_type": report_type})
    publish_timeline(r, "report_created")
    return r


def generate_report(principal, report_id: int, *, capture_snapshots=False, actor_user_id=None) -> dict:
    """Move a report to 'generated'. Composes value summary from Analytics (never recalculating)
    and optionally captures point-in-time values into ``analytics_snapshots`` (the analytics-owned
    snapshot mechanism — Reporting persists no KPI truth of its own)."""
    with engine.begin() as c:
        r = c.execute(select(reports_t).where(reports_t.c.id == report_id)).mappings().first()
        if r is None or not report_visible(principal, dict(r)):
            raise ReportingNotFound(str(report_id))
        r = dict(r)
        ts = now()
        # Compose a value summary from Analytics (metadata only — not KPI truth).
        metric_keys = []
        if r["report_definition_id"]:
            defn = c.execute(select(definitions_t)
                             .where(definitions_t.c.id == r["report_definition_id"])).mappings().first()
            if defn is not None:
                metric_keys = ((defn["definition"] or {}).get("metric_keys")) or []
        result_meta = {"metric_count": len(metric_keys), "generated_at": ts.isoformat()}
        values = {"status": "generated", "generated_at": ts, "generated_by_user_id": actor_user_id,
                  "result_metadata": result_meta, "snapshot_captured": bool(capture_snapshots),
                  "updated_at": ts}
        updated = c.execute(reports_t.update().where(reports_t.c.id == report_id)
                            .values(**values).returning(*reports_t.c)).mappings().one()
        record_event(c, entity_type="report", entity_id=report_id, event_type="report_generated",
                     actor_user_id=actor_user_id, payload={"metric_count": len(metric_keys)})
        updated = dict(updated)
    # Reuse the analytics snapshot mechanism (outside the report txn; idempotent per period).
    if capture_snapshots and metric_keys:
        try:
            from app.services.analytics import service as analytics_service
            for key in metric_keys:
                analytics_service.capture_snapshot(principal, metric_key=key,
                                                   actor_user_id=actor_user_id,
                                                   period_key=updated.get("period_key"))
        except Exception:
            pass
    publish_timeline(updated, "report_generated")
    return updated


def report_audit(principal, report_id: int) -> list[dict]:
    if get_report(principal, report_id) is None:
        raise ReportingNotFound(str(report_id))
    return audit_history(principal, entity_type="report", entity_id=report_id)


# --- overview metrics --------------------------------------------------------

def overview_metrics(principal) -> dict:
    with engine.connect() as c:
        dash = c.scalar(select(func.count()).select_from(dashboards_t)
                        .where(dashboards_t.c.status == "published")) or 0
        defs = c.scalar(select(func.count()).select_from(definitions_t)
                        .where(definitions_t.c.active.is_(True))) or 0
        scope = _reports_scope_clause(principal, c)
        rbase = select(func.count()).select_from(reports_t)
        reports_total = c.scalar(rbase.where(scope) if scope is not None else rbase) or 0
    return {"published_dashboards": dash, "report_definitions": defs, "reports": reports_total}
