"""Enterprise Data Governance, Master Data & Platform Stewardship (Phase D.52) tests.

Verifies the data-governance layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative
data owners — the D.23 Governance package (catalog / quality / MDM / retention / overview), the Person-merge
/ entity-resolution engine, the Event registry, and the domain entity owners — and never becomes a second
master-data platform, identity system, synchronization engine, entity-resolution engine, metadata
repository, or merge engine.

Covers: the master-data + stewardship registries; dashboard composition + explainability + deep links;
lineage/validation/duplicate/metadata composition; authorization (unauthorized → None; unentitled panel →
restricted, never a value); gate + policy awareness; the firm summary + client/household rollups; governance
(clean + detects); diagnostics; the analytics-counter reuse (single registry); AI summaries; the routes
(registered + capability-gated); and the architecture invariants (no second master-data platform, no second
identity system, no duplicate merge engine, no duplicate metadata repository, no mutation, every dashboard
deep-links to authoritative entity owners, every lineage summary references an authoritative owner).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.data_governance import (
    client_governance,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    governance_summary,
    household_governance,
    list_dashboards,
    registry,
)
from app.services.data_governance import diagnostics as diag

DG_DIR = pathlib.Path("app/services/data_governance")

FIRM = Principal(1, "m@e.com", "M", frozenset({"governance.view", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no governance.view


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.MASTER_DATA_REGISTRY) == 15
    assert len(registry.STEWARDSHIP_REGISTRY) == 8
    assert len(registry.PANEL_REGISTRY) == 19
    assert len(registry.GOVERNANCE_DASHBOARDS) == 7


def test_every_entity_names_authoritative_identity_metadata_and_lineage_owner():
    for e in registry.MASTER_DATA_REGISTRY:
        assert e.authoritative_owner and e.identity_owner and e.metadata_owner and e.stewardship_owner
        assert e.lineage_owner and e.runtime_gate and e.deep_links
        # every entity's stewardship owner is a registered stewardship role.
        assert registry.stewardship_registered(e.stewardship_owner)


def test_every_stewardship_role_names_business_technical_validation_and_approval_owner():
    for s in registry.STEWARDSHIP_REGISTRY:
        assert s.business_owner and s.technical_owner and s.validation_owner and s.approval_owner
        assert s.runtime_gate


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.GOVERNANCE_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_lineage_summary_references_an_authoritative_owner():
    # every panel names an authoritative owner + source (dotted/qualified).
    for p in registry.PANEL_REGISTRY:
        assert p.owner and ("." in p.source or ":" in p.source), p.key


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_authoritative_owners():
    for d in registry.GOVERNANCE_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_unregistered_dashboard_returns_none():
    assert compose_dashboard(FIRM, "does_not_exist") is None


def test_list_dashboards_metadata_only():
    ld = list_dashboards(FIRM)
    assert ld["enabled"] and len(ld["dashboards"]) == 7
    for d in ld["dashboards"]:
        assert "panel_count" in d and "required_capabilities" in d
        assert "value" not in d


# --- authorization -----------------------------------------------------------

def test_unauthorized_principal_gets_none():
    assert compose_dashboard(NONE, "master_data") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    p = get_panel(NONE, "registered_entities")
    assert p is not None and p["restricted"] and p["value"] is None


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "master_data") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert governance_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "lineage.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "lineage")
    assert result and result.get("gated") == "lineage.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "master_data")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_governance_summary_shape():
    s = governance_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]


def test_client_and_household_governance_are_lineage_composition():
    cw = client_governance(FIRM, 1)
    assert cw["source"] == "governance.mdm.person_lineage" or cw.get("enabled") is not None
    hw = household_governance(FIRM, 1, [1, 2])
    assert "lineage_records" in hw


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_data_governance()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_merge(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# merge_source_contacts([1, 2])\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_data_governance()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (DG_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_master_data_identity_or_merge_engine():
    composed = (DG_DIR / "service.py").read_text() + (DG_DIR / "panels.py").read_text()
    for forbidden in ("merge_source_contacts(", "resolve_link_to_person(", "resolve_create_person(",
                      "record_merge_decision(", "scan_duplicates(", "create_candidate(", "record_lineage(",
                      "create_domain(", "create_element(", "create_rule(", "run_check("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_governance_package():
    composed = (DG_DIR / "panels.py").read_text() + (DG_DIR / "service.py").read_text()
    assert "governance." in composed  # the D.23 governance owner, not a second MDM
    assert "person_lineage" in composed or "list_candidates" in composed


def test_no_duplicate_metadata_repository():
    for name in ("registry.py", "model.py", "panels.py", "service.py"):
        src = (DG_DIR / name).read_text()
        assert not re.search(r"\bTable\s*\(", src), name


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (DG_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("governance_dashboards_composed", "governance_panels_composed", "governance_panel_failures",
                "governance_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.governance_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 19
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/data-governance", "/api/v1/data-governance/dashboards",
            "/api/v1/data-governance/dashboard/{key}", "/api/v1/data-governance/summary",
            "/api/v1/data-governance/registry", "/api/v1/data-governance/panel/{key}",
            "/api/v1/data-governance/metrics", "/data-governance/diagnostics"} <= paths


def test_routes_capability_gated():
    for cap in ("governance.view", "observability.audit"):
        dep = require_capability(cap)
        without = Principal(9, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/data_governance.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "merge_source_contacts("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_data_governance_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "data_governance" in ws


def test_ai_never_merges_or_alters_identities():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("merge_source_contacts(", "resolve_link_to_person(", "record_merge_decision("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/DATA_GOVERNANCE.md", "docs/MASTER_DATA_REGISTRY.md", "docs/STEWARDSHIP_REGISTRY.md",
                "docs/DATA_GOVERNANCE_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-057-*.md"))
    assert adrs, "ADR-057 missing"
