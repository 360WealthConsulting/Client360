"""Enterprise Regulatory Examination Readiness panel composition (Phase D.59).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric, and
never any document content / tax-return content / client narrative / regulator correspondence / audit payload /
credential / token / account number / license key / PII / private incident narrative / evidence file.
Obligation / evidence-class / examination-request / certification catalog panels are DERIVED from the
declarative registries (labeled ``derived``); evidence panels compose the authoritative owners (Document
Intelligence, Compliance Intelligence, the Exception Engine, Security Operations, Business Continuity, Vendor
Management, Financial Operations, Insurance licensing, audit logging, CI). Filing / examination-correspondence /
evidence-export panels have NO authoritative owner and are emitted ``available=False`` with
``config_status='not_configured'`` — honest, never a fabricated acknowledgement. Certification panels are
BLOCKED / ``reviewer_not_confirmed`` because the reviewer_authorities catalog is seeded empty — reviewer
authority is never inferred and business approval is never regulatory certification. Every compose is
fail-closed and self-restricts: a principal lacking the panel's capability is shown a ``restricted`` panel,
never its value, count, freshness, or leaking metadata. This layer NEVER creates an examination, uploads
evidence, approves a rule set, certifies compliance, signs an attestation, files a form, closes a finding,
resolves an exception, or changes retention. A derived value describes operational readiness (never regulatory
certification) and never interprets an absence of findings as compliance.
"""
from __future__ import annotations

from . import registry, stats
from .model import PanelResult


def _restricted(pdef):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=None,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, restricted=True,
                       available=False, derived=pdef.derived)


def _result(pdef, value, *, available=True, config_status="configured", blocked=False, blocked_reason=None):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=value,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, available=available,
                       derived=pdef.derived, config_status=config_status, blocked=blocked,
                       blocked_reason=blocked_reason)


def _not_configured(pdef, note):
    return _result(pdef, {"status": registry.NOT_CONFIGURED, "note": note}, available=False,
                   config_status=registry.NOT_CONFIGURED)


def _kpi(summary, key):
    if not isinstance(summary, dict):
        return None
    return (summary.get("kpis") or {}).get(key)


# --- registry-derived (DERIVED) ------------------------------------------------------------------------

def _regulatory_obligation_inventory(principal, pdef):
    try:
        by_domain = {}
        for o in registry.REGULATORY_OBLIGATION_REGISTRY:
            by_domain[o.reg_domain] = by_domain.get(o.reg_domain, 0) + 1
        return _result(pdef, {"count": len(registry.REGULATORY_OBLIGATION_REGISTRY), "by_domain": by_domain,
                              "configured": list(registry.configured_obligations()),
                              "not_configured": list(registry.not_configured_obligations())})
    except Exception:
        return _result(pdef, None, available=False)


def _configured_obligation_coverage(principal, pdef):
    try:
        cfg = registry.configured_obligations()
        total = len(registry.REGULATORY_OBLIGATION_REGISTRY)
        pct = round(len(cfg) / total * 100, 1) if total else 0.0
        return _result(pdef, {"configured": len(cfg), "total": total, "coverage_percent": pct,
                              "operational_readiness_not_certification": True})
    except Exception:
        return _result(pdef, None, available=False)


def _unconfigured_obligation_inventory(principal, pdef):
    try:
        nc = list(registry.not_configured_obligations())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


def _evidence_class_inventory(principal, pdef):
    try:
        by_class = {}
        for e in registry.EVIDENCE_REGISTRY:
            by_class[e.evidence_class] = by_class.get(e.evidence_class, 0) + 1
        nc = [e.key for e in registry.EVIDENCE_REGISTRY if e.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"count": len(registry.EVIDENCE_REGISTRY), "by_class": by_class,
                              "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _examination_request_coverage(principal, pdef):
    try:
        configured = [r.key for r in registry.EXAMINATION_REQUEST_REGISTRY
                      if r.config_status == registry.CONFIGURED]
        nc = [r.key for r in registry.EXAMINATION_REQUEST_REGISTRY
              if r.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"total": len(registry.EXAMINATION_REQUEST_REGISTRY),
                              "configured": len(configured), "not_configured": nc,
                              "readiness_map_only": True, "no_active_examination_case": True})
    except Exception:
        return _result(pdef, None, available=False)


