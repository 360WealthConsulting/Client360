"""Enterprise Data Governance, Lineage & Information Stewardship Intelligence (Phase D.66) tests.

Verifies the data-governance-intelligence layer is a governed, READ-ONLY COMPOSITION over the platform's
authoritative D.23 Governance owners — the Governance catalog (data domains, elements, quality rules,
survivorship rules, stewardship), Governance MDM (lineage & provenance, merge candidates), Governance Quality
(findings), and Governance Retention (assignments, legal holds, deletion requests, cases) — and never becomes a
second data catalog / metadata repository / ETL platform / MDM platform / warehouse / governance platform /
lineage engine / quality engine.

Covers: the five registries; registry integrity + duplicate-key prevention (incl. cross-registry) +
configured-owner validation + honest not_configured; the distinct non-colliding master gate; dashboard
composition + panel explainability + deep links; authorization; runtime gates; policy enforcement; per-panel
restriction; the firm summary + record-scoped client/household data-governance-metadata sections that expose
only source-system provenance and never infer governance state or leak confidential metadata; governance;
diagnostics; analytics reuse; AI summarize-only; and the architecture invariants (no second data catalog /
lineage engine / metadata repository, no persistence, no mutation, no fabricated metadata, no inferred
lineage). Enforces the honesty invariants: a registered rule is not an executed check, a lineage record is not
a complete lineage, coverage is not certification.
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.data_governance_intelligence import (
    client_data_governance,
    compose_dashboard,
    data_governance_summary,
    gate,
    get_panel,
    governance,
    household_data_governance,
    list_dashboards,
    registry,
)
from app.services.data_governance_intelligence import diagnostics as diag

DG_DIR = pathlib.Path("app/services/data_governance_intelligence")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"governance.view", "analytics.executive", "observability.audit",
                            "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))     # no governance.view/executive


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.DATA_DOMAIN_REGISTRY) == 8
    assert len(registry.DATA_LINEAGE_REGISTRY) == 5
    assert len(registry.DATA_STEWARDSHIP_REGISTRY) == 5
    assert len(registry.DATA_QUALITY_REGISTRY) == 5
    assert len(registry.DATA_RETENTION_REGISTRY) == 6
    assert len(registry.PANEL_REGISTRY) == 29
    assert len(registry.DATA_GOVERNANCE_DASHBOARDS) == 8


def test_no_duplicate_registry_keys_across_all_registries():
    keys = [e.key for e in registry._all_entries()]
    assert len(keys) == len(set(keys))
    pk = [p.key for p in registry.PANEL_REGISTRY]
    assert len(pk) == len(set(pk))
    dk = [d.key for d in registry.DATA_GOVERNANCE_DASHBOARDS]
    assert len(dk) == len(set(dk))


def test_every_configured_entry_has_authoritative_owner():
    for e in registry._all_entries():
        assert e.owner and e.capabilities and e.deep_links and e.runtime_gate, e.key
        if e.config_status == registry.CONFIGURED:
            assert e.owner != registry.NOT_CONFIGURED, e.key


def test_not_configured_domains_reported_honestly():
    nc = set(registry.not_configured_domains())
    assert {"external_data_catalog", "business_glossary", "data_classification", "automated_column_lineage",
            "data_sharing_agreements", "stewardship_assignment_workflow", "data_product_ownership",
            "quality_scorecards", "retention_policy_catalog", "data_privacy_impact_assessments"} == nc
    assert len(nc) == 10


def test_master_gate_distinct_from_d52_and_present():
    for g in ("data_governance_intelligence.enabled", "lineage_landscape.enabled",
              "data_quality_landscape.enabled", "data_governance_ai_summary.enabled"):
        assert g in gate.GATES
    # must NOT reuse the D.52 data_governance layer's gates.
    assert "data_governance.enabled" not in gate.GATES
    assert "lineage.enabled" not in gate.GATES


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.DATA_GOVERNANCE_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        assert board["registered_rule_is_not_an_executed_check"] is True
        assert board["lineage_record_is_not_complete_lineage"] is True
        assert board["governance_coverage_not_certification"] is True
        assert "not_configured_domains" in board
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_every_panel_has_owner_source_deep_link_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES


def test_derived_executive_posture_labeled_not_fabricated():
    p = get_panel(FIRM, "executive_data_governance_posture")
    assert p["derived"] is True
    assert p["value"]["governance_coverage_not_certification"] is True
    assert p["value"]["registered_rule_is_not_an_executed_check"] is True


def test_quality_rule_disclaims_executed_check():
    p = get_panel(FIRM, "quality_rule_coverage")
    assert p["value"]["registered_rule_is_not_an_executed_check"] is True


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
    assert compose_dashboard(NONE, "enterprise_data_inventory") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    # an analytics.executive principal without governance.view sees a governance.view panel restricted.
    exec_only = Principal(4, "e@e.com", "E", frozenset({"analytics.executive"}))
    p = get_panel(exec_only, "data_domain_coverage")
    assert p is not None and p["restricted"] and p["value"] is None and p["available"] is False


def test_not_configured_panel_is_available_false():
    for key in ("automated_lineage_availability", "stewardship_workflow_availability",
                "quality_scorecard_availability", "retention_policy_catalog_availability"):
        p = get_panel(FIRM, key)
        assert p is not None and p["config_status"] == registry.NOT_CONFIGURED and p["available"] is False


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "enterprise_data_inventory") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert data_governance_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "lineage_landscape.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "lineage_landscape")
    assert result and result.get("gated") == "lineage_landscape.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "enterprise_data_inventory")
    assert result and result.get("denied") == "policy"


# --- summary + record-scoped sections ----------------------------------------

def test_data_governance_summary_coverage_not_certification():
    s = data_governance_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s
    assert s["governance_coverage_not_certification"] is True
    assert s["registered_rule_is_not_an_executed_check"] is True
    assert s["lineage_record_is_not_complete_lineage"] is True
    assert "not_configured_domains" in s


def test_record_scoped_governance_metadata_exposes_only_provenance():
    cd = client_data_governance(FIRM, 1)
    assert cd["internal_governance_notes_exposed"] is False
    assert cd["confidential_metadata_exposed"] is False and cd["governance_state_inferred"] is False
    assert "lineage_records" in cd["signals"] and "source_systems" in cd["signals"]
    hd = household_data_governance(FIRM, 1, [1, 2])
    assert hd["governance_state_inferred"] is False


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_data_governance_intelligence()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# record_lineage(entity_type='person', entity_id=1)\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_data_governance_intelligence()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "__init__.py"):
        src = (DG_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit(",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_catalog_or_lineage_engine():
    composed = (DG_DIR / "service.py").read_text() + (DG_DIR / "panels.py").read_text()
    for forbidden in ("create_domain(", "create_element(", "create_rule(", "record_lineage(",
                      "record_merge_decision(", "create_finding(", "run_check(",
                      "create_retention_assignment(", "place_legal_hold(", "execute_deletion(",
                      "create_case(", "write_audit("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_owners():
    composed = (DG_DIR / "panels.py").read_text() + (DG_DIR / "service.py").read_text()
    assert "list_domains" in composed
    assert "person_lineage" in composed
    assert "governance" in composed


def test_no_fabricated_metadata():
    # every value computed by the layer (data_governance_intelligence.compose) must be labeled derived.
    for p in registry.PANEL_REGISTRY:
        if p.source.startswith("data_governance_intelligence.compose"):
            assert p.derived, p.key
    p = get_panel(FIRM, "governance_readiness")
    assert p["value"]["governance_coverage_not_certification"] is True
    assert p["value"]["coverage_is_not_certification"] is True


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (DG_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


def test_no_sensitive_exposure_in_panels():
    # guard against ACTUAL exposure (field access / env reads), not docstring mentions that disclaim it.
    src = (DG_DIR / "panels.py").read_text()
    for forbidden in ("os.getenv", "os.environ", "token=", '"ssn"', "'ssn'", '"detail"', "'detail'"):
        assert forbidden not in src, forbidden


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("data_governance_dashboards_composed", "data_governance_panels_composed",
                "data_governance_panel_failures", "data_governance_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.data_governance_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 29
    assert d["not_configured_domains"] == 10
    assert d["governance"]["ok"] is True
    assert d["registered_rule_is_not_an_executed_check"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/data-governance-intelligence", "/api/v1/data-governance-intelligence/dashboards",
            "/api/v1/data-governance-intelligence/dashboard/{key}",
            "/api/v1/data-governance-intelligence/summary", "/api/v1/data-governance-intelligence/registry",
            "/api/v1/data-governance-intelligence/panel/{key}",
            "/api/v1/data-governance-intelligence/metrics",
            "/data-governance-intelligence/diagnostics"} <= paths


def test_routes_capability_gated():
    dep = require_any_capability("governance.view", "analytics.executive")
    without = Principal(9, "no@e.com", "No", frozenset({"record.read_all"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=without)
    assert ei.value.status_code == 403
    assert dep(principal=Principal(10, "g@e.com", "G", frozenset({"governance.view"}))) is not None
    assert dep(principal=Principal(11, "e@e.com", "E", frozenset({"analytics.executive"}))) is not None


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/data_governance_intelligence.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit(", "record_lineage(",
                      "create_rule("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_data_governance_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "data_governance_intelligence" in ws


def test_executive_dashboard_reuses_existing_widgets():
    from app.services.executive_intelligence.registry import DASHBOARD_REGISTRY, WIDGET_REGISTRY
    d = next(d for d in DASHBOARD_REGISTRY if d.key == "enterprise_data_governance")
    widget_keys = {w.key for w in WIDGET_REGISTRY}
    assert all(w in widget_keys for w in d.widgets)   # no new widget introduced
    assert len(WIDGET_REGISTRY) == 14


def test_client360_and_household_sections_registered():
    from app.services.client360.household import _SECTION_BUILDERS, HOUSEHOLD_SECTIONS
    from app.services.client360.registry import SECTIONS
    assert any(s.key == "data_governance_metadata" for s in SECTIONS)
    assert ("data_governance_metadata", "governance.view") in HOUSEHOLD_SECTIONS
    assert "data_governance_metadata" in _SECTION_BUILDERS


def test_ai_never_mutates_governance():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("record_lineage(", "create_rule(", "create_finding(", "execute_deletion(",
                      "create_domain("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/ENTERPRISE_DATA_GOVERNANCE.md", "docs/DATA_DOMAIN_REGISTRY.md",
                "docs/DATA_LINEAGE_REGISTRY.md", "docs/DATA_STEWARDSHIP_REGISTRY.md", "docs/DATA_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-071-*.md"))
    assert adrs, "ADR-071 missing"
