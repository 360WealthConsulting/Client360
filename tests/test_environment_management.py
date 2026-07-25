"""Enterprise Environment Management, Deployment Topology & Platform Lifecycle Intelligence (Phase D.64) tests.

Verifies the environment layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative
environment / platform / deployment-topology / lifecycle / infrastructure-dependency owners — the Observability
catalog (environment profiles, deployment references, service inventory, the service dependency graph), the
Observability health owner (runtime snapshots, the live migration head), the Observability service overview,
the Runtime + Policy engines, and the Integration platform — and never becomes a second CMDB /
infrastructure-management platform / cloud-management platform / deployment orchestrator / asset inventory /
configuration database / environment manager / monitoring platform.

Covers: the five registries; registry integrity + duplicate-key prevention (incl. cross-registry) +
configured-owner validation + honest not_configured; dashboard composition + panel explainability + deep links;
authorization; runtime gates; policy enforcement; per-panel restriction; the firm summary + record-scoped
client/household platform-dependency sections that report not_configured and never infer platform impact;
governance; diagnostics; analytics reuse; AI summarize-only; and the architecture invariants (no second CMDB /
infrastructure platform / deployment orchestrator, no persistence, no mutation, no fabricated environments, no
unauthorized platform exposure). Enforces the honesty invariants: environment metadata is not live
infrastructure, a deployment reference is not a deployment, an active flag is not a lifecycle guarantee.
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.environment_management import (
    client_platform_dependencies,
    compose_dashboard,
    environment_summary,
    gate,
    get_panel,
    governance,
    household_platform_dependencies,
    list_dashboards,
    registry,
)
from app.services.environment_management import diagnostics as diag

EM_DIR = pathlib.Path("app/services/environment_management")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"observability.view", "analytics.executive", "integration.view",
                            "observability.audit", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))     # no observability.view/executive


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.ENVIRONMENT_REGISTRY) == 8
    assert len(registry.PLATFORM_REGISTRY) == 9
    assert len(registry.DEPLOYMENT_TOPOLOGY_REGISTRY) == 7
    assert len(registry.LIFECYCLE_REGISTRY) == 8
    assert len(registry.INFRASTRUCTURE_DEPENDENCY_REGISTRY) == 7
    assert len(registry.PANEL_REGISTRY) == 35
    assert len(registry.ENVIRONMENT_DASHBOARDS) == 8


def test_no_duplicate_registry_keys_across_all_registries():
    keys = [e.key for e in registry._all_entries()]
    assert len(keys) == len(set(keys))
    pk = [p.key for p in registry.PANEL_REGISTRY]
    assert len(pk) == len(set(pk))
    dk = [d.key for d in registry.ENVIRONMENT_DASHBOARDS]
    assert len(dk) == len(set(dk))


def test_every_configured_entry_has_authoritative_owner():
    for e in registry._all_entries():
        assert e.owner and e.capabilities and e.deep_links and e.runtime_gate, e.key
        if e.config_status == registry.CONFIGURED:
            assert e.owner != registry.NOT_CONFIGURED, e.key


def test_not_configured_domains_reported_honestly():
    nc = set(registry.not_configured_domains())
    assert {"cloud_environment_provisioning", "cloud_resources", "servers_hosts", "containers_vms",
            "deployment_execution_status", "deployment_rollout_status", "formal_lifecycle_state",
            "deprecation_records", "retirement_records", "decommission_schedule",
            "infrastructure_host_metadata", "network_topology", "cloud_resource_dependencies"} == nc
    assert len(nc) == 13


def test_master_gates_distinct_and_present():
    for g in ("environment_management.enabled", "platform_lifecycle.enabled", "deployment_topology.enabled",
              "environment_ai_summary.enabled"):
        assert g in gate.GATES
    assert "observability.enabled" not in gate.GATES


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.ENVIRONMENT_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        assert board["environment_metadata_is_not_live_infrastructure"] is True
        assert board["deployment_reference_is_not_a_deployment"] is True
        assert board["operational_visibility_not_certification"] is True
        assert "not_configured_domains" in board
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_every_panel_has_owner_source_deep_link_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES


def test_derived_executive_posture_labeled_not_fabricated():
    p = get_panel(FIRM, "executive_platform_posture")
    assert p["derived"] is True
    assert p["value"]["operational_visibility_not_certification"] is True
    assert p["value"]["environment_metadata_is_not_live_infrastructure"] is True
    assert p["value"]["deployment_reference_is_not_a_deployment"] is True


def test_deployment_reference_disclaims_deployment():
    p = get_panel(FIRM, "deployment_reference_inventory")
    assert p["value"]["deployment_reference_is_not_a_deployment"] is True
    assert p["value"]["deployment_execution_status"] == registry.NOT_CONFIGURED


def test_unregistered_dashboard_returns_none():
    assert compose_dashboard(FIRM, "does_not_exist") is None


def test_list_dashboards_metadata_only():
    ld = list_dashboards(FIRM)
    assert ld["enabled"] and len(ld["dashboards"]) == 8
    for d in ld["dashboards"]:
        assert "panel_count" in d and "required_capabilities" in d
        assert "value" not in d


# --- authorization -----------------------------------------------------------

def test_unauthorized_principal_gets_none():
    assert compose_dashboard(NONE, "environment_overview") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    # an observability.view principal without integration.view sees the integration-dependency panel restricted.
    obs_only = Principal(4, "o@e.com", "O", frozenset({"observability.view"}))
    p = get_panel(obs_only, "integration_dependency_coverage")
    assert p is not None and p["restricted"] and p["value"] is None and p["available"] is False


def test_not_configured_panel_is_available_false():
    for key in ("cloud_resource_inventory", "deployment_execution_status", "formal_lifecycle_state",
                "retirement_readiness", "infrastructure_topology_availability"):
        p = get_panel(FIRM, key)
        assert p is not None and p["config_status"] == registry.NOT_CONFIGURED and p["available"] is False


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "environment_overview") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert environment_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate",
                        lambda n: False if n == "deployment_topology.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "deployment_topology")
    assert result and result.get("gated") == "deployment_topology.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "environment_overview")
    assert result and result.get("denied") == "policy"


# --- summary + record-scoped sections ----------------------------------------

def test_environment_summary_operational_visibility_not_certification():
    s = environment_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s
    assert s["operational_visibility_not_certification"] is True
    assert s["environment_metadata_is_not_live_infrastructure"] is True
    assert s["deployment_reference_is_not_a_deployment"] is True
    assert "not_configured_domains" in s


def test_record_scoped_platform_dependencies_are_not_configured_and_never_infer():
    cd = client_platform_dependencies(FIRM, 1)
    assert cd["available"] is False and cd["config_status"] == registry.NOT_CONFIGURED
    assert cd["platform_impact_inferred"] is False and cd["internal_infrastructure_exposed"] is False
    hd = household_platform_dependencies(FIRM, 1, [1, 2])
    assert hd["available"] is False and hd["platform_impact_inferred"] is False


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_environment_management()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# provision(environment='x')\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_environment_management()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "__init__.py"):
        src = (EM_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit(",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_cmdb_or_infrastructure_engine():
    composed = (EM_DIR / "service.py").read_text() + (EM_DIR / "panels.py").read_text()
    for forbidden in ("create_environment_profile(", "create_deployment_reference(", "create_service(",
                      "set_service_status(", "add_dependency(", "capture_runtime_snapshot(", "set_flag(",
                      "provision(", "deploy(", "decommission(", "retire(", "write_audit("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_owners():
    composed = (EM_DIR / "panels.py").read_text() + (EM_DIR / "service.py").read_text()
    assert "list_environment_profiles" in composed
    assert "list_deployment_references" in composed
    assert "observability" in composed


def test_no_fabricated_environment():
    # every value computed by the layer (environment_management.compose) must be labeled derived.
    for p in registry.PANEL_REGISTRY:
        if p.source.startswith("environment_management.compose"):
            assert p.derived, p.key
    p = get_panel(FIRM, "lifecycle_readiness")
    assert p["value"]["operational_visibility_not_certification"] is True
    assert p["value"]["active_flag_is_not_a_lifecycle_guarantee"] is True


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (EM_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


def test_no_secret_exposure_in_panels():
    src = (EM_DIR / "panels.py").read_text()
    for forbidden in ("os.getenv", "os.environ", "connection_string", "SECRET", "password", "token="):
        assert forbidden not in src, forbidden


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("environment_dashboards_composed", "environment_panels_composed",
                "environment_panel_failures", "environment_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.environment_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 35
    assert d["not_configured_domains"] == 13
    assert d["governance"]["ok"] is True
    assert d["environment_metadata_is_not_live_infrastructure"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/environment-management", "/api/v1/environment-management/dashboards",
            "/api/v1/environment-management/dashboard/{key}", "/api/v1/environment-management/summary",
            "/api/v1/environment-management/registry", "/api/v1/environment-management/panel/{key}",
            "/api/v1/environment-management/metrics", "/environment-management/diagnostics"} <= paths


def test_routes_capability_gated():
    dep = require_any_capability("observability.view", "analytics.executive")
    without = Principal(9, "no@e.com", "No", frozenset({"record.read_all"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=without)
    assert ei.value.status_code == 403
    assert dep(principal=Principal(10, "o@e.com", "O", frozenset({"observability.view"}))) is not None
    assert dep(principal=Principal(11, "e@e.com", "E", frozenset({"analytics.executive"}))) is not None


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/environment_management.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit(", "provision(",
                      "create_environment_profile("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_environment_platform_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "environment_platform" in ws


def test_executive_dashboard_reuses_existing_widgets():
    from app.services.executive_intelligence.registry import DASHBOARD_REGISTRY, WIDGET_REGISTRY
    d = next(d for d in DASHBOARD_REGISTRY if d.key == "enterprise_platform_environment_landscape")
    widget_keys = {w.key for w in WIDGET_REGISTRY}
    assert all(w in widget_keys for w in d.widgets)   # no new widget introduced
    assert len(WIDGET_REGISTRY) == 14


def test_client360_and_household_sections_registered():
    from app.services.client360.household import _SECTION_BUILDERS, HOUSEHOLD_SECTIONS
    from app.services.client360.registry import SECTIONS
    assert any(s.key == "platform_dependencies" for s in SECTIONS)
    assert ("platform_dependencies", "observability.view") in HOUSEHOLD_SECTIONS
    assert "platform_dependencies" in _SECTION_BUILDERS


def test_ai_never_provisions_or_deploys():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("provision(", "deploy(", "create_environment_profile(", "set_flag(", "decommission("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/ENTERPRISE_ENVIRONMENT_MANAGEMENT.md", "docs/ENVIRONMENT_REGISTRY.md",
                "docs/PLATFORM_LIFECYCLE_REGISTRY.md", "docs/DEPLOYMENT_TOPOLOGY_REGISTRY.md",
                "docs/ENVIRONMENT_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-069-*.md"))
    assert adrs, "ADR-069 missing"
