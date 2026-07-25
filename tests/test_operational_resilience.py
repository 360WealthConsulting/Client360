"""Enterprise Operational Resilience, Incident Management & Service Continuity Intelligence (Phase D.60) tests.

Verifies the resilience layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative
operational-resilience owners — the Observability service catalog / health / incidents / alerts, Security
incidents, the Integration Platform, Vendor Management, Automation Orchestration, and Business Continuity — and
never becomes a second incident-management platform, ticketing system, monitoring platform, help desk, DR
platform, change-management platform, CMDB, scheduler, or alerting engine.

Covers: the five registries; registry integrity + duplicate-key prevention + configured-owner validation +
honest not_configured; dashboard composition + panel explainability + deep links; authorization; runtime
gates; policy enforcement; per-panel restriction; the firm summary + record-scoped client/household rollups;
governance; diagnostics; analytics reuse; AI summarize-only; and the architecture invariants (no second
monitoring/incident platform, no persistence, no mutation, no fabricated operational status, no sensitive
operational data exposure).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.operational_resilience import (
    client_operational_impact,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_operational_impact,
    list_dashboards,
    registry,
    resilience_summary,
)
from app.services.operational_resilience import diagnostics as diag

OR_DIR = pathlib.Path("app/services/operational_resilience")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"observability.view", "analytics.executive", "security.view", "integration.view",
                            "automation.view", "observability.audit", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no observability.view/executive


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.OPERATIONAL_SERVICE_REGISTRY) == 6
    assert len(registry.INCIDENT_CATEGORY_REGISTRY) == 7
    assert len(registry.CONTINUITY_CAPABILITY_REGISTRY) == 7
    assert len(registry.RECOVERY_OBJECTIVE_REGISTRY) == 5
    assert len(registry.OPERATIONAL_DEPENDENCY_REGISTRY) == 4
    assert len(registry.PANEL_REGISTRY) == 24
    assert len(registry.RESILIENCE_DASHBOARDS) == 8


def test_no_duplicate_registry_keys():
    for reg in (registry.OPERATIONAL_SERVICE_REGISTRY, registry.INCIDENT_CATEGORY_REGISTRY,
                registry.CONTINUITY_CAPABILITY_REGISTRY, registry.RECOVERY_OBJECTIVE_REGISTRY,
                registry.OPERATIONAL_DEPENDENCY_REGISTRY, registry.PANEL_REGISTRY,
                registry.RESILIENCE_DASHBOARDS):
        keys = [e.key for e in reg]
        assert len(keys) == len(set(keys))


def test_every_configured_entry_has_authoritative_owner():
    for reg in (registry.OPERATIONAL_SERVICE_REGISTRY, registry.INCIDENT_CATEGORY_REGISTRY,
                registry.CONTINUITY_CAPABILITY_REGISTRY, registry.RECOVERY_OBJECTIVE_REGISTRY,
                registry.OPERATIONAL_DEPENDENCY_REGISTRY):
        for e in reg:
            assert e.owner and e.capabilities and e.deep_links and e.runtime_gate
            if e.config_status == registry.CONFIGURED:
                assert e.owner != registry.NOT_CONFIGURED, e.key


def test_not_configured_domains_reported_honestly():
    nc = set(registry.not_configured_domains())
    # backup / restore / DR / recovery-testing / failover / vendor-incidents have no authoritative owner.
    assert {"backup", "restore", "disaster_recovery", "recovery_testing", "failover",
            "vendor_incidents"} <= nc


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.RESILIENCE_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        assert board["operational_posture_not_certification"] is True
        assert "not_configured_domains" in board
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_every_panel_has_owner_source_deep_link_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES


def test_derived_executive_status_labeled_and_not_certification():
    p = get_panel(FIRM, "executive_operational_status")
    assert p["derived"] is True
    assert p["value"]["operational_posture_not_certification"] is True
    assert p["value"]["not_production_health_certification"] is True
    assert p["value"]["absent_incident_is_not_health"] is True


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
    assert compose_dashboard(NONE, "operational_resilience") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    # an observability.view principal without security.view sees the security panel restricted.
    obs_only = Principal(4, "o@e.com", "O", frozenset({"observability.view"}))
    p = get_panel(obs_only, "security_incidents")
    assert p is not None and p["restricted"] and p["value"] is None and p["available"] is False


def test_not_configured_recovery_test_panel():
    p = get_panel(FIRM, "recovery_test_coverage")
    assert p is not None and p["config_status"] == registry.NOT_CONFIGURED and p["available"] is False


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "operational_resilience") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert resilience_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "incident_intelligence.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "incident_readiness")
    assert result and result.get("gated") == "incident_intelligence.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "operational_resilience")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_resilience_summary_posture_not_certification():
    s = resilience_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s
    assert s["operational_posture_not_certification"] is True
    assert s["absent_incident_is_not_health"] is True
    assert "not_configured_domains" in s


def test_client_and_household_impact_are_record_scoped_and_hide_firm_wide():
    cr = client_operational_impact(FIRM, 1)
    assert cr["source"] == "operational_resilience.client_operational_impact"
    assert cr["firm_wide_operational_status_exposed"] is False
    assert cr["per_client_incident_impact"] == registry.NOT_CONFIGURED
    hr = household_operational_impact(FIRM, 1, [1, 2])
    assert hr["firm_wide_operational_status_exposed"] is False and "signals" in hr


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_operational_resilience()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# raise_alert(alert_id='x')\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_operational_resilience()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (OR_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit(",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_monitoring_or_incident_engine():
    composed = (OR_DIR / "service.py").read_text() + (OR_DIR / "panels.py").read_text()
    for forbidden in ("open_incident(", "set_incident_status(", "raise_alert(", "acknowledge_alert(",
                      "resolve_alert(", "create_maintenance_window(", "set_service_status(",
                      "create_service(", "write_audit("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_owners():
    composed = (OR_DIR / "panels.py").read_text() + (OR_DIR / "service.py").read_text()
    assert "observability" in composed          # the service/health/incident/alert owner
    assert "business_continuity" in composed     # the continuity owner


def test_no_fabricated_operational_status():
    for p in registry.PANEL_REGISTRY:
        if p.source.startswith("operational_resilience.") and (
                "status" in p.key or "posture" in p.key or "readiness" in p.key):
            assert p.derived, p.key
    s = resilience_summary(FIRM)
    assert "healthy" not in s and "compliant" not in s


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (OR_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("resilience_dashboards_composed", "resilience_panels_composed", "resilience_panel_failures",
                "resilience_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.resilience_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 24
    assert d["not_configured_domains"] == 6
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/operational-resilience", "/api/v1/operational-resilience/dashboards",
            "/api/v1/operational-resilience/dashboard/{key}", "/api/v1/operational-resilience/summary",
            "/api/v1/operational-resilience/registry", "/api/v1/operational-resilience/panel/{key}",
            "/api/v1/operational-resilience/metrics", "/operational-resilience/diagnostics"} <= paths


def test_routes_capability_gated():
    dep = require_any_capability("observability.view", "analytics.executive")
    without = Principal(9, "no@e.com", "No", frozenset({"record.read_all"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=without)
    assert ei.value.status_code == 403
    assert dep(principal=Principal(10, "o@e.com", "O", frozenset({"observability.view"}))) is not None
    assert dep(principal=Principal(11, "e@e.com", "E", frozenset({"analytics.executive"}))) is not None


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/operational_resilience.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit(", "raise_alert("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_operational_status_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "operational_status" in ws


def test_ai_never_generates_alerts_or_certifies():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("raise_alert(", "open_incident(", "acknowledge_alert(", "create_maintenance_window("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/ENTERPRISE_OPERATIONAL_RESILIENCE.md", "docs/INCIDENT_REGISTRY.md",
                "docs/SERVICE_DEPENDENCY_REGISTRY.md", "docs/BUSINESS_CONTINUITY_REGISTRY.md",
                "docs/OPERATIONAL_RESILIENCE_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-065-*.md"))
    assert adrs, "ADR-065 missing"
