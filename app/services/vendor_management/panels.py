"""Vendor Management panel composition (Phase D.56).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric,
and never any contract content / credential / license key / secret / procurement payload. Vendor-inventory /
dependency panels compose the AUTHORITATIVE Integration Platform (`integration.connectors` / `sync` /
`service`); licensing / renewal / certificate panels compose the Security certificate & secret store
(`security.secrets`) + Insurance licensing; lifecycle / technology panels compose the Observability service
catalog; third-party-risk panels compose Security incidents + Compliance Intelligence. Every compose is
fail-closed (a source outage or missing sub-capability yields an unavailable panel, never an exception) and
self-restricts: a principal lacking the panel's capability is shown a ``restricted`` panel, never its value.
This layer NEVER modifies a vendor, renews a license, terminates a contract, alters an integration, or changes
a subscription — it only composes counts + status.
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


# --- declarative-registry panels ----------------------------------------------------------------------

def _registered_vendors(principal, pdef):
    try:
        by_type = {}
        for v in registry.VENDOR_REGISTRY:
            by_type[v.provider_type] = by_type.get(v.provider_type, 0) + 1
        return _result(pdef, {"count": len(registry.VENDOR_REGISTRY),
                              "vendors": [v.key for v in registry.VENDOR_REGISTRY], "by_provider_type": by_type})
    except Exception:
        return _result(pdef, None, available=False)


def _registered_lifecycle(principal, pdef):
    try:
        by_cat = {}
        for t in registry.TECHNOLOGY_LIFECYCLE_REGISTRY:
            by_cat[t.category] = by_cat.get(t.category, 0) + 1
        return _result(pdef, {"count": len(registry.TECHNOLOGY_LIFECYCLE_REGISTRY),
                              "classes": [t.key for t in registry.TECHNOLOGY_LIFECYCLE_REGISTRY],
                              "by_category": by_cat})
    except Exception:
        return _result(pdef, None, available=False)


# --- Integration Platform providers / connectors / sync ------------------------------------------------

def _vendor_inventory(principal, pdef):
    try:
        from app.services.integration.connectors import list_providers
        by_type = {}
        for r in list_providers():
            t = r.get("provider_type") or "other"
            by_type[t] = by_type.get(t, 0) + 1
        return _result(pdef, {"count": sum(by_type.values()), "by_provider_type": by_type})
    except Exception:
        return _result(pdef, None, available=False)


def _connected_vendors(principal, pdef):
    try:
        from app.services.integration.connectors import list_connectors
        return _result(pdef, {"connected": len(list_connectors(status="connected"))})
    except Exception:
        return _result(pdef, None, available=False)


def _credential_expiry(principal, pdef):
    try:
        from app.services.integration.connectors import list_credentials
        creds = list_credentials()
        with_expiry = sum(1 for c in creds if c.get("expires_at"))
        return _result(pdef, {"credentials": len(creds), "with_expiry": with_expiry})
    except Exception:
        return _result(pdef, None, available=False)


def _integration_dependencies(principal, pdef):
    try:
        from app.services.integration.sync import metrics
        m = metrics(principal)
        return _result(pdef, {"sync_failures": m.get("sync_failures", 0),
                              "connector_errors": m.get("connector_errors", 0),
                              "unresolved_conflicts": m.get("unresolved_conflicts", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _integration_overview(principal, pdef):
    try:
        from app.services.integration.service import overview_metrics
        m = overview_metrics(principal)
        return _result(pdef, {"providers": m.get("providers", 0),
                              "connected_connectors": m.get("connected_connectors", 0),
                              "sync_failures": m.get("sync_failures", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Security certificates / secrets (licensing / renewal) ---------------------------------------------

def _certificates(principal, pdef):
    try:
        from app.services.security.secrets import list_certificates
        by_status = {}
        for c in list_certificates():
            s = c.get("status") or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        return _result(pdef, {"total": sum(by_status.values()), "by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


def _expiring_certificates(principal, pdef):
    try:
        from app.services.security.secrets import metrics
        m = metrics(principal)
        return _result(pdef, {"expired_certificates": m.get("expired_certificates", 0),
                              "overdue_secret_rotations": m.get("overdue_secret_rotations", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _overdue_rotations(principal, pdef):
    try:
        from app.services.security.secrets import overdue_rotations
        return _result(pdef, {"overdue_rotations": len(overdue_rotations(principal))})
    except Exception:
        return _result(pdef, None, available=False)


# --- Insurance producer licenses -----------------------------------------------------------------------

def _producer_licenses(principal, pdef):
    try:
        from app.services.insurance_licensing import list_licenses
        rows = list_licenses(principal)
        by_status = {}
        for r in rows:
            s = r.get("status") or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        return _result(pdef, {"total": len(rows), "by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


def _expiring_licenses(principal, pdef):
    try:
        from datetime import UTC, date, datetime, timedelta

        from app.services.insurance_licensing import list_licenses
        rows = list_licenses(principal)
        today = datetime.now(UTC).date()
        horizon = today + timedelta(days=90)
        expired = approaching = 0
        for r in rows:
            exp = r.get("expiry_date")
            if isinstance(exp, datetime):
                exp = exp.date()
            if not isinstance(exp, date):
                continue
            if exp < today:
                expired += 1
            elif exp <= horizon:
                approaching += 1
        return _result(pdef, {"expired": expired, "approaching": approaching, "horizon_days": 90})
    except Exception:
        return _result(pdef, None, available=False)


# --- Observability service catalog (lifecycle / technology) --------------------------------------------

def _production_systems(principal, pdef):
    try:
        from app.services.observability.catalog import metrics
        m = metrics(principal)
        total = m.get("total_services", 0) or 0
        op = m.get("operational_services", 0)
        pct = round(op / total * 100, 1) if total else 100.0
        return _result(pdef, {"operational_services": op, "total_services": total,
                              "degraded_services": m.get("degraded_services", 0),
                              "operational_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _service_environments(principal, pdef):
    try:
        from app.services.observability.catalog import (
            list_deployment_references,
            list_environment_profiles,
        )
        return _result(pdef, {"environment_profiles": len(list_environment_profiles()),
                              "deployment_references": len(list_deployment_references())})
    except Exception:
        return _result(pdef, None, available=False)


def _service_dependencies(principal, pdef):
    try:
        from app.services.observability.catalog import list_dependencies
        return _result(pdef, {"declared_dependencies": len(list_dependencies())})
    except Exception:
        return _result(pdef, None, available=False)


def _technology_health(principal, pdef):
    try:
        from app.services.observability.catalog import metrics
        m = metrics(principal)
        total = m.get("total_services", 0) or 0
        op = m.get("operational_services", 0)
        pct = round(op / total * 100, 1) if total else 100.0
        return _result(pdef, {"health_percent": pct, "operational_services": op,
                              "degraded_services": m.get("degraded_services", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Security incidents / compliance (third-party risk) ------------------------------------------------

def _security_incident_metrics(principal):
    from app.services.security.incidents import metrics
    return metrics(principal)


def _security_risk(principal, pdef):
    try:
        m = _security_incident_metrics(principal)
        return _result(pdef, {"open_incidents": m.get("open_incidents", 0),
                              "open_findings": m.get("open_findings", 0),
                              "pending_exceptions": m.get("pending_exceptions", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _vendor_findings(principal, pdef):
    try:
        m = _security_incident_metrics(principal)
        return _result(pdef, {"open_findings": m.get("open_findings", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _compliance_risk(principal, pdef):
    try:
        from app.services.compliance_intelligence import supervisory_dashboard
        result = supervisory_dashboard(principal)
        if result is None or not result.get("enabled"):
            return _result(pdef, None, available=False)
        counts = result.get("counts", {})
        return _result(pdef, {"open_reviews": counts.get("open_reviews", 0),
                              "open_exceptions": counts.get("open_exceptions", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- governance score (deterministic, advisory) --------------------------------------------------------

def _vendor_governance_score(principal, pdef):
    try:
        from app.services.integration.connectors import list_connectors
        from app.services.security.secrets import metrics as sec_metrics
        connected = len(list_connectors(status="connected"))
        sm = sec_metrics(principal)
        penalties = sm.get("expired_certificates", 0) + sm.get("overdue_secret_rotations", 0)
        try:
            im = _security_incident_metrics(principal)
            penalties += im.get("open_incidents", 0)
        except Exception:
            pass
        base = 100.0
        score = max(0.0, round(base - penalties, 1))
        return _result(pdef, {"governance_percent": score, "connected_vendors": connected,
                              "penalty_signals": penalties, "advisory_only": True})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "vendor_inventory": _vendor_inventory,
    "registered_vendors": _registered_vendors,
    "connected_vendors": _connected_vendors,
    "certificates": _certificates,
    "producer_licenses": _producer_licenses,
    "credential_expiry": _credential_expiry,
    "registered_lifecycle": _registered_lifecycle,
    "production_systems": _production_systems,
    "service_environments": _service_environments,
    "expiring_certificates": _expiring_certificates,
    "overdue_rotations": _overdue_rotations,
    "expiring_licenses": _expiring_licenses,
    "security_risk": _security_risk,
    "vendor_findings": _vendor_findings,
    "compliance_risk": _compliance_risk,
    "integration_dependencies": _integration_dependencies,
    "integration_overview": _integration_overview,
    "service_dependencies": _service_dependencies,
    "technology_health": _technology_health,
    "vendor_governance_score": _vendor_governance_score,
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
