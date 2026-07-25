"""Enterprise Knowledge Management, SOP Governance & Institutional Intelligence (Phase D.62) tests.

Verifies the knowledge layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative knowledge /
SOP / documentation owners — the Document Platform, Document Intelligence, and Data Governance retention — and
never becomes a second wiki, document-management platform, Confluence replacement, SharePoint,
records-management platform, search engine, AI knowledge store, or document repository.

Covers: the five registries; registry integrity + duplicate-key prevention + configured-owner validation +
honest not_configured; dashboard composition + panel explainability + deep links; authorization; runtime
gates; policy enforcement; per-panel restriction; the firm summary + record-scoped client/household
documentation rollups; governance; diagnostics; analytics reuse; AI summarize-only; and the architecture
invariants (no second wiki/document platform, no persistence, no mutation, no fabricated knowledge, no
unauthorized document exposure).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.knowledge_management import (
    client_documentation,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_documentation,
    knowledge_summary,
    list_dashboards,
    registry,
)
from app.services.knowledge_management import diagnostics as diag

KM_DIR = pathlib.Path("app/services/knowledge_management")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"documents.view", "analytics.executive", "governance.view", "compliance.supervise",
                            "observability.audit", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no documents.view/executive


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.KNOWLEDGE_DOMAIN_REGISTRY) == 8
    assert len(registry.SOP_CATEGORY_REGISTRY) == 6
    assert len(registry.DOCUMENTATION_OWNER_REGISTRY) == 5
    assert len(registry.KNOWLEDGE_SOURCE_REGISTRY) == 7
    assert len(registry.PUBLICATION_STATUS_REGISTRY) == 5
    assert len(registry.PANEL_REGISTRY) == 22
    assert len(registry.KNOWLEDGE_DASHBOARDS) == 8


def test_no_duplicate_registry_keys():
    for reg in (registry.KNOWLEDGE_DOMAIN_REGISTRY, registry.SOP_CATEGORY_REGISTRY,
                registry.DOCUMENTATION_OWNER_REGISTRY, registry.KNOWLEDGE_SOURCE_REGISTRY,
                registry.PUBLICATION_STATUS_REGISTRY, registry.PANEL_REGISTRY,
                registry.KNOWLEDGE_DASHBOARDS):
        keys = [e.key for e in reg]
        assert len(keys) == len(set(keys))


def test_every_configured_entry_has_authoritative_owner():
    for reg in (registry.KNOWLEDGE_DOMAIN_REGISTRY, registry.SOP_CATEGORY_REGISTRY,
                registry.DOCUMENTATION_OWNER_REGISTRY, registry.KNOWLEDGE_SOURCE_REGISTRY,
                registry.PUBLICATION_STATUS_REGISTRY):
        for e in reg:
            assert e.owner and e.capabilities and e.deep_links and e.runtime_gate
            if e.config_status == registry.CONFIGURED:
                assert e.owner != registry.NOT_CONFIGURED, e.key


def test_not_configured_domains_reported_honestly():
    nc = set(registry.not_configured_domains())
    # SOP governance / knowledge base / wiki / Confluence / search have no authoritative owner.
    assert {"knowledge_base", "institutional_memory", "runbooks", "playbooks", "onboarding_sops",
            "wiki", "confluence", "search_index"} <= nc


def test_master_gate_is_non_colliding_with_knowledge_graph():
    # the D.45 knowledge GRAPH owns `knowledge.enabled`; D.62 uses a distinct master gate.
    assert "knowledge_management.enabled" in gate.GATES
    assert "knowledge.enabled" not in gate.GATES


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.KNOWLEDGE_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        assert board["documentation_coverage_not_fabricated_knowledge"] is True
        assert "not_configured_domains" in board
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_every_panel_has_owner_source_deep_link_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES


def test_derived_executive_status_labeled_not_fabricated():
    p = get_panel(FIRM, "executive_knowledge_status")
    assert p["derived"] is True
    assert p["value"]["documentation_coverage_not_fabricated_knowledge"] is True
    assert p["value"]["not_a_certified_sop_or_approval"] is True


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
    assert compose_dashboard(NONE, "knowledge_overview") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    # a documents.view principal without governance.view sees the retention panel restricted.
    docs_only = Principal(4, "d@e.com", "D", frozenset({"documents.view"}))
    p = get_panel(docs_only, "retention_coverage")
    assert p is not None and p["restricted"] and p["value"] is None and p["available"] is False


def test_not_configured_runbook_panel():
    p = get_panel(FIRM, "runbook_coverage")
    assert p is not None and p["config_status"] == registry.NOT_CONFIGURED and p["available"] is False


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "knowledge_overview") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert knowledge_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "sop_governance.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "sop_governance")
    assert result and result.get("gated") == "sop_governance.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "knowledge_overview")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_knowledge_summary_not_fabricated_knowledge():
    s = knowledge_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s
    assert s["documentation_coverage_not_fabricated_knowledge"] is True
    assert "not_configured_domains" in s


def test_client_and_household_documentation_are_record_scoped_and_hide_firm_data():
    cd = client_documentation(FIRM, 1)
    assert cd["source"] == "knowledge_management.client_documentation"
    assert cd["firm_wide_metrics_exposed"] is False and cd["internal_sops_exposed"] is False
    hd = household_documentation(FIRM, 1, [1, 2])
    assert hd["firm_wide_metrics_exposed"] is False and "signals" in hd


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_knowledge_management()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# create_document(name='x')\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_knowledge_management()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (KM_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit(",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_wiki_or_document_engine():
    composed = (KM_DIR / "service.py").read_text() + (KM_DIR / "panels.py").read_text()
    for forbidden in ("create_document(", "update_document(", "set_status(", "approve(", "create_version(",
                      "approve_version(", "create_retention_assignment(", "execute_deletion(",
                      "write_audit("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_owners():
    composed = (KM_DIR / "panels.py").read_text() + (KM_DIR / "service.py").read_text()
    assert "document_platform" in composed
    assert "document_intelligence" in composed


def test_no_fabricated_knowledge():
    # every layer-derived status/health/coverage panel must be labeled derived (never a certified figure).
    for p in registry.PANEL_REGISTRY:
        if p.source.startswith("knowledge_management.") and (
                "status" in p.key or "health" in p.key or "coverage" in p.key or "gaps" in p.key):
            assert p.derived, p.key
    # the executive posture explicitly disclaims certified SOP / approval / institutional knowledge.
    p = get_panel(FIRM, "executive_knowledge_status")
    assert p["value"]["not_a_certified_sop_or_approval"] is True
    assert p["value"]["documentation_coverage_not_fabricated_knowledge"] is True


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (KM_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("knowledge_dashboards_composed", "knowledge_panels_composed", "knowledge_panel_failures",
                "knowledge_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.knowledge_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 22
    assert d["not_configured_domains"] == 8
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/knowledge-management", "/api/v1/knowledge-management/dashboards",
            "/api/v1/knowledge-management/dashboard/{key}", "/api/v1/knowledge-management/summary",
            "/api/v1/knowledge-management/registry", "/api/v1/knowledge-management/panel/{key}",
            "/api/v1/knowledge-management/metrics", "/knowledge-management/diagnostics"} <= paths


def test_routes_capability_gated():
    dep = require_any_capability("documents.view", "analytics.executive")
    without = Principal(9, "no@e.com", "No", frozenset({"record.read_all"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=without)
    assert ei.value.status_code == 403
    assert dep(principal=Principal(10, "d@e.com", "D", frozenset({"documents.view"}))) is not None
    assert dep(principal=Principal(11, "e@e.com", "E", frozenset({"analytics.executive"}))) is not None


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/knowledge_management.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit(", "create_document("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_knowledge_sops_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "knowledge_sops" in ws


def test_ai_never_creates_or_publishes():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("create_document(", "update_document(", "approve(", "create_version("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/ENTERPRISE_KNOWLEDGE_MANAGEMENT.md", "docs/KNOWLEDGE_DOMAIN_REGISTRY.md",
                "docs/SOP_GOVERNANCE.md", "docs/DOCUMENTATION_OWNERSHIP.md",
                "docs/KNOWLEDGE_INTELLIGENCE_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-067-*.md"))
    assert adrs, "ADR-067 missing"
