"""Executive widget composition (Phase D.48).

Each widget's value is computed on READ by its authoritative service — never persisted, never a second
metric. KPI widgets flow through the SINGLE Analytics Registry (``analytics.metrics.compute_metric``), which
enforces the ``analytics.executive`` gate + record scope automatically; firm-level widgets read the
authoritative firm reads (work queue, workflow, portfolio, opportunity, communications, runtime,
Operational Intelligence). Every compute is fail-closed (a source outage yields an unavailable widget, never
an exception). Returns a ``WidgetResult`` or ``None`` if the widget is not registered / not explainable.
"""
from __future__ import annotations

from . import registry, stats
from .model import WidgetResult


def _accessible_person_ids(principal):
    try:
        from app.db import engine
        from app.security.authorization import accessible_person_ids
        with engine.connect() as conn:
            return accessible_person_ids(conn, principal)
    except Exception:
        return set()


def _metric_widget(principal, wdef, metric_key):
    """A KPI widget backed by the Analytics Registry — inherits the executive gate + scope from
    ``compute_metric`` (executive metrics return restricted for non-executives)."""
    from app.services.analytics.metrics import compute_metric
    m = compute_metric(principal, metric_key)
    return WidgetResult(
        key=wdef.key, title=wdef.explainability.split(",")[0][:60] or wdef.key.replace("_", " ").title(),
        owner=wdef.owner, source=wdef.source, aggregation=wdef.aggregation,
        unit=m.get("unit") or wdef.unit, viz=wdef.viz, value=m.get("value"),
        explanation=wdef.explainability, deep_link=wdef.deep_link,
        restricted=bool(m.get("restricted")), available=bool(m.get("available", m.get("value") is not None)))


def _result(wdef, value, *, restricted=False, available=True):
    return WidgetResult(
        key=wdef.key, title=wdef.key.replace("_", " ").title(), owner=wdef.owner, source=wdef.source,
        aggregation=wdef.aggregation, unit=wdef.unit, viz=wdef.viz, value=value,
        explanation=wdef.explainability, deep_link=wdef.deep_link, restricted=restricted, available=available)


# --- per-widget compute functions (read-only, fail-closed) -------------------

def _firm_aum(principal, wdef):
    return _metric_widget(principal, wdef, "aum")


def _aum_trend(principal, wdef):
    if not principal.can("analytics.executive"):
        return _result(wdef, None, restricted=True, available=False)
    try:
        from app.services.analytics.trends import metric_trend
        t = metric_trend("aum")
        return _result(wdef, {"series": t.get("series", []), "growth": t.get("period_over_period_growth")})
    except Exception:
        return _result(wdef, None, available=False)


def _revenue_kpi(principal, wdef):
    return _metric_widget(principal, wdef, "total_bd_revenue")


def _client_growth(principal, wdef):
    return _metric_widget(principal, wdef, "client_count")


def _compliance_workload(principal, wdef):
    return _metric_widget(principal, wdef, "open_compliance_reviews")


def _advisor_workload(principal, wdef):
    try:
        from app.services.work_queue.summary import work_queue_summary
        s = work_queue_summary(principal)
        return _result(wdef, {"by_domain": s.get("by_domain", {}), "my_overdue": s.get("my_overdue", 0),
                              "sla_breaches": s.get("sla_breaches", 0)})
    except Exception:
        return _result(wdef, None, available=False)


def _workflow_status(principal, wdef):
    try:
        from app.services.workflow_automation import workflow_metrics
        m = workflow_metrics()
        return _result(wdef, {"by_status": m.get("by_status", {})})
    except Exception:
        return _result(wdef, None, available=False)


def _workflow_aging(principal, wdef):
    try:
        from app.services.workflow_automation import workflow_metrics
        m = workflow_metrics()
        return _result(wdef, {"open_escalations": m.get("open_escalations", 0),
                              "pending_approvals": m.get("pending_approvals", 0)})
    except Exception:
        return _result(wdef, None, available=False)


def _review_cadence(principal, wdef):
    try:
        from app.services.portfolio import accounts_due_for_review
        rows = accounts_due_for_review(_accessible_person_ids(principal), limit=500)
        return _result(wdef, len(rows))
    except Exception:
        return _result(wdef, None, available=False)


def _opportunity_pipeline(principal, wdef):
    try:
        from app.services.opportunity.reporting import pipeline_report
        r = pipeline_report(principal)
        return _result(wdef, {"open_value": r.get("open_value"), "counts": r.get("counts", {})})
    except Exception:
        return _result(wdef, None, available=False)


def _communication_activity(principal, wdef):
    try:
        from app.services.communications.service import metrics as comms_metrics
        m = comms_metrics(principal)
        return _result(wdef, {"open_conversations": m.get("open_conversations", 0),
                              "messages": m.get("messages", 0)})
    except Exception:
        return _result(wdef, None, available=False)


def _tax_workload(principal, wdef):
    try:
        from app.services.tax_domain import dashboard as tax_dashboard
        d = tax_dashboard(principal)
        return _result(wdef, d.get("metrics", {}))
    except Exception:
        return _result(wdef, None, available=False)


def _operational_health(principal, wdef):
    try:
        from app.services.recommendations import workspace_recommendations
        r = workspace_recommendations(principal)
        return _result(wdef, {"total": r.get("total", 0), "by_severity": r.get("counts", {}).get("by_severity", {})})
    except Exception:
        return _result(wdef, None, available=False)


def _runtime_health(principal, wdef):
    try:
        from app.services.runtime.consumption import adoption_stats
        a = adoption_stats()
        out = {"runtime_adoption_pct": a.get("runtime_adoption_pct")}
        try:
            from app.services.observability.health import metrics as health_metrics
            h = health_metrics(principal)
            out["failed_health_checks"] = h.get("failed_health_checks", 0)
        except Exception:
            pass
        return _result(wdef, out)
    except Exception:
        return _result(wdef, None, available=False)


_COMPUTE = {
    "firm_aum": _firm_aum, "aum_trend": _aum_trend, "revenue_kpi": _revenue_kpi,
    "client_growth": _client_growth, "compliance_workload": _compliance_workload,
    "advisor_workload": _advisor_workload, "workflow_status": _workflow_status,
    "workflow_aging": _workflow_aging, "review_cadence": _review_cadence,
    "opportunity_pipeline": _opportunity_pipeline, "communication_activity": _communication_activity,
    "tax_workload": _tax_workload, "operational_health": _operational_health,
    "runtime_health": _runtime_health,
}


def compute_widget(principal, key):
    """Compose one widget by key. Read-only, fail-closed. Returns a WidgetResult, or None if the widget is
    not registered / not explainable."""
    wdef = registry.widget(key)
    fn = _COMPUTE.get(key)
    if wdef is None or fn is None:
        return None
    try:
        result = fn(principal, wdef)
    except Exception:
        stats.note("aggregation_failures", widget=key)
        return None
    if result is None or not result.is_explainable:
        stats.note("missing_explainability", widget=key)
        return None
    stats.note("widgets_composed")
    if result.restricted:
        stats.note("restricted_widgets")
    return result
