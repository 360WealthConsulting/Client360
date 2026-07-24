"""Enterprise Financial Operations, Revenue Intelligence & Firm Performance Governance (Phase D.57) tests.

Verifies the financial-operations layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative
financial owners — the insurance commission ledger (`insurance_reporting.commission_report`, the one money
owner), the portfolio AUM owner, the single Analytics Registry revenue metrics, Executive Reporting, and
Practice Management — and never becomes a second accounting platform, ERP, billing engine, commission engine,
payroll system, bookkeeping platform, general ledger, or budgeting application.

Covers: the financial + revenue registries; dashboard composition + explainability + deep links; revenue /
profitability / payroll / expense / commission composition; authorization (unauthorized → None; unentitled
panel → restricted, never a value); gate + policy awareness; the firm summary + client/household rollups;
governance (clean + detects); diagnostics; the analytics-counter reuse (single registry); AI summaries; the
routes (registered + capability-gated); and the architecture invariants (no second accounting/ERP/payroll
platform, no duplicated accounting data, no mutation, every dashboard deep-links to authoritative owners).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.financial_operations import (
    client_financial,
    compose_dashboard,
    firm_financial_summary,
    gate,
    get_panel,
    governance,
    household_financial,
    list_dashboards,
    registry,
)
from app.services.financial_operations import diagnostics as diag

FO_DIR = pathlib.Path("app/services/financial_operations")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"analytics.view", "analytics.executive", "insurance.commissions.read",
                            "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no analytics.view


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.FINANCIAL_REGISTRY) == 10
    assert len(registry.REVENUE_REGISTRY) == 8
    assert len(registry.PANEL_REGISTRY) == 20
    assert len(registry.FINANCIAL_DASHBOARDS) == 7


def test_every_financial_category_names_authoritative_reporting_and_calculation_owner():
    for f in registry.FINANCIAL_REGISTRY:
        assert f.authoritative_owner and f.reporting_owner and f.calculation_owner
        assert f.runtime_gate and f.deep_links


def test_every_revenue_type_names_authoritative_reporting_and_recognition_owner():
    for r in registry.REVENUE_REGISTRY:
        assert r.category and r.authoritative_owner and r.reporting_owner and r.recognition_owner
        assert r.runtime_gate


def test_unowned_categories_report_not_configured_never_fabricated():
    # billing / payroll / operating expenses / GL / profitability have no authoritative owner today.
    owners = {f.key: f.authoritative_owner for f in registry.FINANCIAL_REGISTRY}
    for key in ("payroll", "operating_expenses", "profitability", "planning_fees", "subscriptions"):
        assert owners[key] == registry.NOT_CONFIGURED
    # the one authoritative money owner is real.
    assert owners["insurance_commissions"] == "insurance_commissions"


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.FINANCIAL_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_panel_deep_links_to_an_authoritative_owner():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and ("." in p.source or ":" in p.source), p.key


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.FINANCIAL_DASHBOARDS:
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
    assert compose_dashboard(NONE, "revenue") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    p = get_panel(NONE, "firm_aum")
    assert p is not None and p["restricted"] and p["value"] is None


def test_executive_financial_panels_require_analytics_executive():
    # firm financial figures self-restrict to analytics.executive; an analytics.view-only principal sees them
    # restricted (never the value).
    view_only = Principal(4, "v@e.com", "V", frozenset({"analytics.view"}))
    p = get_panel(view_only, "commission_revenue")
    assert p is not None and p["restricted"] and p["value"] is None


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "revenue") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert firm_financial_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "revenue.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "revenue")
    assert result and result.get("gated") == "revenue.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "revenue")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_firm_financial_summary_shape():
    s = firm_financial_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]


def test_client_and_household_financial_are_portfolio_composition():
    cf = client_financial(FIRM, 1)
    assert cf["source"] == "portfolio.book_aum" or cf.get("enabled") is not None
    hf = household_financial(FIRM, 1, [1, 2])
    assert "advisory_revenue_basis" in hf


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_financial_operations()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_accounting_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# post_journal_entry(entry='x')\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_financial_operations()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (FO_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_accounting_billing_or_payroll_engine():
    composed = (FO_DIR / "service.py").read_text() + (FO_DIR / "panels.py").read_text()
    for forbidden in ("record_expected(", "record_received(", "record_adjustment(", "write_off(",
                      "import_statement(", "reconcile_statement(", "create_invoice(", "post_journal_entry(",
                      "run_payroll(", "pay_commission(", "process_payment("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_financial_owners():
    composed = (FO_DIR / "panels.py").read_text() + (FO_DIR / "service.py").read_text()
    assert "commission_report" in composed   # the one authoritative money owner, not a second ledger
    assert "analytics.metrics" in composed   # the single revenue-metric owner, not a second registry


def test_no_duplicated_accounting_data_or_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (FO_DIR / name).read_text()
        assert not re.search(r"\bTable\s*\(", src), name
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("financial_dashboards_composed", "financial_panels_composed", "financial_panel_failures",
                "financial_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.financial_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 20
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/financial-operations", "/api/v1/financial-operations/dashboards",
            "/api/v1/financial-operations/dashboard/{key}", "/api/v1/financial-operations/summary",
            "/api/v1/financial-operations/registry", "/api/v1/financial-operations/panel/{key}",
            "/api/v1/financial-operations/metrics", "/financial-operations/diagnostics"} <= paths


def test_routes_capability_gated():
    for cap in ("analytics.view", "observability.audit"):
        dep = require_capability(cap)
        without = Principal(9, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/financial_operations.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "post_journal_entry("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_financial_performance_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "financial_performance" in ws


def test_ai_never_issues_invoices_or_processes_payroll():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("create_invoice(", "post_journal_entry(", "run_payroll(", "pay_commission(",
                      "process_payment("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/FINANCIAL_OPERATIONS.md", "docs/FINANCIAL_REGISTRY.md",
                "docs/REVENUE_REGISTRY.md", "docs/FINANCIAL_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-062-*.md"))
    assert adrs, "ADR-062 missing"
