"""Enterprise Data Governance Intelligence panel composition (Phase D.66).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second catalog /
metadata repository / metric, and never any sensitive data value / client PII / credential / secret / token /
confidential metadata / internal governance note / quality-rule internal. Data-domain / element / rule panels
compose the Governance catalog (`list_domains`, `list_elements`, `list_rules`, `list_survivorship_rules`);
lineage panels compose Governance MDM (`list_candidates`, `person_lineage`); quality panels compose Governance
Quality (`metrics`) + the catalog; retention panels compose Governance Retention (`list_retention_assignments`,
`metrics`). External data catalog, business glossary, data classification, automated column-level lineage, data
contracts, DQ scorecards / SLAs, retention-policy catalog, and DPIA have no authoritative owner and are emitted
``available=False`` with ``config_status='not_configured'`` — honest, never fabricated lineage, source systems,
stewardship assignments, quality scores, retention policies, metadata, catalog entries, or data owners. Every
compose is fail-closed and self-restricts. This layer NEVER transforms data, synchronizes systems, mutates
metadata, repairs data, creates lineage, assigns a steward, executes a quality rule, or enforces retention. A
derived value describes GOVERNANCE READINESS / COVERAGE, never a repaired dataset, a created lineage edge, an
assigned steward, an executed quality rule, or an enforced retention decision — **a registered rule is not an
executed check, a steward assignment is not a governance guarantee, a lineage record is not a complete lineage,
and coverage is not certification.**
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


def _not_configured(pdef, note):
    return _result(pdef, {"status": registry.NOT_CONFIGURED, "note": note}, available=False,
                   config_status=registry.NOT_CONFIGURED)


# --- read helpers (read-only, guarded) -----------------------------------------------------------------

def _domains():
    from app.services.governance import catalog
    return catalog.list_domains()


def _elements():
    from app.services.governance import catalog
    return catalog.list_elements()


def _rules():
    from app.services.governance import catalog
    return catalog.list_rules()


def _quality_metrics(principal):
    from app.services.governance import quality
    return quality.metrics(principal)


def _retention_metrics(principal):
    from app.services.governance import retention
    return retention.metrics(principal)


def _pct(n, d):
    return round(n / d * 100, 1) if d else 0.0


# --- data domain panels --------------------------------------------------------------------------------

def _data_inventory(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"data_domain_entries": len(registry.DATA_DOMAIN_REGISTRY),
                              "lineage_entries": len(registry.DATA_LINEAGE_REGISTRY),
                              "stewardship_entries": len(registry.DATA_STEWARDSHIP_REGISTRY),
                              "quality_entries": len(registry.DATA_QUALITY_REGISTRY),
                              "retention_entries": len(registry.DATA_RETENTION_REGISTRY),
                              "not_configured": len(nc), "registered_rule_is_not_an_executed_check": True})
    except Exception:
        return _result(pdef, None, available=False)


def _data_domain_coverage(principal, pdef):
    try:
        return _result(pdef, {"data_domains": len(_domains()), "confidential_metadata_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _data_element_coverage(principal, pdef):
    try:
        return _result(pdef, {"data_elements": len(_elements())})
    except Exception:
        return _result(pdef, None, available=False)


def _source_of_truth_coverage(principal, pdef):
    try:
        domains = _domains()
        elements = _elements()
        rules = _rules()
        return _result(pdef, {"derived": True, "data_domains": len(domains),
                              "cataloged_elements": len(elements), "governance_rules": len(rules),
                              "coverage_is_not_a_certified_source_of_truth": True})
    except Exception:
        return _result(pdef, None, available=False)


def _governance_gaps(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


# --- lineage panels ------------------------------------------------------------------------------------

def _lineage_coverage(principal, pdef):
    try:
        configured = sum(1 for e in registry.DATA_LINEAGE_REGISTRY if e.config_status == registry.CONFIGURED)
        total = len(registry.DATA_LINEAGE_REGISTRY)
        return _result(pdef, {"derived": True, "configured_lineage_domains": configured, "total": total,
                              "coverage_percent": _pct(configured, total),
                              "lineage_record_is_not_complete_lineage": True})
    except Exception:
        return _result(pdef, None, available=False)


def _record_lineage_availability(principal, pdef):
    try:
        from app.services.governance.mdm import person_lineage
        return _result(pdef, {"record_scoped_lineage": "configured", "owner_present": callable(person_lineage),
                              "firm_wide_provenance_never_aggregated_or_inferred": True})
    except Exception:
        return _result(pdef, None, available=False)


def _mdm_candidate_coverage(principal, pdef):
    try:
        from app.services.governance.mdm import list_candidates
        q = list_candidates(principal, page=1, page_size=1)
        return _result(pdef, {"mdm_candidates": q.get("total", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _automated_lineage_availability(principal, pdef):
    return _not_configured(pdef, "no automated column-level lineage / data-sharing-contract owner exists; "
                                 "lineage is never inferred")


# --- stewardship panels --------------------------------------------------------------------------------

def _stewardship_coverage(principal, pdef):
    try:
        domains = _domains()
        stewarded = sum(1 for d in domains if d.get("steward_user_id"))
        return _result(pdef, {"derived": True, "stewarded": stewarded, "total": len(domains),
                              "coverage_percent": _pct(stewarded, len(domains)),
                              "steward_assignment_is_not_a_governance_guarantee": True})
    except Exception:
        return _result(pdef, None, available=False)


def _stewarded_domains(principal, pdef):
    try:
        domains = _domains()
        stewarded = sum(1 for d in domains if d.get("steward_user_id"))
        return _result(pdef, {"stewarded": stewarded, "total": len(domains),
                              "steward_identity_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _remediation_case_coverage(principal, pdef):
    try:
        from app.services.governance.retention import list_cases
        return _result(pdef, {"remediation_cases": len(list_cases())})
    except Exception:
        return _result(pdef, None, available=False)


def _stewardship_workflow_availability(principal, pdef):
    return _not_configured(pdef, "no formal stewardship-assignment workflow / data-product ownership owner "
                                 "exists in the platform")


# --- quality panels ------------------------------------------------------------------------------------

def _quality_rule_coverage(principal, pdef):
    try:
        return _result(pdef, {"quality_rules": len(_rules()),
                              "registered_rule_is_not_an_executed_check": True})
    except Exception:
        return _result(pdef, None, available=False)


def _quality_finding_summary(principal, pdef):
    try:
        m = _quality_metrics(principal)
        return _result(pdef, {"open": m.get("open"), "critical_open": m.get("critical_open"),
                              "finding_detail_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _critical_finding_summary(principal, pdef):
    try:
        m = _quality_metrics(principal)
        return _result(pdef, {"critical_open": m.get("critical_open")})
    except Exception:
        return _result(pdef, None, available=False)


def _quality_coverage(principal, pdef):
    try:
        rules = _rules()
        domains = _domains()
        m = _quality_metrics(principal)
        return _result(pdef, {"derived": True, "quality_rules": len(rules), "data_domains": len(domains),
                              "open_findings": m.get("open"),
                              "coverage_is_not_a_certified_quality_score": True})
    except Exception:
        return _result(pdef, None, available=False)


def _quality_scorecard_availability(principal, pdef):
    return _not_configured(pdef, "no data-quality scorecard / SLA owner exists; a quality score is never "
                                 "fabricated")


# --- retention panels ----------------------------------------------------------------------------------

def _retention_assignment_coverage(principal, pdef):
    try:
        from app.services.governance.retention import list_retention_assignments
        return _result(pdef, {"retention_assignments": len(list_retention_assignments())})
    except Exception:
        return _result(pdef, None, available=False)


def _legal_hold_summary(principal, pdef):
    try:
        m = _retention_metrics(principal)
        return _result(pdef, {"active_legal_holds": m.get("active_legal_holds"),
                              "hold_reason_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _deletion_request_summary(principal, pdef):
    try:
        m = _retention_metrics(principal)
        return _result(pdef, {"pending_deletion_reviews": m.get("pending_deletion_reviews"),
                              "layer_never_executes_a_deletion": True})
    except Exception:
        return _result(pdef, None, available=False)


def _retention_coverage(principal, pdef):
    try:
        from app.services.governance.retention import list_retention_assignments
        m = _retention_metrics(principal)
        return _result(pdef, {"derived": True, "retention_assignments": len(list_retention_assignments()),
                              "active_legal_holds": m.get("active_legal_holds"),
                              "pending_deletion_reviews": m.get("pending_deletion_reviews"),
                              "coverage_is_not_an_enforced_retention_decision": True})
    except Exception:
        return _result(pdef, None, available=False)


def _retention_policy_catalog_availability(principal, pdef):
    return _not_configured(pdef, "no retention-policy catalog beyond the Document Platform / DPIA owner exists "
                                 "in the platform")


# --- governance readiness + risk + executive -----------------------------------------------------------

def _configured_data_domains(principal, pdef):
    try:
        total = len(registry._all_entries())
        cfg = len(registry.configured_domains())
        return _result(pdef, {"configured": cfg, "total": total, "coverage_percent": _pct(cfg, total)})
    except Exception:
        return _result(pdef, None, available=False)


def _unconfigured_data_domains(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


def _governance_readiness(principal, pdef):
    """DERIVED governance readiness — deterministic, authoritative inputs, labeled derived. GOVERNANCE
    READINESS ONLY, never a repaired dataset / created lineage / assigned steward / executed rule / enforced
    retention."""
    try:
        signals = {}
        try:
            domains = _domains()
            signals["data_domains"] = len(domains)
            signals["stewarded_domains"] = sum(1 for d in domains if d.get("steward_user_id"))
        except Exception:
            pass
        try:
            signals["quality_rules"] = len(_rules())
        except Exception:
            pass
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"derived": True, "governance_coverage_not_certification": True,
                              "self_signals": signals, "not_configured_domains": len(nc),
                              "registered_rule_is_not_an_executed_check": True,
                              "coverage_is_not_certification": True})
    except Exception:
        return _result(pdef, None, available=False)


def _data_risk_indicators(principal, pdef):
    try:
        qm = _quality_metrics(principal)
        rm = _retention_metrics(principal)
        domains = _domains()
        unstewarded = sum(1 for d in domains if not d.get("steward_user_id"))
        return _result(pdef, {"derived": True, "critical_open_findings": qm.get("critical_open"),
                              "active_legal_holds": rm.get("active_legal_holds"),
                              "pending_deletion_reviews": rm.get("pending_deletion_reviews"),
                              "unstewarded_domains": unstewarded,
                              "risk_visibility_not_a_remediation_or_enforcement": True})
    except Exception:
        return _result(pdef, None, available=False)


def _governance_health_status(principal, pdef):
    try:
        checked = clean = 0
        validators = (
            ("data_governance", "validate_data_governance"),
            ("change_management", "validate_change_management"),
            ("environment_management", "validate_environment_management"),
        )
        for mod, fn in validators:
            try:
                g = getattr(__import__(f"app.services.{mod}.governance", fromlist=[fn]), fn)()
                checked += 1
                if g.get("ok"):
                    clean += 1
            except Exception:
                pass
        return _result(pdef, {"checked": checked, "clean": clean,
                              "governance_coverage_not_certification": True})
    except Exception:
        return _result(pdef, None, available=False)


def _executive_data_governance_posture(principal, pdef):
    try:
        domains = _domains()
        rules = _rules()
        qm = _quality_metrics(principal)
        configured = len(registry.configured_domains())
        not_configured = list(registry.not_configured_domains())
        return _result(pdef, {"derived": True, "governance_coverage_not_certification": True,
                              "data_domains": len(domains), "quality_rules": len(rules),
                              "open_findings": qm.get("open"),
                              "stewarded_domains": sum(1 for d in domains if d.get("steward_user_id")),
                              "configured_domains": configured,
                              "not_configured_domains": len(not_configured),
                              "registered_rule_is_not_an_executed_check": True})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "data_inventory": _data_inventory,
    "data_domain_coverage": _data_domain_coverage,
    "data_element_coverage": _data_element_coverage,
    "source_of_truth_coverage": _source_of_truth_coverage,
    "governance_gaps": _governance_gaps,
    "lineage_coverage": _lineage_coverage,
    "record_lineage_availability": _record_lineage_availability,
    "mdm_candidate_coverage": _mdm_candidate_coverage,
    "automated_lineage_availability": _automated_lineage_availability,
    "stewardship_coverage": _stewardship_coverage,
    "stewarded_domains": _stewarded_domains,
    "remediation_case_coverage": _remediation_case_coverage,
    "stewardship_workflow_availability": _stewardship_workflow_availability,
    "quality_rule_coverage": _quality_rule_coverage,
    "quality_finding_summary": _quality_finding_summary,
    "critical_finding_summary": _critical_finding_summary,
    "quality_coverage": _quality_coverage,
    "quality_scorecard_availability": _quality_scorecard_availability,
    "retention_assignment_coverage": _retention_assignment_coverage,
    "legal_hold_summary": _legal_hold_summary,
    "deletion_request_summary": _deletion_request_summary,
    "retention_coverage": _retention_coverage,
    "retention_policy_catalog_availability": _retention_policy_catalog_availability,
    "configured_data_domains": _configured_data_domains,
    "unconfigured_data_domains": _unconfigured_data_domains,
    "governance_readiness": _governance_readiness,
    "data_risk_indicators": _data_risk_indicators,
    "governance_health_status": _governance_health_status,
    "executive_data_governance_posture": _executive_data_governance_posture,
}


def compute_panel(principal, key):
    """Compose one panel by key. Read-only, fail-closed, self-restricting. Returns a PanelResult, or None if
    the panel is not registered / not explainable."""
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
