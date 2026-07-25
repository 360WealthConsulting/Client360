"""Enterprise Change Management, Release Governance & Configuration Intelligence (Phase D.63) tests.

Verifies the change layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative change /
release / configuration / evidence owners — the architecture manifest, the live Alembic script head, the live
route / ADR / section / dashboard counts, the Runtime + Policy engines, the Observability catalog / alerts /
incidents / health owners, Security incidents, Compliance Intelligence, and the CI pipeline evidence the
manifest records — and never becomes a second ITSM / change-management / deployment / CI-CD / Git / CMDB /
feature-flag / release-approval / incident / maintenance-scheduling platform.

Covers: the four registries; registry integrity + duplicate-key prevention + configured-owner validation +
honest not_configured; dashboard composition + panel explainability + deep links; self-verification drift
panels; authorization; runtime gates; policy enforcement; per-panel restriction; the firm summary +
record-scoped client/household change-impact rollups; governance; diagnostics; analytics reuse; AI
summarize-only; and the architecture invariants (no second ITSM/CI-CD/deployment platform, no persistence, no
mutation, no fabricated change/deployment/release, no credential exposure). Enforces the honesty invariants:
green CI is not production, merged is not deployed, an absent incident is not success.
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.change_management import (
    change_summary,
    client_change_impact,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_change_impact,
    list_dashboards,
    registry,
)
from app.services.change_management import diagnostics as diag

CM_DIR = pathlib.Path("app/services/change_management")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"observability.view", "analytics.executive", "security.view",
                            "compliance.supervise", "documents.view", "observability.audit",
                            "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))     # no observability.view/executive


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.CHANGE_DOMAIN_REGISTRY) == 15
    assert len(registry.RELEASE_REGISTRY) == 15
    assert len(registry.CONFIGURATION_REGISTRY) == 13
    assert len(registry.CHANGE_EVIDENCE_REGISTRY) == 20
    assert len(registry.PANEL_REGISTRY) == 35
    assert len(registry.CHANGE_DASHBOARDS) == 8


def test_no_duplicate_registry_keys():
    for reg in (registry.CHANGE_DOMAIN_REGISTRY, registry.RELEASE_REGISTRY,
                registry.CONFIGURATION_REGISTRY, registry.CHANGE_EVIDENCE_REGISTRY,
                registry.PANEL_REGISTRY, registry.CHANGE_DASHBOARDS):
        keys = [e.key for e in reg]
        assert len(keys) == len(set(keys))


def test_every_configured_entry_has_authoritative_owner():
    for reg in (registry.CHANGE_DOMAIN_REGISTRY, registry.RELEASE_REGISTRY,
                registry.CONFIGURATION_REGISTRY, registry.CHANGE_EVIDENCE_REGISTRY):
        for e in reg:
            links = getattr(e, "deep_links", None) or getattr(e, "deep_link", None)
            assert e.owner and e.capabilities and links and e.runtime_gate, e.key
            if e.config_status == registry.CONFIGURED:
                assert e.owner != registry.NOT_CONFIGURED, e.key


def test_not_configured_domains_reported_honestly():
    nc = set(registry.not_configured_domains())
    # live git / PR / CI / deployment / rollback / production verification / post-change review — no owner.
    assert {"branch", "pull_request", "merge_commit", "version_tag", "deployment_status",
            "rollback_artifact", "production_verification_status", "deployment_verification",
            "rollback_test", "production_signoff", "post_change_review"} <= nc
    assert len(nc) == 15


def test_master_gates_distinct_and_present():
    for g in ("change_management.enabled", "release_governance.enabled",
              "configuration_intelligence.enabled", "deployment_evidence.enabled",
              "change_ai_summary.enabled"):
        assert g in gate.GATES
    # no unrelated gate reused.
    assert "observability.enabled" not in gate.GATES


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.CHANGE_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        assert board["operational_readiness_not_deployment_or_certification"] is True
        assert board["merged_is_not_deployed"] is True
        assert board["green_ci_is_not_production"] is True
        assert "not_configured_domains" in board
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_every_panel_has_owner_source_deep_link_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES


def test_derived_executive_posture_labeled_not_fabricated():
    p = get_panel(FIRM, "executive_change_posture")
    assert p["derived"] is True
    assert p["value"]["operational_readiness_not_deployment_or_certification"] is True
    assert p["value"]["green_ci_is_not_production"] is True
    assert p["value"]["merged_is_not_deployed"] is True


def test_self_verification_drift_panels_compare_declared_vs_live():
    rc = get_panel(FIRM, "route_count_verification")
    assert rc["value"]["declared"] == rc["value"]["live"]  # manifest bumped to live route count
    assert rc["value"]["in_sync"] is True
    mh = get_panel(FIRM, "migration_head_status")
    assert mh["value"]["in_sync"] is True
    assert mh["value"]["clean_migration_is_not_app_health"] is True
    adr = get_panel(FIRM, "adr_count_verification")
    assert adr["value"]["sequential"] is True


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
    assert compose_dashboard(NONE, "change_overview") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    # an observability.view principal without security.view sees the security-findings panel restricted.
    obs_only = Principal(4, "o@e.com", "O", frozenset({"observability.view"}))
    p = get_panel(obs_only, "related_security_findings")
    assert p is not None and p["restricted"] and p["value"] is None and p["available"] is False


def test_not_configured_panel_is_available_false():
    for key in ("rollback_evidence", "production_verification_evidence", "open_pull_requests"):
        p = get_panel(FIRM, key)
        assert p is not None and p["config_status"] == registry.NOT_CONFIGURED and p["available"] is False


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "change_overview") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert change_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate",
                        lambda n: False if n == "release_governance.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "release_readiness")
    assert result and result.get("gated") == "release_governance.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "change_overview")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_change_summary_operational_readiness_not_certification():
    s = change_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s
    assert s["operational_readiness_not_deployment_or_certification"] is True
    assert s["green_ci_is_not_production"] is True and s["merged_is_not_deployed"] is True
    assert "not_configured_domains" in s


def test_client_and_household_change_impact_hide_firm_change_status():
    cd = client_change_impact(FIRM, 1)
    assert cd["source"] == "change_management.client_change_impact"
    assert cd["firm_wide_change_status_exposed"] is False and cd["merged_is_not_deployed"] is True
    hd = household_change_impact(FIRM, 1, [1, 2])
    assert hd["firm_wide_change_status_exposed"] is False and "signals" in hd


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_change_management()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# deploy(release='x')\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_change_management()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "__init__.py"):
        src = (CM_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit(",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_change_or_deployment_engine():
    composed = (CM_DIR / "service.py").read_text() + (CM_DIR / "panels.py").read_text()
    for forbidden in ("set_flag(", "upgrade(", "downgrade(", "merge(", "deploy(", "rollback(",
                      "schedule_maintenance(", "create_environment_profile(", "write_audit("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_owners_and_self_verification():
    composed = (CM_DIR / "panels.py").read_text() + (CM_DIR / "service.py").read_text()
    assert "_expected_head" in composed        # live migration head
    assert "app.routes" in composed            # live route count
    assert "observability" in composed


def test_no_fabricated_change():
    # every value computed by the layer (change_management.compose) must be labeled derived.
    for p in registry.PANEL_REGISTRY:
        if p.source.startswith("change_management.compose"):
            assert p.derived, p.key
    p = get_panel(FIRM, "derived_change_readiness_coverage")
    assert p["value"]["operational_readiness_not_deployment_or_certification"] is True
    assert p["value"]["green_ci_is_not_production"] is True


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (CM_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


def test_no_secret_exposure_in_panels():
    src = (CM_DIR / "panels.py").read_text()
    for forbidden in ("os.getenv", "os.environ", "connection_string", "SECRET", "password", "token="):
        assert forbidden not in src, forbidden


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("change_dashboards_composed", "change_panels_composed", "change_panel_failures",
                "change_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.change_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 35
    assert d["not_configured_domains"] == 15
    assert d["governance"]["ok"] is True
    assert d["green_ci_is_not_production"] is True and d["merged_is_not_deployed"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/change-management", "/api/v1/change-management/dashboards",
            "/api/v1/change-management/dashboard/{key}", "/api/v1/change-management/summary",
            "/api/v1/change-management/registry", "/api/v1/change-management/panel/{key}",
            "/api/v1/change-management/metrics", "/change-management/diagnostics"} <= paths


def test_routes_capability_gated():
    dep = require_any_capability("observability.view", "analytics.executive")
    without = Principal(9, "no@e.com", "No", frozenset({"record.read_all"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=without)
    assert ei.value.status_code == 403
    assert dep(principal=Principal(10, "o@e.com", "O", frozenset({"observability.view"}))) is not None
    assert dep(principal=Principal(11, "e@e.com", "E", frozenset({"analytics.executive"}))) is not None


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/change_management.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit(", "deploy(", "set_flag("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_change_release_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "change_release" in ws


def test_executive_dashboard_reuses_existing_widgets():
    from app.services.executive_intelligence.registry import DASHBOARD_REGISTRY, WIDGET_REGISTRY
    d = next(d for d in DASHBOARD_REGISTRY if d.key == "enterprise_change_release_governance")
    widget_keys = {w.key for w in WIDGET_REGISTRY}
    assert all(w in widget_keys for w in d.widgets)   # no new widget introduced
    assert len(WIDGET_REGISTRY) == 14


def test_client360_and_household_sections_registered():
    from app.services.client360.household import _SECTION_BUILDERS, HOUSEHOLD_SECTIONS
    from app.services.client360.registry import SECTIONS
    assert any(s.key == "change_impact" for s in SECTIONS)
    assert ("change_impact", "observability.view") in HOUSEHOLD_SECTIONS
    assert "change_impact" in _SECTION_BUILDERS


def test_ai_never_deploys_or_approves():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("deploy(", "set_flag(", "merge(", "rollback(", "approve("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/ENTERPRISE_CHANGE_MANAGEMENT.md", "docs/CHANGE_DOMAIN_REGISTRY.md",
                "docs/RELEASE_REGISTRY.md", "docs/CONFIGURATION_REGISTRY.md",
                "docs/CHANGE_EVIDENCE_REGISTRY.md", "docs/CHANGE_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-068-*.md"))
    assert adrs, "ADR-068 missing"
