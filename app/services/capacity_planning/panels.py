"""Enterprise Capacity Planning panel composition (Phase D.61).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric,
and never any employee detail / payroll / HR record / calendar content / time entry / sensitive staffing
datum. Capacity / utilization panels compose the Operations capacity owner; workload / queue / assignment
panels compose the Work Queue; staffing panels compose Practice Management; automation panels compose
Automation Orchestration; workforce / capacity catalog + posture panels are DERIVED from the declarative
registries (labeled ``derived``). HR employee directory, contractors, PTO / availability, meeting / onboarding
/ planning capacity have no authoritative owner and are emitted ``available=False`` with
``config_status='not_configured'`` — honest, never a fabricated staffing / availability figure. Every compose
is fail-closed and self-restricts: a principal lacking the panel's capability is shown a ``restricted`` panel,
never its value or count. This layer NEVER assigns work, approves PTO, creates meetings, moves calendar events,
schedules resources, or modifies assignments — it only composes counts, status, and coverage. A derived value
describes an operational summary, never a certified staffing / utilization figure and never an HR record.
"""
from __future__ import annotations

from . import registry, stats
from .model import PanelResult


def _restricted(pdef):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=None,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, restricted=True,
                       available=False, derived=pdef.derived)


def _result(pdef, value, *, available=True, config_status="configured"):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=value,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, available=available,
                       derived=pdef.derived, config_status=config_status)


def _kpi(summary, key):
    if not isinstance(summary, dict):
        return None
    return (summary.get("kpis") or {}).get(key)


# --- Operations capacity owner -------------------------------------------------------------------------

def _capacity_overview(principal):
    from app.services.operations.capacity import capacity_overview
    return capacity_overview(principal)


