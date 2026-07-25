"""Enterprise Operational Resilience panel composition (Phase D.60).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric,
and never any sensitive operational payload. Service-health panels compose the Observability service catalog /
health owner; incident / alert / maintenance panels compose Observability incidents / alerts + Security
incidents; integration panels compose the Integration Platform; vendor panels compose Vendor Management;
workflow panels compose Automation Orchestration; continuity / recovery panels compose Business Continuity;
catalog / posture panels are DERIVED from the declarative registries (labeled ``derived``). Recovery-testing /
failover / backup / restore / disaster-recovery / vendor-incident owners do not exist and are emitted
``available=False`` with ``config_status='not_configured'`` — honest, never a fabricated operational status.
Every compose is fail-closed and self-restricts: a principal lacking the panel's capability is shown a
``restricted`` panel, never its value or count. This layer NEVER creates an incident, acknowledges an alert,
executes recovery, modifies monitoring, schedules maintenance, or closes an incident — it only composes counts,
status, and coverage. A derived value describes operational posture, never a certification that production is
healthy or continuity assured, and never infers recovery success.
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


# --- Observability service catalog / health / incidents / alerts ---------------------------------------

def _service_health(principal, pdef):
    try:
        from app.services.observability.catalog import metrics
        m = metrics(principal)
        total = m.get("total_services", 0) or 0
        op = m.get("operational_services", 0)
        pct = round(op / total * 100, 1) if total else 100.0
        return _result(pdef, {"operational_services": op, "total_services": total,
                              "operational_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _degraded_services(principal, pdef):
    try:
        from app.services.observability.catalog import metrics
        m = metrics(principal)
        return _result(pdef, {"degraded_services": m.get("degraded_services", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _failed_health_checks(principal, pdef):
    try:
        from app.services.observability.health import metrics
        m = metrics(principal)
        return _result(pdef, {"failed_health_checks": m.get("failed_health_checks", 0),
                              "diagnostic_failures": m.get("diagnostic_failures", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _reliability_incidents(principal, pdef):
    try:
        from app.services.observability.incidents import metrics
        m = metrics(principal)
        return _result(pdef, {"reliability_incidents": m.get("reliability_incidents", 0),
                              "reliability_findings": m.get("reliability_findings", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _open_alerts(principal, pdef):
    try:
        from app.services.observability.alerts import metrics
        m = metrics(principal)
        return _result(pdef, {"open_alerts": m.get("open_alerts", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _active_maintenance_windows(principal, pdef):
    try:
        from app.services.observability.alerts import metrics
        m = metrics(principal)
        return _result(pdef, {"active_maintenance_windows": m.get("active_maintenance_windows", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _dependency_health(principal, pdef):
    try:
        from app.services.observability.catalog import list_dependencies
        return _result(pdef, {"declared_dependencies": len(list_dependencies())})
    except Exception:
        return _result(pdef, None, available=False)


# --- Security incidents --------------------------------------------------------------------------------

def _security_incidents(principal, pdef):
    try:
        from app.services.security.incidents import metrics
        m = metrics(principal)
        return _result(pdef, {"open_incidents": m.get("open_incidents", 0),
                              "open_findings": m.get("open_findings", 0),
                              "pending_exceptions": m.get("pending_exceptions", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Integration Platform ------------------------------------------------------------------------------

def _integration_failures(principal, pdef):
    try:
        from app.services.integration.service import overview_metrics
        m = overview_metrics(principal)
        return _result(pdef, {"providers": m.get("providers", 0),
                              "connected_connectors": m.get("connected_connectors", 0),
                              "sync_failures": m.get("sync_failures", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _synchronization_failures(principal, pdef):
    try:
        from app.services.integration.sync import metrics
        m = metrics(principal)
        return _result(pdef, {"sync_failures": m.get("sync_failures", 0),
                              "connector_errors": m.get("connector_errors", 0),
                              "unresolved_conflicts": m.get("unresolved_conflicts", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Automation Orchestration --------------------------------------------------------------------------

def _workflow_escalations(principal, pdef):
    try:
        from app.services.automation_orchestration import automation_summary
        s = automation_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"open_escalations": _kpi(s, "open_escalations"),
                              "failed_runs": _kpi(s, "failed_runs")})
    except Exception:
        return _result(pdef, None, available=False)


# --- Vendor Management ---------------------------------------------------------------------------------

def _vendor_operational_status(principal, pdef):
    try:
        from app.services.vendor_management import vendor_summary
        s = vendor_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"vendor_governance_score": _kpi(s, "vendor_governance_score"),
                              "integration_dependencies": _kpi(s, "integration_dependencies"),
                              "expiring_certificates": _kpi(s, "expiring_certificates"),
                              "vendor_incidents": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


# --- Business Continuity (compose D.55) ----------------------------------------------------------------

def _continuity_summary(principal):
    from app.services.business_continuity import continuity_summary
    return continuity_summary(principal)


def _resilience_posture(principal, pdef):
    try:
        s = _continuity_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"resilience_score": _kpi(s, "resilience_score")})
    except Exception:
        return _result(pdef, None, available=False)


def _infrastructure_availability(principal, pdef):
    try:
        s = _continuity_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"infrastructure_availability": _kpi(s, "infrastructure_availability")})
    except Exception:
        return _result(pdef, None, available=False)


def _continuity_coverage(principal, pdef):
    try:
        s = _continuity_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"backup_coverage": _kpi(s, "backup_coverage"),
                              "service_incidents": _kpi(s, "service_incidents"),
                              "backup_restore_dr": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


# --- registry-derived (DERIVED) ------------------------------------------------------------------------

def _operational_service_inventory(principal, pdef):
    try:
        return _result(pdef, {"count": len(registry.OPERATIONAL_SERVICE_REGISTRY),
                              "services": [s.key for s in registry.OPERATIONAL_SERVICE_REGISTRY]})
    except Exception:
        return _result(pdef, None, available=False)


def _incident_category_inventory(principal, pdef):
    try:
        nc = [i.key for i in registry.INCIDENT_CATEGORY_REGISTRY
              if i.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"count": len(registry.INCIDENT_CATEGORY_REGISTRY),
                              "categories": [i.key for i in registry.INCIDENT_CATEGORY_REGISTRY],
                              "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _service_dependencies(principal, pdef):
    try:
        return _result(pdef, {"count": len(registry.OPERATIONAL_DEPENDENCY_REGISTRY),
                              "dependencies": [d.key for d in registry.OPERATIONAL_DEPENDENCY_REGISTRY]})
    except Exception:
        return _result(pdef, None, available=False)


def _recovery_readiness(principal, pdef):
    try:
        owned = [r.key for r in registry.RECOVERY_OBJECTIVE_REGISTRY
                 if r.config_status == registry.CONFIGURED]
        nc = [r.key for r in registry.RECOVERY_OBJECTIVE_REGISTRY
              if r.config_status == registry.NOT_CONFIGURED]
        total = len(registry.RECOVERY_OBJECTIVE_REGISTRY)
        pct = round(len(owned) / total * 100, 1) if total else 0.0
        return _result(pdef, {"configured": len(owned), "total": total, "coverage_percent": pct,
                              "not_configured": nc, "operational_posture_not_certification": True})
    except Exception:
        return _result(pdef, None, available=False)


def _rpo_targets(principal, pdef):
    try:
        return _result(pdef, {"assets": ["recovery_assets", "rpo_targets"], "declarative": True})
    except Exception:
        return _result(pdef, None, available=False)


def _rto_targets(principal, pdef):
    try:
        return _result(pdef, {"assets": ["recovery_assets", "rto_targets"], "declarative": True})
    except Exception:
        return _result(pdef, None, available=False)


def _resilience_gaps(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


def _executive_operational_status(principal, pdef):
    """DERIVED operational posture — deterministic, authoritative inputs, labeled derived. Operational posture
    only, never a certification that production is healthy or continuity assured; an absent incident is not
    health."""
    try:
        configured = len(registry.configured_domains())
        not_configured = list(registry.not_configured_domains())
        signals = {}
        try:
            from app.services.observability.catalog import metrics as cat_metrics
            m = cat_metrics(principal)
            signals["degraded_services"] = m.get("degraded_services", 0)
        except Exception:
            pass
        try:
            from app.services.observability.incidents import metrics as inc_metrics
            signals["reliability_incidents"] = inc_metrics(principal).get("reliability_incidents", 0)
        except Exception:
            pass
        try:
            from app.services.observability.alerts import metrics as alert_metrics
            signals["open_alerts"] = alert_metrics(principal).get("open_alerts", 0)
        except Exception:
            pass
        return _result(pdef, {"derived": True, "operational_posture_not_certification": True,
                              "not_production_health_certification": True,
                              "configured_domains": configured, "not_configured_domains": len(not_configured),
                              "not_configured": not_configured, "open_signal_counts": signals,
                              "absent_incident_is_not_health": True})
    except Exception:
        return _result(pdef, None, available=False)


def _recovery_test_coverage(principal, pdef):
    return _result(pdef, {"status": registry.NOT_CONFIGURED,
                          "note": "no authoritative recovery-testing owner exists in the platform"},
                   available=False, config_status=registry.NOT_CONFIGURED)


_COMPUTE = {
    "service_health": _service_health,
    "degraded_services": _degraded_services,
    "failed_health_checks": _failed_health_checks,
    "reliability_incidents": _reliability_incidents,
    "security_incidents": _security_incidents,
    "open_alerts": _open_alerts,
    "active_maintenance_windows": _active_maintenance_windows,
    "integration_failures": _integration_failures,
    "synchronization_failures": _synchronization_failures,
    "workflow_escalations": _workflow_escalations,
    "vendor_operational_status": _vendor_operational_status,
    "resilience_posture": _resilience_posture,
    "infrastructure_availability": _infrastructure_availability,
    "continuity_coverage": _continuity_coverage,
    "recovery_readiness": _recovery_readiness,
    "rpo_targets": _rpo_targets,
    "rto_targets": _rto_targets,
    "recovery_test_coverage": _recovery_test_coverage,
    "dependency_health": _dependency_health,
    "service_dependencies": _service_dependencies,
    "operational_service_inventory": _operational_service_inventory,
    "incident_category_inventory": _incident_category_inventory,
    "resilience_gaps": _resilience_gaps,
    "executive_operational_status": _executive_operational_status,
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
