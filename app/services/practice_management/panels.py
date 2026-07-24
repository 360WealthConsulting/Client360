"""Practice Management panel composition (Phase D.49).

Each panel's value is composed on READ by its authoritative service — never persisted, never re-computed,
never a second metric. Capacity/utilization panels compose the AUTHORITATIVE capacity owner
(``operations.capacity`` — Phase D.20), workload/backlog/SLA panels compose the Unified Work Queue, aging
panels compose Workflow Automation's firm metrics, pipeline/overload panels compose the opportunity +
Analytics firm-intelligence layers, and tax panels compose the tax domain. Every compose is fail-closed (a
source outage yields an unavailable panel, never an exception) and self-restricts: a principal lacking the
panel's capability is shown a ``restricted`` panel, never its value. This layer NEVER assigns work,
rebalances staff, modifies schedules, or mutates anything — staffing signals are deterministic, read-only
advisory summaries.
"""
from __future__ import annotations

from . import registry, stats
from .model import PanelResult


def _restricted(pdef):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=None,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, restricted=True,
                       available=False)


def _result(pdef, value, *, available=True):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=value,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, available=available)


# --- capacity / utilization (compose the authoritative Operations Capacity owner) --------------------

def _capacity_overview(principal):
    from app.services.operations.capacity import capacity_overview
    return capacity_overview(principal)


def _firm_capacity_utilization(principal, pdef):
    try:
        ov = _capacity_overview(principal)
        rows = ov.get("resources", [])
        avg = round(sum(r["utilization_percent"] for r in rows) / len(rows), 1) if rows else 0.0
        return _result(pdef, {"resource_count": ov.get("resource_count", 0),
                              "over_capacity_count": ov.get("over_capacity_count", 0),
                              "avg_utilization_percent": avg})
    except Exception:
        return _result(pdef, None, available=False)


def _department_capacity(principal, pdef):
    try:
        ov = _capacity_overview(principal)
        by_dept: dict = {}
        for r in ov.get("resources", []):
            # resources carry no department here; group by resource_name prefix is not meaningful, so we
            # report the firm rollup + per-resource utilization for the department view (authoritative rows).
            by_dept.setdefault("all", []).append(r["utilization_percent"])
        departments = {d: round(sum(v) / len(v), 1) for d, v in by_dept.items() if v}
        return _result(pdef, {"departments": departments,
                              "resources": [{"resource_id": r["resource_id"], "name": r["resource_name"],
                                             "utilization_percent": r["utilization_percent"],
                                             "over_capacity": r["over_capacity"]}
                                            for r in ov.get("resources", [])]})
    except Exception:
        return _result(pdef, None, available=False)


def _over_capacity_resources(principal, pdef):
    try:
        ov = _capacity_overview(principal)
        over = [{"resource_id": r["resource_id"], "name": r["resource_name"],
                 "utilization_percent": r["utilization_percent"],
                 "committed_minutes": r["committed_minutes"], "available_minutes": r["available_minutes"]}
                for r in ov.get("resources", []) if r["over_capacity"]]
        return _result(pdef, {"count": len(over), "resources": over})
    except Exception:
        return _result(pdef, None, available=False)


def _capacity_horizon(principal, pdef):
    try:
        from app.services.operations.capacity import list_capacity_plans
        plans = list_capacity_plans()
        horizons: dict = {}
        for cm in registry.CAPACITY_REGISTRY:
            horizons[cm.planning_horizon] = horizons.get(cm.planning_horizon, 0) + 1
        return _result(pdef, {"registered_plans": len(plans),
                              "capacity_models_by_horizon": horizons})
    except Exception:
        return _result(pdef, None, available=False)


def _staffing_recommendations(principal, pdef):
    """Deterministic, read-only staffing SIGNALS — advisory only. Never assigns, rebalances, or mutates."""
    try:
        signals = []
        try:
            ov = _capacity_overview(principal)
            over = ov.get("over_capacity_count", 0)
            if over:
                signals.append({"signal": "over_capacity", "count": over,
                                "detail": f"{over} resource(s) over declared capacity.",
                                "deep_link": "/operations/capacity"})
        except Exception:
            stats.note("aggregation_failures", panel="staffing_recommendations")
        try:
            from app.services.work_queue.summary import work_queue_summary
            s = work_queue_summary(principal)
            if s.get("unassigned_team"):
                signals.append({"signal": "unassigned_backlog", "count": s["unassigned_team"],
                                "detail": f"{s['unassigned_team']} unassigned work item(s) awaiting owner.",
                                "deep_link": "/work?assignee=unassigned"})
            if s.get("sla_breaches"):
                signals.append({"signal": "sla_pressure", "count": s["sla_breaches"],
                                "detail": f"{s['sla_breaches']} SLA breach(es).", "deep_link": "/work"})
        except Exception:
            stats.note("aggregation_failures", panel="staffing_recommendations")
        return _result(pdef, {"advisory_only": True, "signal_count": len(signals), "signals": signals})
    except Exception:
        return _result(pdef, None, available=False)


# --- workload / backlog / SLA (compose the Unified Work Queue) ---------------------------------------

def _summary(principal):
    from app.services.work_queue.summary import work_queue_summary
    return work_queue_summary(principal)


