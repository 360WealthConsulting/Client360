"""Deterministic rendering (Phase D.21) — compose values from the Analytics read-model.

This is where "consume Analytics, never recalculate KPIs" lives. Every KPI value is produced by
calling the Analytics service (``metrics.compute_metric`` / ``compute_many``, ``dashboards``,
``trends``, ``service.export_metrics``) — Reporting never re-queries source tables and never
recomputes a KPI. Executive gating is inherited automatically: ``compute_metric`` withholds
executive metrics (``value None, restricted True``) when the principal lacks ``analytics.executive``.
Record scope is inherited too — ``compute_metric`` computes with the principal's book scope.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import engine, reporting_kpi_groups, reporting_scorecards
from app.services.analytics import dashboards as analytics_dashboards
from app.services.analytics import metrics as analytics_metrics
from app.services.analytics import service as analytics_service
from app.services.analytics import trends as analytics_trends


def metric_value(principal, metric_key: str) -> dict:
    """Single KPI value, straight from Analytics (the authoritative read-model)."""
    return analytics_metrics.compute_metric(principal, metric_key)


def compose_metrics(principal, metric_keys) -> list[dict]:
    return analytics_metrics.compute_many(principal, list(metric_keys or []))


def _kpi_group_keys(group_id: int) -> list[str]:
    with engine.connect() as c:
        row = c.execute(select(reporting_kpi_groups.c.metric_keys)
                        .where(reporting_kpi_groups.c.id == group_id)).scalar()
    return list(row or [])


def render_widget(principal, widget: dict) -> dict:
    """Render one widget by composing Analytics. Value-bearing widget types resolve KPI values;
    presentational types (chart/table/text) return metadata only."""
    out = {"id": widget.get("id"), "title": widget["title"], "widget_type": widget["widget_type"],
           "viz_type": widget["viz_type"], "sort_order": widget.get("sort_order", 0)}
    wtype = widget["widget_type"]
    if wtype in ("metric",) and widget.get("metric_key"):
        out["value"] = metric_value(principal, widget["metric_key"])
    elif wtype == "kpi_group" and widget.get("kpi_group_id"):
        out["values"] = compose_metrics(principal, _kpi_group_keys(widget["kpi_group_id"]))
    elif wtype == "trend" and widget.get("metric_key"):
        out["trend"] = analytics_trends.metric_trend(widget["metric_key"])
    elif wtype == "scorecard" and widget.get("metric_key"):
        # a scorecard widget may point at an analytics predefined scorecard code via metric_key
        try:
            out["scorecard"] = analytics_dashboards.compose_predefined(principal, widget["metric_key"])
        except Exception:
            out["scorecard"] = None
    else:
        out["note"] = "presentational"
    return out


def render_dashboard(principal, dashboard: dict, widgets: list[dict]) -> dict:
    return {"dashboard": dashboard,
            "widgets": [render_widget(principal, w) for w in sorted(
                widgets, key=lambda w: (w.get("sort_order", 0), w.get("id", 0)))]}


def render_scorecard(principal, scorecard: dict) -> dict:
    """A reporting scorecard = a saved bundle of Analytics metric keys, composed on read."""
    return {"scorecard": {"code": scorecard["code"], "name": scorecard["name"],
                          "category": scorecard["category"]},
            "metrics": compose_metrics(principal, scorecard.get("metric_keys") or [])}


def render_scorecard_by_code(principal, code: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(reporting_scorecards)
                        .where(reporting_scorecards.c.code == code)).mappings().first()
    return render_scorecard(principal, dict(row)) if row else None


def compose_definition(principal, definition: dict) -> dict:
    """Compose a report definition's metric keys into values (from Analytics)."""
    spec = definition.get("definition") or {}
    metric_keys = spec.get("metric_keys") or []
    result = {"definition_id": definition["id"], "name": definition["name"],
              "report_type": definition["report_type"], "category": definition["category"],
              "metrics": compose_metrics(principal, metric_keys)}
    if definition.get("kpi_group_id"):
        result["kpi_group"] = compose_metrics(principal, _kpi_group_keys(definition["kpi_group_id"]))
    return result


def export_values(principal, metric_keys=None) -> dict:
    """Reuse the ONLY existing export producer (Analytics ``export_metrics``); no binary generation
    is implemented in Reporting — export profiles are metadata that select this producer."""
    return analytics_service.export_metrics(principal, metric_keys)


def predefined_scorecards(principal) -> list[dict]:
    """Surface the Analytics predefined scorecards (executive gating applied by Analytics)."""
    return analytics_dashboards.list_predefined(principal)


def available_metrics(principal) -> list[dict]:
    return analytics_metrics.list_metrics(principal)