def _firm_capacity_utilization(principal, pdef):
    try:
        m = _capacity_overview(principal)
        total = m.get("resource_count", 0) or 0
        over = m.get("over_capacity_count", 0) or 0
        pct = round(over / total * 100, 1) if total else 0.0
        return _result(pdef, {"resource_count": total, "over_capacity_count": over,
                              "over_capacity_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _over_capacity_resources(principal, pdef):
    try:
        m = _capacity_overview(principal)
        return _result(pdef, {"over_capacity_count": m.get("over_capacity_count", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _department_capacity(principal, pdef):
    try:
        m = _capacity_overview(principal)
        return _result(pdef, {"resource_count": m.get("resource_count", 0),
                              "over_capacity_count": m.get("over_capacity_count", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _utilization_summary(principal, pdef):
    try:
        m = _capacity_overview(principal)
        total = m.get("resource_count", 0) or 0
        over = m.get("over_capacity_count", 0) or 0
        return _result(pdef, {"resource_count": total, "over_capacity_count": over,
                              "at_capacity_percent": (round(over / total * 100, 1) if total else 0.0)})
    except Exception:
        return _result(pdef, None, available=False)


def _capacity_horizon(principal, pdef):
    try:
        from app.services.operations.capacity import list_capacity_plans
        return _result(pdef, {"capacity_plans": len(list_capacity_plans())})
    except Exception:
        return _result(pdef, None, available=False)


# --- Practice Management (staffing) --------------------------------------------------------------------

def _staffing_recommendations(principal, pdef):
    try:
        from app.services.practice_management import practice_summary
        s = practice_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"staffing_recommendations": _kpi(s, "staffing_recommendations"),
                              "firm_capacity_utilization": _kpi(s, "firm_capacity_utilization")})
    except Exception:
        return _result(pdef, None, available=False)


# --- Work Queue (workload / queue / assignment) --------------------------------------------------------

def _work_queue_summary(principal):
    from app.services.work_queue.summary import work_queue_summary
    return work_queue_summary(principal)


def _wq(principal, pdef, keys):
    try:
        s = _work_queue_summary(principal)
        if not isinstance(s, dict):
            return _result(pdef, None, available=False)
        return _result(pdef, {k: s.get(k) for k in keys})
    except Exception:
        return _result(pdef, None, available=False)


def _advisor_workload_distribution(principal, pdef):
    return _wq(principal, pdef, ("by_domain", "unassigned_team"))


def _workload_by_domain(principal, pdef):
    return _wq(principal, pdef, ("by_domain",))


def _open_backlog(principal, pdef):
    return _wq(principal, pdef, ("my_overdue", "unassigned_team"))


def _unassigned_backlog(principal, pdef):
    return _wq(principal, pdef, ("unassigned_team",))


def _sla_backlog(principal, pdef):
    return _wq(principal, pdef, ("sla_breaches",))


def _queue_health(principal, pdef):
    return _wq(principal, pdef, ("my_overdue", "sla_breaches", "unassigned_team"))


def _assignment_distribution(principal, pdef):
    return _wq(principal, pdef, ("by_domain", "unassigned_team"))


# --- Automation Orchestration --------------------------------------------------------------------------

def _automation_summary(principal):
    from app.services.automation_orchestration import automation_summary
    return automation_summary(principal)


def _automation_workload(principal, pdef):
    try:
        s = _automation_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"workflow_status": _kpi(s, "workflow_status"),
                              "failed_runs": _kpi(s, "failed_runs")})
    except Exception:
        return _result(pdef, None, available=False)


def _workflow_escalations(principal, pdef):
    try:
        s = _automation_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"open_escalations": _kpi(s, "open_escalations"),
                              "workflow_pending_approvals": _kpi(s, "workflow_pending_approvals")})
    except Exception:
        return _result(pdef, None, available=False)


# --- registry-derived (DERIVED) ------------------------------------------------------------------------

def _workforce_inventory(principal, pdef):
    try:
        nc = [w.key for w in registry.WORKFORCE_REGISTRY if w.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"count": len(registry.WORKFORCE_REGISTRY),
                              "classes": [w.key for w in registry.WORKFORCE_REGISTRY], "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _registered_workforce(principal, pdef):
    try:
        owned = [w.key for w in registry.WORKFORCE_REGISTRY if w.owner != registry.NOT_CONFIGURED]
        nc = [w.key for w in registry.WORKFORCE_REGISTRY if w.owner == registry.NOT_CONFIGURED]
        return _result(pdef, {"with_owner": len(owned), "total": len(registry.WORKFORCE_REGISTRY),
                              "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _registered_capacity(principal, pdef):
    try:
        nc = [c.key for c in registry.CAPACITY_REGISTRY if c.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"count": len(registry.CAPACITY_REGISTRY),
                              "categories": [c.key for c in registry.CAPACITY_REGISTRY], "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _staffing_readiness(principal, pdef):
    try:
        owned = [w.key for w in registry.WORKFORCE_REGISTRY if w.owner != registry.NOT_CONFIGURED]
        total = len(registry.WORKFORCE_REGISTRY)
        pct = round(len(owned) / total * 100, 1) if total else 0.0
        return _result(pdef, {"with_owner": len(owned), "total": total, "coverage_percent": pct,
                              "operational_summary_not_hr_record": True})
    except Exception:
        return _result(pdef, None, available=False)


def _staffing_gaps(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


def _capacity_forecast(principal, pdef):
    try:
        owned = [c.key for c in registry.CAPACITY_REGISTRY if c.config_status == registry.CONFIGURED]
        total = len(registry.CAPACITY_REGISTRY)
        nc = [c.key for c in registry.CAPACITY_REGISTRY if c.config_status == registry.NOT_CONFIGURED]
        pct = round(len(owned) / total * 100, 1) if total else 0.0
        return _result(pdef, {"configured": len(owned), "total": total, "coverage_percent": pct,
                              "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _availability_summary(principal, pdef):
    return _result(pdef, {"status": registry.NOT_CONFIGURED,
                          "note": "no authoritative PTO / availability owner exists in the platform"},
                   available=False, config_status=registry.NOT_CONFIGURED)


def _executive_workforce_status(principal, pdef):
    """DERIVED executive workforce posture — deterministic, authoritative inputs, labeled derived. An
    operational summary only, never a certified staffing / utilization figure and never an HR record."""
    try:
        configured = len(registry.configured_domains())
        not_configured = list(registry.not_configured_domains())
        signals = {}
        try:
            m = _capacity_overview(principal)
            signals["over_capacity_count"] = m.get("over_capacity_count", 0)
            signals["resource_count"] = m.get("resource_count", 0)
        except Exception:
            pass
        try:
            s = _work_queue_summary(principal)
            if isinstance(s, dict):
                signals["sla_breaches"] = s.get("sla_breaches")
        except Exception:
            pass
        return _result(pdef, {"derived": True, "operational_summary_not_hr_record": True,
                              "not_a_certified_staffing_figure": True,
                              "configured_domains": configured, "not_configured_domains": len(not_configured),
                              "not_configured": not_configured, "signals": signals})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "firm_capacity_utilization": _firm_capacity_utilization,
    "over_capacity_resources": _over_capacity_resources,
    "department_capacity": _department_capacity,
    "capacity_horizon": _capacity_horizon,
    "utilization_summary": _utilization_summary,
    "staffing_recommendations": _staffing_recommendations,
    "staffing_readiness": _staffing_readiness,
    "staffing_gaps": _staffing_gaps,
    "availability_summary": _availability_summary,
    "advisor_workload_distribution": _advisor_workload_distribution,
    "workload_by_domain": _workload_by_domain,
    "open_backlog": _open_backlog,
    "unassigned_backlog": _unassigned_backlog,
    "sla_backlog": _sla_backlog,
    "queue_health": _queue_health,
    "assignment_distribution": _assignment_distribution,
    "automation_workload": _automation_workload,
    "workflow_escalations": _workflow_escalations,
    "capacity_forecast": _capacity_forecast,
    "workforce_inventory": _workforce_inventory,
    "registered_workforce": _registered_workforce,
    "registered_capacity": _registered_capacity,
    "executive_workforce_status": _executive_workforce_status,
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
