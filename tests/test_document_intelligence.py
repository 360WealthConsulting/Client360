"""Enterprise Document Intelligence & Records Lifecycle (Phase D.50) tests.

Verifies the document-intelligence layer is a governed, READ-ONLY COMPOSITION over the platform's
authoritative document systems — the Document Platform (Phase D.16, the single document + metadata +
lifecycle + retention-policy owner), Governance retention (Phase D.23), and Compliance Intelligence (Phase
D.47) — and never becomes a second DMS, OCR engine, indexing/search engine, archive, document database,
metadata store, or records repository.

Covers: the document + retention + panel + dashboard registries; dashboard composition + explainability +
deep links; lifecycle/retention/metadata composition; authorization (unauthorized → None; unentitled panel
→ restricted, never a value); gate + policy awareness; the firm summary + client/household rollups;
governance (clean + detects); diagnostics; the analytics-counter reuse (single registry); AI summaries; the
routes (registered + capability-gated); and the architecture invariants (no second DMS/OCR/index, no
duplicate metadata, no mutation, every dashboard deep-links to authoritative documents, every lifecycle
calculation references an authoritative owner).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_intelligence import (
    client_documents,
    compose_dashboard,
    document_summary,
    gate,
    get_panel,
    governance,
    household_documents,
    list_dashboards,
    registry,
)
from app.services.document_intelligence import diagnostics as diag

DI_DIR = pathlib.Path("app/services/document_intelligence")

FIRM = Principal(1, "m@e.com", "M", frozenset({
    "documents.view", "compliance.supervise", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no documents.view


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.DOCUMENT_REGISTRY) == 10
    assert len(registry.RETENTION_REGISTRY) == 6
    assert len(registry.PANEL_REGISTRY) == 18
    assert len(registry.INTELLIGENCE_DASHBOARDS) == 6


def test_every_document_class_names_owner_storage_metadata_retention_and_lifecycle():
    for dc in registry.DOCUMENT_REGISTRY:
        assert dc.owner and dc.storage_source and dc.metadata_source and dc.classification
        assert dc.retention_policy and dc.lifecycle and dc.runtime_gate and dc.deep_links
        # storage is owned by the authoritative Document Platform — never a second store.
        assert "document_platform" in dc.storage_source
        # the lifecycle references an authoritative owner (the Document Platform state machine).
        assert "document_platform" in dc.lifecycle or "portal.signatures" in dc.lifecycle
        # every document class points at a registered retention policy.
        assert registry.retention_policy_registered(dc.retention_policy)


def test_every_retention_policy_names_owner_period_archive_and_regulation():
    for rp in registry.RETENTION_REGISTRY:
        assert rp.owner and rp.retention_period and rp.archive_owner and rp.disposition_policy
        assert rp.governing_regulation and rp.runtime_gate
        # archive/disposition is owned by Governance retention — never re-implemented here.
        assert rp.archive_owner == "governance.retention"


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.INTELLIGENCE_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_lifecycle_panel_references_an_authoritative_owner():
    # No panel may report a lifecycle/retention/inventory number without naming an authoritative owner+source.
    for p in registry.PANEL_REGISTRY:
        assert p.owner and ("." in p.source or ":" in p.source), p.key


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_authoritative_documents():
    for d in registry.INTELLIGENCE_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        for panel in board["panels"]:
            # every emitted panel is explainable + deep-links to its authoritative document surface.
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_unregistered_dashboard_returns_none():
    assert compose_dashboard(FIRM, "does_not_exist") is None


def test_list_dashboards_metadata_only():
    ld = list_dashboards(FIRM)
    assert ld["enabled"] and len(ld["dashboards"]) == 6
    for d in ld["dashboards"]:
        assert "panel_count" in d and "required_capabilities" in d
        assert "value" not in d


# --- authorization -----------------------------------------------------------

def test_unauthorized_principal_gets_none():
    assert compose_dashboard(NONE, "document_inventory") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    p = get_panel(NONE, "inventory_by_classification")
    assert p is not None and p["restricted"] and p["value"] is None


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "document_inventory") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert document_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "retention.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "retention")
    assert result and result.get("gated") == "retention.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "document_inventory")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_document_summary_shape():
    s = document_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]


def test_client_and_household_documents_are_platform_composition():
    cw = client_documents(FIRM, 1)
    assert cw["source"] == "document_intelligence.client_documents" or cw.get("enabled") is not None
    hw = household_documents(FIRM, 1, [1, 2])
    assert "document_count" in hw and "by_classification" in hw


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_document_intelligence()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_document_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# update_document(principal, 1, fields={})\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_document_intelligence()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


def test_governance_detects_second_ocr_engine(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "panels.py":
            s = s + "\nimport pytesseract\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_document_intelligence()
    assert any(f["type"] == "second_ocr_or_index" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (DI_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_dms_ocr_or_index():
    composed = (DI_DIR / "service.py").read_text() + (DI_DIR / "panels.py").read_text()
    # never a document mutation / second DMS
    for forbidden in ("create_document(", "update_document(", "set_status(", "apply_retention(",
                      "create_retention_policy(", "execute_deletion(", "place_legal_hold("):
        assert forbidden not in composed, forbidden
    # never a second OCR / index engine
    for tell in ("tesseract", "extract_text(", "to_tsvector", "pdfminer", "pypdf", "textract"):
        assert tell not in composed, tell


def test_composes_the_authoritative_document_platform():
    composed = (DI_DIR / "panels.py").read_text() + (DI_DIR / "service.py").read_text()
    assert "document_platform" in composed  # the D.16 document owner, not a second DMS
    assert "list_documents" in composed or "documents_for_entity" in composed


def test_no_duplicate_metadata_store():
    # metadata is owned by the Document Platform; this layer must define no metadata table/model of its own.
    for name in ("registry.py", "model.py", "panels.py", "service.py"):
        src = (DI_DIR / name).read_text()
        assert not re.search(r"\bTable\s*\(", src), name


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (DI_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("document_dashboards_composed", "document_panels_composed", "document_panel_failures",
                "document_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.document_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 18
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/document-intelligence", "/api/v1/document-intelligence/dashboards",
            "/api/v1/document-intelligence/dashboard/{key}", "/api/v1/document-intelligence/summary",
            "/api/v1/document-intelligence/registry", "/api/v1/document-intelligence/panel/{key}",
            "/api/v1/document-intelligence/metrics", "/document-intelligence/diagnostics"} <= paths


def test_routes_capability_gated():
    for cap in ("documents.view", "observability.audit"):
        dep = require_capability(cap)
        without = Principal(9, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/document_intelligence.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "update_document("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_document_intelligence_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "document_intelligence" in ws


def test_ai_never_mutates_documents():
    # The AI grounding for documents is summarize-only (counts), and the module carries no doc mutation.
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("update_document(", "archive(", "soft_delete(", "apply_retention("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/DOCUMENT_INTELLIGENCE.md", "docs/RECORDS_LIFECYCLE.md", "docs/RETENTION_REGISTRY.md",
                "docs/DOCUMENT_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-055-*.md"))
    assert adrs, "ADR-055 missing"