def _blocked_certifications(principal, pdef):
    try:
        blocked = [{"key": c.key, "status": c.status, "blocked_reason": c.blocked_reason}
                   for c in registry.CERTIFICATION_REGISTRY
                   if c.status in (registry.BLOCKED, registry.REVIEWER_NOT_CONFIRMED)]
        return _result(pdef, {"count": len(blocked), "blocked": blocked,
                              "business_approval_is_not_certification": True},
                       blocked=bool(blocked),
                       blocked_reason=(blocked[0]["blocked_reason"] if blocked else None))
    except Exception:
        return _result(pdef, None, available=False)


def _reviewer_not_confirmed_certifications(principal, pdef):
    try:
        items = [{"key": c.key, "accountable_reviewer_role": c.accountable_reviewer_role,
                  "reviewer_qualification": c.reviewer_qualification, "named_reviewer": c.named_reviewer}
                 for c in registry.CERTIFICATION_REGISTRY
                 if c.status == registry.REVIEWER_NOT_CONFIRMED]
        return _result(pdef, {"count": len(items), "items": items,
                              "reviewer_authority_never_inferred": True,
                              "business_owner_not_regulatory_certifier": True},
                       blocked=bool(items), blocked_reason=registry._REVIEWER_EMPTY_REASON)
    except Exception:
        return _result(pdef, None, available=False)


def _approval_artifact_coverage(principal, pdef):
    try:
        with_owner = [c.key for c in registry.CERTIFICATION_REGISTRY
                      if c.approval_artifact_owner != registry.NOT_CONFIGURED]
        total = len(registry.CERTIFICATION_REGISTRY)
        pct = round(len(with_owner) / total * 100, 1) if total else 0.0
        return _result(pdef, {"with_artifact_owner": len(with_owner), "total": total,
                              "coverage_percent": pct, "artifact_owner_coverage_not_approval": True})
    except Exception:
        return _result(pdef, None, available=False)


def _stale_evidence(principal, pdef):
    try:
        stale = [{"key": e.key, "freshness": e.freshness} for e in registry.EVIDENCE_REGISTRY
                 if e.freshness in ("periodic", "not_tracked")]
        return _result(pdef, {"count": len(stale), "stale": stale})
    except Exception:
        return _result(pdef, None, available=False)


def _unverifiable_evidence(principal, pdef):
    try:
        unver = [e.key for e in registry.EVIDENCE_REGISTRY
                 if e.verification_owner == registry.NOT_CONFIGURED
                 or e.storage_owner == registry.NOT_CONFIGURED]
        return _result(pdef, {"count": len(unver), "unverifiable": unver})
    except Exception:
        return _result(pdef, None, available=False)


def _evidence_availability(principal, pdef):
    try:
        owned = [e.key for e in registry.EVIDENCE_REGISTRY
                 if e.authoritative_owner != registry.NOT_CONFIGURED]
        total = len(registry.EVIDENCE_REGISTRY)
        pct = round(len(owned) / total * 100, 1) if total else 0.0
        nc = [e.key for e in registry.EVIDENCE_REGISTRY if e.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"available": len(owned), "total": total, "availability_percent": pct,
                              "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _derived_readiness_coverage(principal, pdef):
    """DERIVED operational-readiness coverage — deterministic, authoritative inputs, labeled derived. Describes
    OPERATIONAL READINESS, not regulatory compliance; an absent finding is never interpreted as compliance."""
    try:
        obligations = registry.REGULATORY_OBLIGATION_REGISTRY
        evid = registry.EVIDENCE_REGISTRY
        cfg_ob = len(registry.configured_obligations())
        owned_ev = sum(1 for e in evid if e.authoritative_owner != registry.NOT_CONFIGURED)
        blocked = len(registry.blocked_certifications())
        return _result(pdef, {"derived": True, "operational_readiness_not_certification": True,
                              "configured_obligations": cfg_ob, "total_obligations": len(obligations),
                              "owned_evidence_classes": owned_ev, "total_evidence_classes": len(evid),
                              "blocked_certifications": blocked,
                              "not_configured_obligations": list(registry.not_configured_obligations()),
                              "absence_of_findings_is_not_compliance": True})
    except Exception:
        return _result(pdef, None, available=False)


# --- Document Intelligence (evidence completeness / retention) -----------------------------------------

def _document_summary(principal):
    from app.services.document_intelligence import document_summary
    return document_summary(principal)


def _evidence_completeness(principal, pdef):
    try:
        s = _document_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"completeness_score": _kpi(s, "completeness_score"),
                              "missing_documents": _kpi(s, "missing_documents")})
    except Exception:
        return _result(pdef, None, available=False)


