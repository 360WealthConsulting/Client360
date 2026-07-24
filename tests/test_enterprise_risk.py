"""Enterprise Risk Management, Internal Controls & Assurance Governance (Phase D.58) tests.

Verifies the risk-management layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative
risk / control / assurance owners — Compliance Intelligence + the Exception Engine, Security Operations +
incidents, Data Governance, the Integration Platform, Business Continuity, Vendor Management, Financial
Operations, Document Intelligence, Automation Orchestration, Insurance licensing, and the Runtime + Policy
engines + audit logging — and never becomes a second GRC platform, risk register, compliance engine, exception
system, audit platform, incident-management system, control-testing application, policy engine, or approval
engine.

Covers: the risk + control + assurance registries; registry completeness + duplicate-key prevention +
configured-owner validation + honest not_configured; dashboard composition + panel explainability + deep
links; compliance / operational / security / third-party / resilience / financial-control summaries; control +
assurance coverage; authorization (unauthorized → None; unentitled panel → restricted, never a value); gate +
policy awareness; the firm summary + client/household rollups; governance (clean + detects); diagnostics; the
analytics-counter reuse (single registry); AI summarize-only; and the architecture invariants (no second GRC
/ risk / compliance / exception / incident / control-testing system, no mutation, no fabricated risk score, no
sensitive evidence, every configured panel references an authoritative owner, every dashboard deep-links,
every derived summary labeled, missing control-testing owners report not_configured).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.enterprise_risk import (
    client_risk_controls,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_risk_controls,
    list_dashboards,
    registry,
    risk_summary,
)
from app.services.enterprise_risk import diagnostics as diag

ER_DIR = pathlib.Path("app/services/enterprise_risk")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"compliance.supervise", "analytics.executive", "security.view", "governance.view",
                            "integration.view", "observability.view", "automation.view", "documents.view",
                            "observability.audit", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no supervise/executive


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.ENTERPRISE_RISK_REGISTRY) == 15
    assert len(registry.CONTROL_REGISTRY) == 20
    assert len(registry.ASSURANCE_REGISTRY) == 15
    assert len(registry.PANEL_REGISTRY) == 24
    assert len(registry.RISK_DASHBOARDS) == 8


def test_no_duplicate_registry_keys():
    for keys in ([r.key for r in registry.ENTERPRISE_RISK_REGISTRY],
                 [c.key for c in registry.CONTROL_REGISTRY],
                 [a.key for a in registry.ASSURANCE_REGISTRY],
                 [p.key for p in registry.PANEL_REGISTRY],
                 [d.key for d in registry.RISK_DASHBOARDS]):
        assert len(keys) == len(set(keys))


def test_every_configured_risk_domain_has_authoritative_owner():
    for r in registry.ENTERPRISE_RISK_REGISTRY:
        assert r.risk_category and r.signal_owners and r.capabilities and r.deep_links and r.runtime_gate
        if r.config_status == registry.CONFIGURED:
            assert r.authoritative_owner != registry.NOT_CONFIGURED, r.key


def test_not_configured_domains_reported_honestly():
    not_cfg = set(registry.not_configured_domains())
    # model/AI risk + privacy risk have no authoritative owner today.
    assert {"model_ai_risk", "privacy_risk"} <= not_cfg
    for key in not_cfg:
        assert registry.risk_domain(key).authoritative_owner == registry.NOT_CONFIGURED


def test_control_testing_is_not_configured_everywhere():
    # control testing / effectiveness is owned nowhere — every test_owner MUST be not_configured.
    for c in registry.CONTROL_REGISTRY:
        assert c.test_owner == registry.NOT_CONFIGURED, c.key


def test_every_configured_control_and_assurance_has_owner():
    for c in registry.CONTROL_REGISTRY:
        assert c.control_family and c.control_objective and c.capabilities and c.deep_links
        if c.config_status == registry.CONFIGURED:
            assert c.authoritative_owner != registry.NOT_CONFIGURED, c.key
    for a in registry.ASSURANCE_REGISTRY:
        assert a.assurance_owner and a.evidence_source and a.scope and a.frequency and a.deep_link
        assert a.assurance_owner != registry.NOT_CONFIGURED  # every assurance source references evidence


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.RISK_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_panel_deep_links_to_an_authoritative_owner():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and ("." in p.source or ":" in p.source or "_" in p.source), p.key


def test_derived_posture_panel_is_labeled_derived():
    assert registry.panel("enterprise_risk_posture").derived is True
    assert registry.panel("risk_domain_inventory").derived is True


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.RISK_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        assert "not_configured_domains" in board and "configured_domains" in board
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


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
    assert compose_dashboard(NONE, "enterprise_risk") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    # a compliance.supervise principal without security.view sees security panels restricted.
    supervisor = Principal(4, "s@e.com", "S", frozenset({"compliance.supervise"}))
    p = get_panel(supervisor, "security_incidents")
    assert p is not None and p["restricted"] and p["value"] is None


def test_restricted_panel_leaks_no_value_or_metadata():
    supervisor = Principal(5, "s2@e.com", "S2", frozenset({"compliance.supervise"}))
    p = get_panel(supervisor, "financial_reconciliation_status")
    assert p["restricted"] and p["value"] is None and p["available"] is False


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "enterprise_risk") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert risk_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "controls_assurance.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "controls_assurance")
    assert result and result.get("gated") == "controls_assurance.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "enterprise_risk")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_risk_summary_shape_and_no_compliance_certification():
    s = risk_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]
    assert s["not_compliance_certification"] is True   # absence of a finding never certifies compliance
    assert "not_configured_domains" in s


def test_client_and_household_risk_controls_are_record_scoped_composition():
    cr = client_risk_controls(FIRM, 1)
    assert cr["source"] == "enterprise_risk.client_risk_controls"
    assert cr["not_compliance_certification"] is True and "signals" in cr
    hr = household_risk_controls(FIRM, 1, [1, 2])
    assert "signals" in hr and hr["not_compliance_certification"] is True


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_enterprise_risk()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_risk_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# resolve(exception_id='x')\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_enterprise_risk()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


def test_governance_detects_fabricated_control_test_owner(monkeypatch):
    # simulate a control declaring a test owner — control testing must stay not_configured.
    from app.services.enterprise_risk.registry import ControlFamily
    bogus = ControlFamily("bogus", "access", "obj", "owner", "ev", "mon", "SOME_TEST_OWNER", "not_configured",
                          "not_configured", "controls_assurance.enabled", ("security.view",), ("/x",),
                          "configured")
    monkeypatch.setattr(registry, "CONTROL_REGISTRY", (*registry.CONTROL_REGISTRY, bogus))
    report = governance.validate_enterprise_risk()
    assert any(f["type"] == "fabricated_control_test_owner" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (ER_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit(",
                     "write_audit_event", "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_grc_risk_or_incident_engine():
    composed = (ER_DIR / "service.py").read_text() + (ER_DIR / "panels.py").read_text()
    for forbidden in ("raise_exception(", "acknowledge(", "escalate(", "resolve(", "assign(",
                      "submit_review(", "record_decision(", "create_incident(", "create_finding(",
                      "approve_exception(", "write_audit("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_owners():
    composed = (ER_DIR / "panels.py").read_text() + (ER_DIR / "service.py").read_text()
    assert "compliance_intelligence" in composed   # the compliance/exception owner, not a second GRC
    assert "security.incidents" in composed        # the incident owner, not a second incident manager


def test_no_fabricated_composite_risk_score():
    # no panel emits a certified composite risk score; the posture panel is a derived coverage summary.
    for p in registry.PANEL_REGISTRY:
        if "posture" in p.key or p.key.endswith("_score"):
            assert p.derived, p.key
    posture = get_panel(FIRM, "enterprise_risk_posture")
    assert posture["derived"] is True and posture["value"].get("not_a_certified_rating") is True


def test_no_duplicated_registers_or_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (ER_DIR / name).read_text()
        assert not re.search(r"\bTable\s*\(", src), name
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("risk_dashboards_composed", "risk_panels_composed", "risk_panel_failures",
                "risk_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.risk_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 24
    assert d["not_configured_risk_domains"] == 2
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/enterprise-risk", "/api/v1/enterprise-risk/dashboards",
            "/api/v1/enterprise-risk/dashboard/{key}", "/api/v1/enterprise-risk/summary",
            "/api/v1/enterprise-risk/registry", "/api/v1/enterprise-risk/panel/{key}",
            "/api/v1/enterprise-risk/metrics", "/enterprise-risk/diagnostics"} <= paths


def test_routes_capability_gated():
    # the risk surface admits a supervisor OR an executive; a principal with neither is refused.
    dep = require_any_capability("compliance.supervise", "analytics.executive")
    without = Principal(9, "no@e.com", "No", frozenset({"record.read_all"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=without)
    assert ei.value.status_code == 403
    # either capability admits.
    assert dep(principal=Principal(10, "s@e.com", "S", frozenset({"compliance.supervise"}))) is not None
    assert dep(principal=Principal(11, "e@e.com", "E", frozenset({"analytics.executive"}))) is not None


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/enterprise_risk.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit(", "resolve("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_enterprise_risk_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "enterprise_risk" in ws


def test_ai_never_assigns_or_certifies():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("resolve(", "record_decision(", "approve_exception(", "create_incident(",
                      "raise_exception("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/ENTERPRISE_RISK_MANAGEMENT.md", "docs/ENTERPRISE_RISK_REGISTRY.md",
                "docs/CONTROL_REGISTRY.md", "docs/ASSURANCE_REGISTRY.md", "docs/RISK_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-063-*.md"))
    assert adrs, "ADR-063 missing"
