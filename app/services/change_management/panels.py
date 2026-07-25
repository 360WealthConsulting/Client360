"""Enterprise Change Management panel composition (Phase D.63).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric, and
never any credential / secret / token / environment variable / connection string / private key / deployment
payload / protected infrastructure detail / sensitive configuration value / private incident narrative /
repository credential. Self-verification panels read the architecture manifest (declared route/migration/
capability counts), the live Alembic script head (`observability.health._expected_head`), the live route count
(`len(app.routes)`), the live ADR file count (`docs/adr`), and the live Client 360 section + Executive
dashboard counts — comparing declared vs live (drift). CI-evidence panels REFERENCE the CI pipeline evidence
(produced per-commit, not live-read). Configuration / deployment / incident / maintenance panels compose the
Runtime + Policy engines, the Observability catalog / alerts / incidents, Security incidents, and Compliance
Intelligence. Live git / PR / CI / deployment / rollback / production-verification / post-change owners do not
exist and are emitted ``available=False`` with ``config_status='not_configured'`` — honest, never a fabricated
deployment status, rollback readiness, or production verification. Every compose is fail-closed and
self-restricts. This layer NEVER creates a branch, merges a PR, pushes a commit, tags a release, deploys code,
runs a migration, changes a flag, approves a change, schedules maintenance, acknowledges an incident, executes
rollback, or certifies production. A derived value describes OPERATIONAL READINESS, never approval /
certification / deployment success / production safety — a green build is not production, merged is not
deployed, an absent incident is not success.
"""
from __future__ import annotations

import pathlib

from . import registry, stats
from .model import PanelResult

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_MANIFEST = _REPO_ROOT / "docs" / "platform_architecture_manifest.yaml"
_ADR_DIR = _REPO_ROOT / "docs" / "adr"


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


# --- self-verification reads (read-only) ---------------------------------------------------------------

def _manifest_meta():
    import yaml
    with _MANIFEST.open() as f:
        return (yaml.safe_load(f) or {}).get("meta", {}) or {}


def _live_route_count():
    from app.main import app
    return len(app.routes)


def _live_head():
    from app.services.observability.health import _expected_head
    return _expected_head()


def _adr_numbers():
    nums = []
    for p in _ADR_DIR.glob("ADR-*.md"):
        stem = p.name.split("-", 2)
        if len(stem) >= 2 and stem[1][:3].isdigit():
            nums.append(int(stem[1][:3]))
    return sorted(nums)


# --- catalog panels (DERIVED) --------------------------------------------------------------------------

