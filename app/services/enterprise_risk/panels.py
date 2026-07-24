"""Enterprise Risk Management panel composition (Phase D.58).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric,
and never any client-sensitive evidence / audit payload / security detail / credential / token / bank
information / tax-return content / document content / private incident narrative. Compliance / exception /
remediation panels compose Compliance Intelligence over the AUTHORITATIVE Exception Engine; security panels
compose Security incidents + Security Operations; data panels compose Data Governance; integration panels
compose the Integration Platform; resilience panels compose Business Continuity; vendor panels compose Vendor
Management; financial panels compose Financial Operations + the commission ledger; documentation / licensing
panels compose Document Intelligence + Insurance licensing; catalog / posture panels are DERIVED from the
declarative registries (labeled ``derived``). Every compose is fail-closed (a source outage, gated-off source,
or missing sub-capability yields an unavailable panel, never an exception) and self-restricts: a principal
lacking the panel's capability is shown a ``restricted`` panel, never its value, count, or leaking metadata.
This layer NEVER creates a risk, changes a rating, closes a finding, approves a control, accepts an exception,
acknowledges an incident, assigns remediation, alters evidence, certifies compliance, or modifies policy — it
only composes counts, status, severity distributions, and coverage summaries. It NEVER fabricates a composite
risk score.
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


# --- declarative-registry panels (DERIVED) -------------------------------------------------------------

def _risk_domain_inventory(principal, pdef):
    try:
        by_cat = {}
        for r in registry.ENTERPRISE_RISK_REGISTRY:
            by_cat[r.risk_category] = by_cat.get(r.risk_category, 0) + 1
        return _result(pdef, {"count": len(registry.ENTERPRISE_RISK_REGISTRY), "by_category": by_cat,
                              "configured": list(registry.configured_domains()),
                              "not_configured": list(registry.not_configured_domains())})
    except Exception:
        return _result(pdef, None, available=False)


def _control_coverage(principal, pdef):
    try:
        owned = [c.key for c in registry.CONTROL_REGISTRY
                 if c.authoritative_owner != registry.NOT_CONFIGURED]
        not_owned = [c.key for c in registry.CONTROL_REGISTRY
                     if c.authoritative_owner == registry.NOT_CONFIGURED]
        # control TESTING is not owned platform-wide — every test_owner is not_configured.
        tested = [c.key for c in registry.CONTROL_REGISTRY if c.test_owner != registry.NOT_CONFIGURED]
        return _result(pdef, {"total": len(registry.CONTROL_REGISTRY), "with_owner": len(owned),
                              "not_configured": not_owned, "with_test_owner": len(tested),
                              "control_testing": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


def _assurance_evidence_coverage(principal, pdef):
    try:
        configured = [a.key for a in registry.ASSURANCE_REGISTRY
                      if a.config_status == registry.CONFIGURED]
        not_configured = [a.key for a in registry.ASSURANCE_REGISTRY
                          if a.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"total": len(registry.ASSURANCE_REGISTRY), "configured": len(configured),
                              "not_configured": not_configured,
                              "sources": [a.key for a in registry.ASSURANCE_REGISTRY]})
    except Exception:
        return _result(pdef, None, available=False)


def _enterprise_risk_posture(principal, pdef):
    """DERIVED coverage summary — deterministic, authoritative inputs, labeled derived. NEVER a certified
    composite risk score or regulatory rating."""
    try:
        configured = list(registry.configured_domains())
        not_configured = list(registry.not_configured_domains())
        open_signals = {}
        # authoritative open-finding counts (each self-gated; fail-closed to omitted).
        try:
            from app.services.compliance_intelligence import supervisory_dashboard
            sd = supervisory_dashboard(principal)
            if sd and sd.get("enabled"):
                counts = sd.get("counts", {})
                open_signals["compliance"] = (counts.get("open_reviews", 0)
                                              + counts.get("open_exceptions", 0))
        except Exception:
            pass
        try:
            from app.services.security.incidents import metrics as sec_metrics
            m = sec_metrics(principal)
            open_signals["security"] = m.get("open_incidents", 0) + m.get("open_findings", 0)
        except Exception:
            pass
        return _result(pdef, {"derived": True, "not_a_certified_rating": True,
                              "configured_domains": len(configured),
                              "not_configured_domains": len(not_configured),
                              "not_configured": not_configured,
                              "open_signal_counts": open_signals})
    except Exception:
        return _result(pdef, None, available=False)


# --- Compliance Intelligence / Exception Engine --------------------------------------------------------

def _supervisory(principal):
    from app.services.compliance_intelligence import supervisory_dashboard
    return supervisory_dashboard(principal)


def _open_compliance_findings(principal, pdef):
    try:
        sd = _supervisory(principal)
        if not sd or not sd.get("enabled"):
            return _result(pdef, None, available=False)
        c = sd.get("counts", {})
        return _result(pdef, {"open_reviews": c.get("open_reviews", 0),
                              "open_exceptions": c.get("open_exceptions", 0),
                              "pending_approvals": c.get("pending_approvals", 0),
                              "blocked": c.get("blocked", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _compliance_exception_severity(principal, pdef):
    try:
        sd = _supervisory(principal)
        if not sd or not sd.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"by_severity": sd.get("counts", {}).get("by_severity", {})})
    except Exception:
        return _result(pdef, None, available=False)


def _unresolved_remediation_workload(principal, pdef):
    try:
        sd = _supervisory(principal)
        if not sd or not sd.get("enabled"):
            return _result(pdef, None, available=False)
        c = sd.get("counts", {})
        return _result(pdef, {"open_exceptions": c.get("open_exceptions", 0),
                              "blocked": c.get("blocked", 0),
                              "pending_approvals": c.get("pending_approvals", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _operational_incidents(principal, pdef):
    try:
        sd = _supervisory(principal)
        if not sd or not sd.get("enabled"):
            return _result(pdef, None, available=False)
        c = sd.get("counts", {})
        return _result(pdef, {"blocked": c.get("blocked", 0),
                              "pending_approvals": c.get("pending_approvals", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Security incidents / operations -------------------------------------------------------------------

def _security_incidents(principal, pdef):
    try:
        from app.services.security.incidents import metrics
        m = metrics(principal)
        return _result(pdef, {"open_incidents": m.get("open_incidents", 0),
                              "open_findings": m.get("open_findings", 0),
                              "pending_exceptions": m.get("pending_exceptions", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _identity_access_warnings(principal, pdef):
    try:
        from app.services.security_operations import security_summary
        s = security_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"authorization_failures": _kpi(s, "authorization_failures"),
                              "mfa_coverage": _kpi(s, "mfa_coverage")})
    except Exception:
        return _result(pdef, None, available=False)


# --- Data Governance -----------------------------------------------------------------------------------

def _data_governance_summary(principal):
    from app.services.data_governance import governance_summary
    return governance_summary(principal)


def _data_quality_exceptions(principal, pdef):
    try:
        s = _data_governance_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"validation_metrics": _kpi(s, "validation_metrics"),
                              "data_quality_score": _kpi(s, "data_quality_score")})
    except Exception:
        return _result(pdef, None, available=False)


def _duplicate_lineage_issues(principal, pdef):
    try:
        s = _data_governance_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"duplicate_candidates": _kpi(s, "duplicate_candidates")})
    except Exception:
        return _result(pdef, None, available=False)


# --- Integration Platform ------------------------------------------------------------------------------

def _integration_failures(principal, pdef):
    try:
        from app.services.integration.service import overview_metrics
        m = overview_metrics(principal)
        return _result(pdef, {"providers": m.get("providers", 0),
                              "connected_connectors": m.get("connected_connectors", 0),
                              "sync_failures": m.get("sync_failures", 0),
                              "connector_errors": m.get("connector_errors", 0)})
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


# --- Vendor Management ---------------------------------------------------------------------------------

def _vendor_summary(principal):
    from app.services.vendor_management import vendor_summary
    return vendor_summary(principal)


def _vendor_risk_findings(principal, pdef):
    try:
        s = _vendor_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"vendor_governance_score": _kpi(s, "vendor_governance_score"),
                              "integration_dependencies": _kpi(s, "integration_dependencies")})
    except Exception:
        return _result(pdef, None, available=False)


def _expiring_technology_certificates(principal, pdef):
    try:
        s = _vendor_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"expiring_certificates": _kpi(s, "expiring_certificates")})
    except Exception:
        return _result(pdef, None, available=False)


# --- Business Continuity -------------------------------------------------------------------------------

def _continuity_summary(principal):
    from app.services.business_continuity import continuity_summary
    return continuity_summary(principal)


def _continuity_gaps(principal, pdef):
    try:
        s = _continuity_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"resilience_score": _kpi(s, "resilience_score"),
                              "service_incidents": _kpi(s, "service_incidents")})
    except Exception:
        return _result(pdef, None, available=False)


def _backup_recovery_config(principal, pdef):
    try:
        s = _continuity_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        cov = _kpi(s, "backup_coverage")
        # backup / restore / DR have no authoritative owner — reported honestly.
        return _result(pdef, {"backup_coverage": cov, "backup_restore_dr": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


# --- Automation Orchestration --------------------------------------------------------------------------

def _automation_summary(principal):
    from app.services.automation_orchestration import automation_summary
    return automation_summary(principal)


def _workflow_escalations(principal, pdef):
    try:
        s = _automation_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"open_escalations": _kpi(s, "open_escalations"),
                              "failed_runs": _kpi(s, "failed_runs")})
    except Exception:
        return _result(pdef, None, available=False)


def _overdue_approvals(principal, pdef):
    try:
        s = _automation_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"workflow_pending_approvals": _kpi(s, "workflow_pending_approvals"),
                              "dedicated_approval_owner": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


# --- Document Intelligence / Insurance licensing -------------------------------------------------------

def _documentation_gaps(principal, pdef):
    try:
        from app.services.document_intelligence import document_summary
        s = document_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"missing_documents": _kpi(s, "missing_documents"),
                              "expiring_documents": _kpi(s, "expiring_documents")})
    except Exception:
        return _result(pdef, None, available=False)


def _licensing_gaps(principal, pdef):
    try:
        from datetime import UTC, date, datetime, timedelta

        from app.services.insurance_licensing import list_licenses
        rows = list_licenses(principal)
        today = datetime.now(UTC).date()
        horizon = today + timedelta(days=60)
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
        return _result(pdef, {"expired": expired, "approaching": approaching, "horizon_days": 60})
    except Exception:
        return _result(pdef, None, available=False)


# --- Financial Operations / commission ledger ----------------------------------------------------------

def _financial_reconciliation_status(principal, pdef):
    try:
        from app.services.financial_operations import firm_financial_summary
        s = firm_financial_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"collections": _kpi(s, "collections"),
                              "commission_revenue": _kpi(s, "commission_revenue")})
    except Exception:
        return _result(pdef, None, available=False)


def _commission_exceptions(principal, pdef):
    try:
        from app.services import insurance_reporting
        r = insurance_reporting.commission_report(principal)
        return _result(pdef, {"outstanding_total": r.get("outstanding_total", 0.0),
                              "variance_total": r.get("variance_total", 0.0), "currency": "USD"})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "risk_domain_inventory": _risk_domain_inventory,
    "enterprise_risk_posture": _enterprise_risk_posture,
    "open_compliance_findings": _open_compliance_findings,
    "compliance_exception_severity": _compliance_exception_severity,
    "unresolved_remediation_workload": _unresolved_remediation_workload,
    "operational_incidents": _operational_incidents,
    "security_incidents": _security_incidents,
    "identity_access_warnings": _identity_access_warnings,
    "data_quality_exceptions": _data_quality_exceptions,
    "duplicate_lineage_issues": _duplicate_lineage_issues,
    "integration_failures": _integration_failures,
    "synchronization_failures": _synchronization_failures,
    "vendor_risk_findings": _vendor_risk_findings,
    "expiring_technology_certificates": _expiring_technology_certificates,
    "continuity_gaps": _continuity_gaps,
    "backup_recovery_config": _backup_recovery_config,
    "workflow_escalations": _workflow_escalations,
    "overdue_approvals": _overdue_approvals,
    "documentation_gaps": _documentation_gaps,
    "licensing_gaps": _licensing_gaps,
    "financial_reconciliation_status": _financial_reconciliation_status,
    "commission_exceptions": _commission_exceptions,
    "control_coverage": _control_coverage,
    "assurance_evidence_coverage": _assurance_evidence_coverage,
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
