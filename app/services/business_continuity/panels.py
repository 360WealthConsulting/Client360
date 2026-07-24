"""Business Continuity panel composition (Phase D.55).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric,
and never any infrastructure payload. Infrastructure / health / incident / maintenance / alert panels compose
the AUTHORITATIVE Observability domain (`service` / `catalog` / `health` / `incidents` / `alerts`); runtime
panels compose the Runtime engine (`runtime.service` / `coordination` / `consumption`); scheduled-job panels
compose the Automation scheduler; notification panels compose Communications. Backup / restore / DR domains
have NO authoritative owner in the platform today — those panels report ``not_configured`` honestly (never a
fabricated backup status), mirroring the D.50/OCR precedent. Every compose is fail-closed (a source outage
yields an unavailable panel, never an exception) and self-restricts: a principal lacking the panel's
capability is shown a ``restricted`` panel, never its value. This layer NEVER starts a backup, restores
data, acknowledges an incident, changes monitoring, alters runtime, or modifies infrastructure — it only
composes counts + status.
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


# --- backup / restore (declarative — no authoritative owner exists) ------------------------------------

def _backup_status_value(status_key):
    """No authoritative backup/restore owner is configured; report the declared domain status honestly."""
    return {"status": "not_configured", "note": "no authoritative backup/restore owner is configured",
            "domain": status_key}


def _last_successful_backup(principal, pdef):
    return _result(pdef, _backup_status_value("backup"))


def _failed_backups(principal, pdef):
    return _result(pdef, _backup_status_value("backup"))


def _restore_test_status(principal, pdef):
    return _result(pdef, _backup_status_value("restore"))


def _backup_coverage(principal, pdef):
    try:
        with_backup = sum(1 for a in registry.RECOVERY_REGISTRY if a.backup_owner != "not_configured")
        return _result(pdef, {"assets": len(registry.RECOVERY_REGISTRY), "with_backup_owner": with_backup})
    except Exception:
        return _result(pdef, None, available=False)


# --- declarative-registry panels ----------------------------------------------------------------------

def _recovery_assets(principal, pdef):
    try:
        return _result(pdef, {"count": len(registry.RECOVERY_REGISTRY),
                              "assets": [a.key for a in registry.RECOVERY_REGISTRY]})
    except Exception:
        return _result(pdef, None, available=False)


def _rpo_targets(principal, pdef):
    try:
        return _result(pdef, {"by_asset": {a.key: a.rpo for a in registry.RECOVERY_REGISTRY}})
    except Exception:
        return _result(pdef, None, available=False)


def _rto_targets(principal, pdef):
    try:
        return _result(pdef, {"by_asset": {a.key: a.rto for a in registry.RECOVERY_REGISTRY}})
    except Exception:
        return _result(pdef, None, available=False)


def _recovery_documentation(principal, pdef):
    try:
        return _result(pdef, {"resilience_domains": len(registry.RESILIENCE_REGISTRY),
                              "recovery_assets": len(registry.RECOVERY_REGISTRY)})
    except Exception:
        return _result(pdef, None, available=False)


def _resilience_domains(principal, pdef):
    try:
        by_owner = {}
        for r in registry.RESILIENCE_REGISTRY:
            by_owner[r.authoritative_owner] = by_owner.get(r.authoritative_owner, 0) + 1
        return _result(pdef, {"count": len(registry.RESILIENCE_REGISTRY),
                              "domains": [r.key for r in registry.RESILIENCE_REGISTRY],
                              "by_owner": by_owner})
    except Exception:
        return _result(pdef, None, available=False)


# --- Observability (infrastructure / health / incidents / alerts / overview) ---------------------------

def _observability_overview(principal):
    from app.services.observability.service import overview_metrics
    return overview_metrics(principal)


def _infrastructure_availability(principal, pdef):
    try:
        from app.services.observability.catalog import metrics
        m = metrics(principal)
        total = m.get("total_services", 0) or 0
        op = m.get("operational_services", 0)
        pct = round(op / total * 100, 1) if total else 100.0
        return _result(pdef, {"operational_services": op, "total_services": total,
                              "degraded_services": m.get("degraded_services", 0),
                              "availability_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _health_checks(principal, pdef):
    try:
        from app.services.observability.health import metrics
        m = metrics(principal)
        return _result(pdef, {"failed_health_checks": m.get("failed_health_checks", 0),
                              "diagnostic_failures": m.get("diagnostic_failures", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _service_incidents(principal, pdef):
    try:
        from app.services.observability.incidents import metrics
        m = metrics(principal)
        return _result(pdef, {"reliability_incidents": m.get("reliability_incidents", 0),
                              "reliability_findings": m.get("reliability_findings", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _scheduled_maintenance(principal, pdef):
    try:
        from app.services.observability.alerts import list_maintenance_windows, metrics
        windows = list_maintenance_windows()
        active = metrics(principal).get("active_maintenance_windows", 0)
        return _result(pdef, {"windows": len(windows), "active": active})
    except Exception:
        return _result(pdef, None, available=False)


def _open_alerts(principal, pdef):
    try:
        from app.services.observability.alerts import metrics
        return _result(pdef, {"open_alerts": metrics(principal).get("open_alerts", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _observability_overview_panel(principal, pdef):
    try:
        m = _observability_overview(principal)
        return _result(pdef, {"operational_services": m.get("operational_services", 0),
                              "failed_health_checks": m.get("failed_health_checks", 0),
                              "open_alerts": m.get("open_alerts", 0),
                              "reliability_incidents": m.get("reliability_incidents", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _resilience_score(principal, pdef):
    try:
        m = _observability_overview(principal)
        total = m.get("total_services", 0) or 0
        op = m.get("operational_services", 0)
        penalties = (m.get("degraded_services", 0) + m.get("failed_health_checks", 0)
                     + m.get("reliability_incidents", 0) + m.get("open_alerts", 0))
        base = (op / total * 100) if total else 100.0
        score = max(0.0, round(base - penalties, 1))
        return _result(pdef, {"readiness_percent": score, "operational_services": op,
                              "total_services": total, "penalty_signals": penalties, "advisory_only": True})
    except Exception:
        return _result(pdef, None, available=False)


# --- Runtime (readiness / cluster / adoption) ----------------------------------------------------------

def _runtime_health(principal, pdef):
    try:
        from app.services.runtime.service import overview_metrics
        m = overview_metrics(principal)
        return _result(pdef, {"validation_ok": m.get("validation_ok"), "issue_count": m.get("issue_count", 0),
                              "evaluations": m.get("evaluations", 0),
                              "cache_hit_ratio": m.get("cache_hit_ratio")})
    except Exception:
        return _result(pdef, None, available=False)


def _cluster_health(principal, pdef):
    try:
        from app.services.runtime.coordination import cluster_state
        c = cluster_state()
        return _result(pdef, {"active": c.get("active", 0), "stale": c.get("stale", 0),
                              "total": c.get("total", 0), "converged": c.get("converged", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _runtime_adoption(principal, pdef):
    try:
        from app.services.runtime.consumption import adoption_stats
        a = adoption_stats()
        return _result(pdef, {"runtime_adoption_pct": a.get("runtime_adoption_pct"),
                              "legacy_fallbacks": a.get("legacy_fallbacks", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Automation (scheduled jobs) -----------------------------------------------------------------------

def _scheduled_jobs(principal, pdef):
    try:
        from app.services.automation.service import metrics
        m = metrics(principal)
        return _result(pdef, {"jobs": m.get("jobs", 0), "running": m.get("running", 0),
                              "failed": m.get("failed", 0), "succeeded": m.get("succeeded", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Communications (notifications) --------------------------------------------------------------------

def _notification_health(principal, pdef):
    try:
        from app.services.communications.service import metrics
        m = metrics(principal)
        return _result(pdef, {"sent": m.get("sent", 0), "messages": m.get("messages", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _notification_activity(principal, pdef):
    try:
        from app.services.communications.service import metrics
        m = metrics(principal)
        return _result(pdef, {"open_conversations": m.get("open_conversations", 0)})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "last_successful_backup": _last_successful_backup,
    "failed_backups": _failed_backups,
    "backup_coverage": _backup_coverage,
    "recovery_assets": _recovery_assets,
    "rpo_targets": _rpo_targets,
    "rto_targets": _rto_targets,
    "restore_test_status": _restore_test_status,
    "recovery_documentation": _recovery_documentation,
    "resilience_domains": _resilience_domains,
    "infrastructure_availability": _infrastructure_availability,
    "health_checks": _health_checks,
    "service_incidents": _service_incidents,
    "runtime_health": _runtime_health,
    "cluster_health": _cluster_health,
    "runtime_adoption": _runtime_adoption,
    "scheduled_maintenance": _scheduled_maintenance,
    "open_alerts": _open_alerts,
    "scheduled_jobs": _scheduled_jobs,
    "notification_health": _notification_health,
    "notification_activity": _notification_activity,
    "resilience_score": _resilience_score,
    "observability_overview": _observability_overview_panel,
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