def _change_domain_inventory(principal, pdef):
    try:
        nc = [c.key for c in registry.CHANGE_DOMAIN_REGISTRY if c.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"count": len(registry.CHANGE_DOMAIN_REGISTRY),
                              "domains": [c.key for c in registry.CHANGE_DOMAIN_REGISTRY],
                              "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _configured_change_domains(principal, pdef):
    try:
        total = len(registry._all_entries())
        cfg = len(registry.configured_domains())
        pct = round(cfg / total * 100, 1) if total else 0.0
        return _result(pdef, {"configured": cfg, "total": total, "coverage_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _unconfigured_change_domains(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


def _configuration_inventory(principal, pdef):
    try:
        sensitive = [c.key for c in registry.CONFIGURATION_REGISTRY if c.sensitivity == "sensitive"]
        return _result(pdef, {"count": len(registry.CONFIGURATION_REGISTRY),
                              "entries": [c.key for c in registry.CONFIGURATION_REGISTRY],
                              "sensitive_entries": len(sensitive),
                              "values_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


# --- release / self-verification -----------------------------------------------------------------------

def _current_release_line(principal, pdef):
    try:
        m = _manifest_meta()
        return _result(pdef, {"release_line": "release/0.13.0", "migration_head": m.get("migration_head"),
                              "route_count": m.get("route_count"),
                              "capability_count": m.get("production_capability_count"),
                              "branch_live_git": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


def _migration_head_status(principal, pdef):
    try:
        declared = _manifest_meta().get("migration_head")
        live = _live_head()
        return _result(pdef, {"declared": declared, "live": live,
                              "in_sync": bool(live) and (declared is None or live == declared),
                              "clean_migration_is_not_app_health": True})
    except Exception:
        return _result(pdef, None, available=False)


def _migration_head_count(principal, pdef):
    try:
        live = _live_head() or ""
        heads = live.split("|") if live else []
        return _result(pdef, {"head_count": len(heads), "single_head": len(heads) == 1, "heads": heads})
    except Exception:
        return _result(pdef, None, available=False)


def _route_count_verification(principal, pdef):
    try:
        declared = _manifest_meta().get("route_count")
        live = _live_route_count()
        return _result(pdef, {"declared": declared, "live": live, "in_sync": declared == live})
    except Exception:
        return _result(pdef, None, available=False)


def _adr_count_verification(principal, pdef):
    try:
        nums = _adr_numbers()
        expected = list(range(1, (max(nums) if nums else 0) + 1))
        return _result(pdef, {"count": len(nums), "max": (max(nums) if nums else 0),
                              "sequential": nums == expected})
    except Exception:
        return _result(pdef, None, available=False)


def _client360_section_count_verification(principal, pdef):
    try:
        from app.services.client360.registry import SECTIONS
        return _result(pdef, {"section_count": len(SECTIONS)})
    except Exception:
        return _result(pdef, None, available=False)


def _executive_dashboard_count_verification(principal, pdef):
    try:
        from app.services.executive_intelligence.registry import DASHBOARD_REGISTRY, WIDGET_REGISTRY
        return _result(pdef, {"dashboard_count": len(DASHBOARD_REGISTRY),
                              "widget_count": len(WIDGET_REGISTRY)})
    except Exception:
        return _result(pdef, None, available=False)


# --- CI evidence (referenced, produced per-commit; not live-read) --------------------------------------

def _ci_evidence(pdef, gate_name):
    return _result(pdef, {"evidence_source": "continuous_integration", "check": gate_name,
                          "produced": "per_commit", "live_read": False,
                          "green_ci_is_not_production": True})


def _ci_build_status(principal, pdef):
    return _ci_evidence(pdef, "build")


def _e2e_status(principal, pdef):
    return _ci_evidence(pdef, "e2e")


def _architecture_guard_status(principal, pdef):
    return _ci_evidence(pdef, "architecture_guards")


def _regression_status(principal, pdef):
    return _ci_evidence(pdef, "pytest")


def _code_quality_status(principal, pdef):
    return _ci_evidence(pdef, "ruff")


def _documentation_status(principal, pdef):
    try:
        from app.services.knowledge_management import knowledge_summary
        s = knowledge_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"documentation_completeness": (s.get("kpis") or {}).get("documentation_completeness"),
                              "ci_documentation_advisory": "per_commit"})
    except Exception:
        return _result(pdef, None, available=False)


# --- governance (composed across the D.55-D.62 read-only layers) ---------------------------------------

def _governance_status(principal, pdef):
    try:
        checked = clean = 0
        validators = (
            ("operational_resilience", "validate_operational_resilience"),
            ("enterprise_risk", "validate_enterprise_risk"),
            ("regulatory_readiness", "validate_regulatory_readiness"),
            ("knowledge_management", "validate_knowledge_management"),
            ("capacity_planning", "validate_capacity_planning"),
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
                              "operational_readiness_not_approval": True})
    except Exception:
        return _result(pdef, None, available=False)


# --- configuration -------------------------------------------------------------------------------------

def _runtime_gate_coverage(principal, pdef):
    try:
        from . import gate
        own = gate.gate_status()
        adoption = {}
        try:
            from app.services.runtime import consumption
            adoption = consumption.adoption_stats() if hasattr(consumption, "adoption_stats") else {}
        except Exception:
            adoption = {}
        return _result(pdef, {"layer_gates": len(own), "layer_gates_enabled": sum(1 for v in own.values() if v),
                              "runtime_adoption_available": bool(adoption)})
    except Exception:
        return _result(pdef, None, available=False)


def _policy_engine_coverage(principal, pdef):
    try:
        from app.services.policy import registry as pol
        cov = pol.coverage() if hasattr(pol, "coverage") else {}
        return _result(pdef, {"policy_engine_present": True,
                              "policy_coverage_available": bool(cov), "policy_values_never_exposed": True})
    except Exception:
        return _result(pdef, {"policy_engine_present": True, "policy_coverage_available": False,
                              "policy_values_never_exposed": True})


def _configuration_drift_availability(principal, pdef):
    try:
        live_verified = ["route_count", "migration_state"]
        reference_only = [c.key for c in registry.CONFIGURATION_REGISTRY
                          if c.key not in ("route_registration", "migration_state")]
        return _result(pdef, {"live_verification": live_verified,
                              "reference_only_count": len(reference_only),
                              "drift_is_declared_vs_live": True})
    except Exception:
        return _result(pdef, None, available=False)


# --- deployment / operational (composed) ---------------------------------------------------------------

def _deployment_evidence(principal, pdef):
    try:
        from app.services.observability.catalog import (
            list_deployment_references,
            list_environment_profiles,
        )
        return _result(pdef, {"deployment_references": len(list_deployment_references()),
                              "environment_profiles": len(list_environment_profiles()),
                              "deployment_execution_status": registry.NOT_CONFIGURED,
                              "merged_is_not_deployed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _maintenance_window_status(principal, pdef):
    try:
        from app.services.observability.alerts import metrics
        m = metrics(principal)
        return _result(pdef, {"active_maintenance_windows": m.get("active_maintenance_windows", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _related_operational_incidents(principal, pdef):
    try:
        from app.services.observability.incidents import metrics
        m = metrics(principal)
        return _result(pdef, {"reliability_incidents": m.get("reliability_incidents", 0),
                              "reliability_findings": m.get("reliability_findings", 0),
                              "absent_incident_is_not_success": True})
    except Exception:
        return _result(pdef, None, available=False)


def _related_security_findings(principal, pdef):
    try:
        from app.services.security.incidents import metrics
        m = metrics(principal)
        return _result(pdef, {"open_incidents": m.get("open_incidents", 0),
                              "open_findings": m.get("open_findings", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _change_related_exceptions(principal, pdef):
    try:
        from app.services.compliance_intelligence import supervisory_dashboard
        sd = supervisory_dashboard(principal)
        if not sd or not sd.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"open_exceptions": sd.get("counts", {}).get("open_exceptions", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- not_configured (honest) ---------------------------------------------------------------------------

def _open_pull_requests(principal, pdef):
    return _not_configured(pdef, "no live git / pull-request reader exists in the platform")


def _merge_status(principal, pdef):
    return _not_configured(pdef, "no live git reader exists; merged is not deployed")


def _merge_commit(principal, pdef):
    return _not_configured(pdef, "no live git reader exists in the platform")


def _release_version(principal, pdef):
    return _not_configured(pdef, "no live version-tag reader exists; a tag does not prove rollout")


def _production_verification_evidence(principal, pdef):
    return _not_configured(pdef, "no authoritative production-verification owner exists; green CI is not "
                                 "production certification")


def _rollback_evidence(principal, pdef):
    return _not_configured(pdef, "no authoritative rollback owner exists in the platform")


def _post_change_review_availability(principal, pdef):
    return _not_configured(pdef, "no authoritative post-change-review owner exists in the platform")


# --- derived readiness + executive ---------------------------------------------------------------------

def _derived_change_readiness_coverage(principal, pdef):
    """DERIVED operational change-readiness — deterministic, authoritative inputs, labeled derived. OPERATIONAL
    READINESS ONLY, never approval / certification / deployment success / production safety."""
    try:
        verifications = {}
        try:
            verifications["route_count_in_sync"] = (_manifest_meta().get("route_count") == _live_route_count())
        except Exception:
            pass
        try:
            declared = _manifest_meta().get("migration_head")
            live = _live_head()
            verifications["migration_in_sync"] = bool(live) and (declared is None or live == declared)
        except Exception:
            pass
        try:
            nums = _adr_numbers()
            verifications["adr_sequential"] = nums == list(range(1, (max(nums) if nums else 0) + 1))
        except Exception:
            pass
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"derived": True,
                              "operational_readiness_not_deployment_or_certification": True,
                              "self_verifications": verifications,
                              "not_configured_domains": len(nc),
                              "green_ci_is_not_production": True, "merged_is_not_deployed": True,
                              "absent_incident_is_not_success": True})
    except Exception:
        return _result(pdef, None, available=False)


def _executive_change_posture(principal, pdef):
    try:
        configured = len(registry.configured_domains())
        not_configured = list(registry.not_configured_domains())
        signals = {}
        try:
            signals["route_count_in_sync"] = (_manifest_meta().get("route_count") == _live_route_count())
        except Exception:
            pass
        try:
            live = _live_head()
            signals["migration_in_sync"] = bool(live) and live == _manifest_meta().get("migration_head")
        except Exception:
            pass
        return _result(pdef, {"derived": True,
                              "operational_readiness_not_deployment_or_certification": True,
                              "configured_domains": configured, "not_configured_domains": len(not_configured),
                              "not_configured": not_configured, "self_verifications": signals,
                              "green_ci_is_not_production": True, "merged_is_not_deployed": True})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "change_domain_inventory": _change_domain_inventory,
    "configured_change_domains": _configured_change_domains,
    "unconfigured_change_domains": _unconfigured_change_domains,
    "current_release_line": _current_release_line,
    "open_pull_requests": _open_pull_requests,
    "merge_status": _merge_status,
    "merge_commit": _merge_commit,
    "release_version": _release_version,
    "ci_build_status": _ci_build_status,
    "e2e_status": _e2e_status,
    "documentation_status": _documentation_status,
    "architecture_guard_status": _architecture_guard_status,
    "governance_status": _governance_status,
    "regression_status": _regression_status,
    "code_quality_status": _code_quality_status,
    "migration_head_status": _migration_head_status,
    "migration_head_count": _migration_head_count,
    "route_count_verification": _route_count_verification,
    "adr_count_verification": _adr_count_verification,
    "client360_section_count_verification": _client360_section_count_verification,
    "executive_dashboard_count_verification": _executive_dashboard_count_verification,
    "runtime_gate_coverage": _runtime_gate_coverage,
    "policy_engine_coverage": _policy_engine_coverage,
    "configuration_inventory": _configuration_inventory,
    "configuration_drift_availability": _configuration_drift_availability,
    "deployment_evidence": _deployment_evidence,
    "production_verification_evidence": _production_verification_evidence,
    "rollback_evidence": _rollback_evidence,
    "maintenance_window_status": _maintenance_window_status,
    "related_operational_incidents": _related_operational_incidents,
    "related_security_findings": _related_security_findings,
    "change_related_exceptions": _change_related_exceptions,
    "post_change_review_availability": _post_change_review_availability,
    "derived_change_readiness_coverage": _derived_change_readiness_coverage,
    "executive_change_posture": _executive_change_posture,
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