def _advisor_workload_distribution(principal, pdef):
    try:
        s = _summary(principal)
        return _result(pdef, {"by_domain": s.get("by_domain", {}), "my_open": s.get("my_open", 0),
                              "my_overdue": s.get("my_overdue", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _workload_by_domain(principal, pdef):
    try:
        s = _summary(principal)
        return _result(pdef, {"by_domain": s.get("by_domain", {}),
                              "total_visible": s.get("total_visible", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _open_backlog(principal, pdef):
    try:
        from app.services.work_queue.service import compose_queue
        q = compose_queue(principal, page=1, page_size=1)
        counts = q.get("counts", {})
        return _result(pdef, {"open": q.get("total", 0), "overdue": counts.get("overdue", 0),
                              "unassigned": counts.get("unassigned", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _unassigned_backlog(principal, pdef):
    try:
        s = _summary(principal)
        return _result(pdef, {"unassigned_team": s.get("unassigned_team", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _sla_backlog(principal, pdef):
    try:
        s = _summary(principal)
        return _result(pdef, {"sla_breaches": s.get("sla_breaches", 0),
                              "high_priority": s.get("high_priority", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _sla_performance(principal, pdef):
    try:
        s = _summary(principal)
        total = s.get("total_visible", 0) or 0
        breaches = s.get("sla_breaches", 0) or 0
        on_track_pct = round((total - breaches) / total * 100, 1) if total else 100.0
        return _result(pdef, {"total_visible": total, "sla_breaches": breaches,
                              "on_track_percent": on_track_pct})
    except Exception:
        return _result(pdef, None, available=False)


# --- pipeline / overload (compose opportunity + Analytics firm-intelligence) -------------------------

def _advisor_open_pipeline(principal, pdef):
    try:
        from app.services.opportunity.intelligence import pipeline_intelligence
        pi = pipeline_intelligence(principal)
        return _result(pdef, {"advisor_open_counts": pi.get("advisor_open_counts", {}),
                              "capacity_warnings": pi.get("advisor_capacity_warnings", [])})
    except Exception:
        return _result(pdef, None, available=False)


def _advisor_overload(principal, pdef):
    try:
        from app.services.analytics.intelligence import firm_intelligence
        obs = firm_intelligence(principal).get("observations", [])
        overload = [o for o in obs if o.get("kind") == "advisor_overload"]
        return _result(pdef, {"count": len(overload), "observations": overload})
    except Exception:
        return _result(pdef, None, available=False)


# --- workflow aging (compose Workflow Automation firm metrics) ---------------------------------------

def _workflow_metrics():
    from app.services.workflow_automation import workflow_metrics
    return workflow_metrics()


def _workflow_open_escalations(principal, pdef):
    try:
        return _result(pdef, {"open_escalations": _workflow_metrics().get("open_escalations", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _workflow_pending_approvals(principal, pdef):
    try:
        return _result(pdef, {"pending_approvals": _workflow_metrics().get("pending_approvals", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _workflow_by_status(principal, pdef):
    try:
        return _result(pdef, {"by_status": _workflow_metrics().get("by_status", {})})
    except Exception:
        return _result(pdef, None, available=False)


def _workflow_escalation_rate(principal, pdef):
    try:
        m = _workflow_metrics()
        return _result(pdef, {"open_escalations": m.get("open_escalations", 0),
                              "pending_approvals": m.get("pending_approvals", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- tax / seasonal (compose the tax domain) ---------------------------------------------------------

def _tax_dashboard(principal):
    from app.services.tax_domain import dashboard as tax_dashboard
    return tax_dashboard(principal)


def _tax_workload(principal, pdef):
    try:
        return _result(pdef, _tax_dashboard(principal).get("metrics", {}))
    except Exception:
        return _result(pdef, None, available=False)


def _seasonal_tax_forecast(principal, pdef):
    try:
        m = _tax_dashboard(principal).get("metrics", {})
        return _result(pdef, {"due_30_days": m.get("due_30_days", 0), "overdue": m.get("overdue", 0),
                              "returns": m.get("returns", 0)})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "firm_capacity_utilization": _firm_capacity_utilization,
    "department_capacity": _department_capacity,
    "over_capacity_resources": _over_capacity_resources,
    "capacity_horizon": _capacity_horizon,
    "staffing_recommendations": _staffing_recommendations,
    "advisor_workload_distribution": _advisor_workload_distribution,
    "advisor_open_pipeline": _advisor_open_pipeline,
    "advisor_overload": _advisor_overload,
    "workload_by_domain": _workload_by_domain,
    "tax_workload": _tax_workload,
    "open_backlog": _open_backlog,
    "unassigned_backlog": _unassigned_backlog,
    "sla_backlog": _sla_backlog,
    "workflow_open_escalations": _workflow_open_escalations,
    "workflow_pending_approvals": _workflow_pending_approvals,
    "workflow_by_status": _workflow_by_status,
    "seasonal_tax_forecast": _seasonal_tax_forecast,
    "sla_performance": _sla_performance,
    "workflow_escalation_rate": _workflow_escalation_rate,
}


def compute_panel(principal, key):
    """Compose one panel by key. Read-only, fail-closed, self-restricting. Returns a PanelResult, or None
    if the panel is not registered / not explainable."""
    pdef = registry.panel(key)
    fn = _COMPUTE.get(key)
    if pdef is None or fn is None:
        return None
    try:
        entitled = principal.can(pdef.permission)
    except Exception:
        entitled = False
    if not entitled:
        stats.note("restricted_panels")
        return _restricted(pdef)
    try:
        result = fn(principal, pdef)
    except Exception:
        stats.note("aggregation_failures", panel=key)
        return None
    if result is None or not result.is_explainable:
        stats.note("missing_explainability", panel=key)
        return None
    stats.note("panels_composed")
    return result