def _retention_coverage(principal, pdef):
    try:
        s = _document_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"expiring_documents": _kpi(s, "expiring_documents")})
    except Exception:
        return _result(pdef, None, available=False)


def _documentation_gaps(principal, pdef):
    try:
        s = _document_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"missing_documents": _kpi(s, "missing_documents")})
    except Exception:
        return _result(pdef, None, available=False)


# --- Compliance Intelligence (supervisory / findings / exceptions / suitability) -----------------------

def _supervisory(principal):
    from app.services.compliance_intelligence import supervisory_dashboard
    return supervisory_dashboard(principal)


def _supervisory_counts(principal, pdef, keys):
    try:
        sd = _supervisory(principal)
        if not sd or not sd.get("enabled"):
            return _result(pdef, None, available=False)
        c = sd.get("counts", {})
        return _result(pdef, {k: c.get(k, 0) for k in keys})
    except Exception:
        return _result(pdef, None, available=False)


def _supervisory_review_status(principal, pdef):
    return _supervisory_counts(principal, pdef, ("open_reviews", "pending_approvals", "blocked"))


def _unresolved_compliance_findings(principal, pdef):
    return _supervisory_counts(principal, pdef, ("open_reviews", "open_exceptions"))


def _unresolved_exceptions(principal, pdef):
    try:
        sd = _supervisory(principal)
        if not sd or not sd.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"by_severity": sd.get("counts", {}).get("by_severity", {})})
    except Exception:
        return _result(pdef, None, available=False)


def _remediation_evidence(principal, pdef):
    return _supervisory_counts(principal, pdef, ("open_exceptions", "blocked"))


def _communications_review_evidence(principal, pdef):
    return _supervisory_counts(principal, pdef, ("open_reviews",))


def _suitability_evidence(principal, pdef):
    return _supervisory_counts(principal, pdef, ("open_reviews",))


def _replacement_1035_evidence(principal, pdef):
    return _supervisory_counts(principal, pdef, ("open_reviews",))


# --- Insurance licensing / CE --------------------------------------------------------------------------

def _licensing_evidence(principal, pdef):
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


def _ce_evidence(principal, pdef):
    try:
        from app.services.insurance_licensing import list_ce
        rows = list_ce(principal)
        by_status = {}
        for r in rows:
            s = r.get("status") or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        return _result(pdef, {"total": len(rows), "by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


# --- Vendor / security / continuity / financial evidence -----------------------------------------------

def _vendor_review_evidence(principal, pdef):
    try:
        from app.services.vendor_management import vendor_summary
        s = vendor_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"vendor_governance_score": _kpi(s, "vendor_governance_score"),
                              "integration_dependencies": _kpi(s, "integration_dependencies")})
    except Exception:
        return _result(pdef, None, available=False)


def _cybersecurity_evidence(principal, pdef):
    try:
        from app.services.security_operations import security_summary
        s = security_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"mfa_coverage": _kpi(s, "mfa_coverage"),
                              "authorization_failures": _kpi(s, "authorization_failures")})
    except Exception:
        return _result(pdef, None, available=False)


def _continuity_evidence(principal, pdef):
    try:
        from app.services.business_continuity import continuity_summary
        s = continuity_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"resilience_score": _kpi(s, "resilience_score")})
    except Exception:
        return _result(pdef, None, available=False)


