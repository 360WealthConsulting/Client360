"""Analytics dashboards (Phase D.15) — predefined executive scorecards + custom dashboards.

Predefined dashboards are deterministic metric bundles with visualization metadata; custom
dashboards come from ``analytics_dashboards`` / ``analytics_dashboard_widgets``. Composition
computes each metric (executive gating applied per-metric), attaches its target/variance, and
returns visualization metadata only — no chart libraries, no business data ownership.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import analytics_dashboard_widgets, analytics_dashboards, engine
from app.services.analytics import metrics, targets

# Predefined executive scorecards: code -> (name, executive_only, [(metric_key, viz)]).
PREDEFINED = {
    "firm": ("Firm Dashboard", True,
             [("aum", "card"), ("client_count", "card"), ("household_count", "card"),
              ("pipeline_value", "card"), ("open_work", "card"), ("active_campaigns", "card")]),
    "advisor": ("Advisor Dashboard", False,
                [("pipeline_value", "card"), ("open_opportunities", "card"),
                 ("won_opportunities", "card"), ("open_work", "card"), ("open_tasks", "card"),
                 ("annual_reviews", "card")]),
    "business_development": ("Business Development Dashboard", False,
                             [("campaign_revenue", "card"), ("referral_revenue", "card"),
                              ("active_campaigns", "card"), ("active_referral_sources", "card"),
                              ("pipeline_value", "card"), ("pipeline_conversion", "gauge")]),
    "operations": ("Operations Dashboard", False,
                   [("open_work", "card"), ("open_tasks", "card"), ("annual_reviews", "card"),
                    ("business_plans", "card"), ("timeline_activity", "card"),
                    ("open_compliance_reviews", "card")]),
    "compliance": ("Compliance Dashboard", False,
                   [("open_compliance_reviews", "card")]),
    "tax": ("Tax Dashboard", False,
            [("tax_engagements", "card"), ("tax_returns_due", "card")]),
    "insurance": ("Insurance Dashboard", False, [("insurance_cases", "card")]),
    "retirement": ("Retirement Dashboard", False,
                   [("business_plans", "card"), ("annual_reviews", "card")]),
    "client_service": ("Client Service Dashboard", False,
                       [("annual_reviews", "card"), ("annual_reviews_completed", "card"),
                        ("open_tasks", "card"), ("timeline_activity", "card")]),
    "revenue": ("Revenue Dashboard", True,
                [("aum", "card"), ("campaign_revenue", "card"), ("referral_revenue", "card"),
                 ("forecast_revenue", "card"), ("total_bd_revenue", "card")]),
    "executive_summary": ("Executive Summary", True,
                          [("aum", "card"), ("total_bd_revenue", "card"), ("pipeline_value", "card"),
                           ("client_count", "card"), ("open_work", "card"),
                           ("open_compliance_reviews", "card")]),
}


class DashboardError(Exception):
    """Unknown or inaccessible dashboard."""


def list_predefined(principal) -> list[dict]:
    return [{"code": code, "name": name, "executive_only": exec_only,
             "accessible": (not exec_only) or principal.can("analytics.executive")}
            for code, (name, exec_only, _widgets) in PREDEFINED.items()]


def compose_predefined(principal, code: str) -> dict:
    entry = PREDEFINED.get(code)
    if entry is None:
        raise DashboardError(f"unknown dashboard {code!r}")
    name, exec_only, widgets = entry
    if exec_only and not principal.can("analytics.executive"):
        raise DashboardError("executive dashboard requires analytics.executive")
    return {"code": code, "name": name, "executive_only": exec_only,
            "widgets": [_widget(principal, mk, viz) for mk, viz in widgets]}


def _widget(principal, metric_key: str, viz: str) -> dict:
    metric = metrics.compute_metric(principal, metric_key)
    var = targets.variance(principal, metric_key)
    return {"metric_key": metric_key, "viz": viz, "title": metric.get("label", metric_key),
            "value": metric.get("value"), "unit": metric.get("unit"),
            "restricted": metric.get("restricted", False),
            "available": metric.get("available", True), "variance": var}


# --- custom dashboards -------------------------------------------------------

def create_dashboard(principal, *, code, name, actor_user_id, description=None,
                     executive_only=False) -> dict:
    with engine.begin() as c:
        if c.scalar(select(analytics_dashboards.c.id)
                    .where(analytics_dashboards.c.code == code)) is not None:
            raise DashboardError("dashboard code already exists")
        row = c.execute(analytics_dashboards.insert().values(
            code=code, name=name, description=description, is_system=False,
            executive_only=executive_only, owner_user_id=actor_user_id, created_by=actor_user_id,
            updated_by=actor_user_id).returning(analytics_dashboards)).mappings().one()
    return dict(row)


def add_widget(principal, dashboard_id: int, *, title, metric_key, viz_type="card",
               dimension_key=None, sort_order=0, config=None) -> dict:
    from app.database.analytics_tables import WIDGET_VIZ_TYPES
    if viz_type not in WIDGET_VIZ_TYPES:
        raise DashboardError(f"invalid viz_type {viz_type!r}")
    if metric_key and metric_key not in metrics.METRICS:
        raise DashboardError(f"unknown metric {metric_key!r}")
    with engine.begin() as c:
        if c.scalar(select(analytics_dashboards.c.id)
                    .where(analytics_dashboards.c.id == dashboard_id)) is None:
            raise DashboardError("dashboard does not exist")
        row = c.execute(analytics_dashboard_widgets.insert().values(
            dashboard_id=dashboard_id, title=title, metric_key=metric_key, viz_type=viz_type,
            dimension_key=dimension_key, sort_order=sort_order, config=config or {})
            .returning(analytics_dashboard_widgets)).mappings().one()
    return dict(row)


def list_custom(principal) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(analytics_dashboards)
                                           .order_by(analytics_dashboards.c.name)).mappings()]


def compose_custom(principal, code: str) -> dict:
    with engine.connect() as c:
        d = c.execute(select(analytics_dashboards)
                      .where(analytics_dashboards.c.code == code)).mappings().first()
        if d is None:
            raise DashboardError(f"unknown dashboard {code!r}")
        if d["executive_only"] and not principal.can("analytics.executive"):
            raise DashboardError("executive dashboard requires analytics.executive")
        widgets = c.execute(select(analytics_dashboard_widgets)
                            .where(analytics_dashboard_widgets.c.dashboard_id == d["id"])
                            .order_by(analytics_dashboard_widgets.c.sort_order)).mappings().all()
    return {"code": d["code"], "name": d["name"], "executive_only": d["executive_only"],
            "widgets": [_widget(principal, w["metric_key"], w["viz_type"]) if w["metric_key"]
                        else {"title": w["title"], "viz": w["viz_type"]} for w in widgets]}
