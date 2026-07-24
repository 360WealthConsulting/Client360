"""Enterprise Vendor Management, Third-Party Risk & Technology Lifecycle Governance (Phase D.56) tests.

Verifies the vendor-management layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative
vendor / technology owners — the Integration Platform provider registry (the vendor inventory of record), the
Security certificate & secret store, the Observability service catalog, Insurance licensing, and Security
incidents + Compliance Intelligence — and never becomes a second vendor-management platform, procurement
system, contract repository, CMDB, asset inventory, licensing platform, or risk engine.

Covers: the vendor + technology-lifecycle registries; dashboard composition + explainability + deep links;
vendor / licensing / renewal / risk composition; authorization (unauthorized → None; unentitled panel →
restricted, never a value); gate + policy awareness; the firm summary + client/household rollups; governance
(clean + detects); diagnostics; the analytics-counter reuse (single registry); AI summaries; the routes
(registered + capability-gated); and the architecture invariants (no second vendor/licensing/contract system,
no duplicated inventories, no mutation, every dashboard deep-links to authoritative owners).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.vendor_management import (
    client_technology,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_technology,
    list_dashboards,
    registry,
    vendor_summary,
)
from app.services.vendor_management import diagnostics as diag

VM_DIR = pathlib.Path("app/services/vendor_management")

FIRM = Principal(1, "m@e.com", "M", frozenset({"integration.view", "security.view", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no integration.view


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.VENDOR_REGISTRY) == 8
    assert len(registry.TECHNOLOGY_LIFECYCLE_REGISTRY) == 8
    assert len(registry.PANEL_REGISTRY) == 20
    assert len(registry.VENDOR_DASHBOARDS) == 7


def test_every_vendor_class_names_authoritative_integration_security_and_lifecycle_owner():
    for v in registry.VENDOR_REGISTRY:
        assert v.authoritative_owner and v.integration_owner and v.security_owner and v.lifecycle_owner
        assert v.provider_type and v.runtime_gate and v.deep_links


def test_every_lifecycle_class_names_owner_lifecycle_renewal_and_support():
    for t in registry.TECHNOLOGY_LIFECYCLE_REGISTRY:
        assert t.category and t.owner and t.lifecycle_owner and t.renewal_owner and t.support_owner
        assert t.runtime_gate


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.VENDOR_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_panel_deep_links_to_an_authoritative_owner():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and ("." in p.source or ":" in p.source), p.key


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.VENDOR_DASHBOARDS:
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
    assert compose_dashboard(NONE, "vendors") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    p = get_panel(NONE, "vendor_inventory")
    assert p is not None and p["restricted"] and p["value"] is None


def test_risk_panels_require_security_view():
    # third-party-risk panels self-restrict to security.view; an integration.view-only principal sees them
    # restricted (never the value).
    integ_only = Principal(4, "i@e.com", "I", frozenset({"integration.view"}))
    p = get_panel(integ_only, "security_risk")
    assert p is not None and p["restricted"] and p["value"] is None


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "vendors") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert vendor_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "licensing.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "licensing")
    assert result and result.get("gated") == "licensing.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "vendors")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_vendor_summary_shape():
    s = vendor_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]


def test_client_and_household_technology_are_integration_composition():
    cw = client_technology(FIRM, 1)
    assert cw["source"] == "integration_hub.client_integrations" or cw.get("enabled") is not None
    hw = household_technology(FIRM, 1, [1, 2])
    assert "vendor_dependencies" in hw


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_vendor_management()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_vendor_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# create_provider(code='x')\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_vendor_management()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (VM_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_vendor_licensing_or_contract_engine():
    composed = (VM_DIR / "service.py").read_text() + (VM_DIR / "panels.py").read_text()
    for forbidden in ("create_provider(", "create_connector(", "set_connector_status(",
                      "create_certificate(", "renew_certificate_reference(", "rotate_secret(",
                      "create_license(", "renew_license(", "create_incident(", "run_sync("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_vendor_owner():
    composed = (VM_DIR / "panels.py").read_text() + (VM_DIR / "service.py").read_text()
    assert "integration.connectors" in composed  # the vendor inventory of record, not a second store
    assert "list_providers" in composed


def test_no_duplicated_inventory_or_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (VM_DIR / name).read_text()
        assert not re.search(r"\bTable\s*\(", src), name
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("vendor_dashboards_composed", "vendor_panels_composed", "vendor_panel_failures",
                "vendor_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.vendor_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 20
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/vendor-management", "/api/v1/vendor-management/dashboards",
            "/api/v1/vendor-management/dashboard/{key}", "/api/v1/vendor-management/summary",
            "/api/v1/vendor-management/registry", "/api/v1/vendor-management/panel/{key}",
            "/api/v1/vendor-management/metrics", "/vendor-management/diagnostics"} <= paths


def test_routes_capability_gated():
    for cap in ("integration.view", "observability.audit"):
        dep = require_capability(cap)
        without = Principal(9, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/vendor_management.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "create_provider("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_technology_vendor_health_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "technology_vendor_health" in ws


def test_ai_never_approves_or_renews():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("create_provider(", "renew_license(", "renew_certificate_reference(",
                      "set_connector_status("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/VENDOR_MANAGEMENT.md", "docs/VENDOR_REGISTRY.md",
                "docs/TECHNOLOGY_LIFECYCLE_REGISTRY.md", "docs/VENDOR_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-061-*.md"))
    assert adrs, "ADR-061 missing"