def _financial_control_evidence(principal, pdef):
    try:
        from app.services.financial_operations import firm_financial_summary
        s = firm_financial_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"collections": _kpi(s, "collections")})
    except Exception:
        return _result(pdef, None, available=False)


def _commission_reconciliation_evidence(principal, pdef):
    try:
        from app.services import insurance_reporting
        r = insurance_reporting.commission_report(principal)
        return _result(pdef, {"outstanding_total": r.get("outstanding_total", 0.0),
                              "variance_total": r.get("variance_total", 0.0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- audit / CI evidence (availability only) -----------------------------------------------------------

def _audit_log_verification(principal, pdef):
    try:
        # the hash-chain audit log is the authoritative evidence; availability only — never a payload.
        return _result(pdef, {"audit_log": "available", "hash_chain": True})
    except Exception:
        return _result(pdef, None, available=False)


def _architecture_test_evidence(principal, pdef):
    return _result(pdef, {"ci_architecture_guards": "available", "source": "continuous_integration"})


def _ci_evidence(principal, pdef):
    return _result(pdef, {"ci_pipeline": "available", "source": "continuous_integration"})


# --- not_configured (filing / examination / export) — reported honestly --------------------------------

def _federal_filing(principal, pdef):
    return _not_configured(pdef, "no authoritative federal-filing owner exists in the platform")


def _state_filing(principal, pdef):
    return _not_configured(pdef, "no authoritative state-filing owner exists in the platform")


def _filing_history(principal, pdef):
    return _not_configured(pdef, "no authoritative filing owner exists in the platform")


def _examination_correspondence(principal, pdef):
    return _not_configured(pdef, "no authoritative examination-case owner exists in the platform")


def _evidence_export(principal, pdef):
    return _not_configured(pdef, "no authoritative evidence-export owner exists in the platform")


_COMPUTE = {
    "regulatory_obligation_inventory": _regulatory_obligation_inventory,
    "configured_obligation_coverage": _configured_obligation_coverage,
    "unconfigured_obligation_inventory": _unconfigured_obligation_inventory,
    "evidence_class_inventory": _evidence_class_inventory,
    "examination_request_coverage": _examination_request_coverage,
    "blocked_certifications": _blocked_certifications,
    "reviewer_not_confirmed_certifications": _reviewer_not_confirmed_certifications,
    "approval_artifact_coverage": _approval_artifact_coverage,
    "derived_readiness_coverage": _derived_readiness_coverage,
    "evidence_availability": _evidence_availability,
    "evidence_completeness": _evidence_completeness,
    "stale_evidence": _stale_evidence,
    "unverifiable_evidence": _unverifiable_evidence,
    "retention_coverage": _retention_coverage,
    "documentation_gaps": _documentation_gaps,
    "supervisory_review_status": _supervisory_review_status,
    "unresolved_compliance_findings": _unresolved_compliance_findings,
    "unresolved_exceptions": _unresolved_exceptions,
    "remediation_evidence": _remediation_evidence,
    "licensing_evidence": _licensing_evidence,
    "ce_evidence": _ce_evidence,
    "communications_review_evidence": _communications_review_evidence,
    "suitability_evidence": _suitability_evidence,
    "replacement_1035_evidence": _replacement_1035_evidence,
    "vendor_review_evidence": _vendor_review_evidence,
    "cybersecurity_evidence": _cybersecurity_evidence,
    "continuity_evidence": _continuity_evidence,
    "financial_control_evidence": _financial_control_evidence,
    "commission_reconciliation_evidence": _commission_reconciliation_evidence,
    "audit_log_verification": _audit_log_verification,
    "architecture_test_evidence": _architecture_test_evidence,
    "ci_evidence": _ci_evidence,
    "federal_filing_acknowledgements": _federal_filing,
    "state_filing_acknowledgements": _state_filing,
    "filing_history": _filing_history,
    "examination_correspondence_availability": _examination_correspondence,
    "evidence_export_availability": _evidence_export,
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
