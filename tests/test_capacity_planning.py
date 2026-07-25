"""Enterprise Capacity Planning, Workforce Operations & Resource Intelligence (Phase D.61) tests.

Verifies the capacity layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative workforce /
capacity / utilization owners — the Operations capacity owner, the Work Queue, Practice Management, and
Automation Orchestration — and never becomes a second HR platform, HCM, scheduling application, calendar
system, project-management system, PSA, time-tracking platform, payroll platform, or workforce-management
system.

Covers: the three registries; registry integrity + duplicate-key prevention + configured-owner validation +
honest not_configured; dashboard composition + panel explainability + deep links; authorization; runtime
gates; policy enforcement; per-panel restriction; the firm summary + record-scoped client/household staffing
rollups; governance; diagnostics; analytics reuse; AI summarize-only; and the architecture invariants (no
second HR/scheduling/workforce platform, no persistence, no mutation, no fabricated utilization/staffing, no
sensitive workforce data exposure).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.capacity_planning import (
    capacity_summary,
    client_staffing,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_staffing,
    list_dashboards,
    registry,
)
from app.services.capacity_planning import diagnostics as diag

CP_DIR = pathlib.Path("app/services/capacity_planning")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"capacity.read", "analytics.executive", "work.read", "automation.view",
                            "observability.audit", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no capacity.read/executive


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.WORKFORCE_REGISTRY) == 8
    assert len(registry.CAPACITY_REGISTRY) == 9
    assert len(registry.UTILIZATION_REGISTRY) == 5
    assert len(registry.PANEL_REGISTRY) == 23
    assert len(registry.RESOURCE_DASHBOARDS) == 8


def test_no_duplicate_registry_keys():
    for reg in (registry.WORKFORCE_REGISTRY, registry.CAPACITY_REGISTRY, registry.UTILIZATION_REGISTRY,
                registry.PANEL_REGISTRY, registry.RESOURCE_DASHBOARDS):
        keys = [e.key for e in reg]
        assert len(keys) == len(set(keys))


def test_every_configured_entry_has_authoritative_owner():
    for reg in (registry.WORKFORCE_REGISTRY, registry.CAPACITY_REGISTRY, registry.UTILIZATION_REGISTRY):
        for e in reg:
            assert e.owner and e.capabilities and e.deep_links and e.runtime_gate
            if e.config_status == registry.CONFIGURED:
                assert e.owner != registry.NOT_CONFIGURED, e.key


def test_not_configured_domains_reported_honestly():
    nc = set(registry.not_configured_domains())
    # contractors / HR directory + PTO / meeting / onboarding / planning capacity have no authoritative owner.
    assert {"contractors", "meeting_capacity", "onboarding_capacity", "planning_capacity"} <= nc


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.RESOURCE_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        assert board["operational_summary_not_hr_record"] is True
        assert "not_configured_domains" in board
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_every_panel_has_owner_source_deep_link_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES


def test_derived_executive_status_labeled_not_hr_record():
    p = get_panel(FIRM, "executive_workforce_status")
    assert p["derived"] is True
    assert p["value"]["operational_summary_not_hr_record"] is True
    assert p["value"]["not_a_certified_staffing_figure"] is True


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
    assert compose_dashboard(NONE, "workforce_overview") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    # a capacity.read principal without work.read sees the queue panel restricted.
    cap_only = Principal(4, "c@e.com", "C", frozenset({"capacity.read"}))
    p = get_panel(cap_only, "queue_health")
    assert p is not None and p["restricted"] and p["value"] is None and p["available"] is False


def test_not_configured_availability_panel():
    p = get_panel(FIRM, "availability_summary")
    assert p is not None and p["config_status"] == registry.NOT_CONFIGURED and p["available"] is False


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "workforce_overview") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert capacity_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "workforce.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "workforce_overview")
    assert result and result.get("gated") == "workforce.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "workforce_overview")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_capacity_summary_operational_not_hr_record():
    s = capacity_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s
    assert s["operational_summary_not_hr_record"] is True
    assert "not_configured_domains" in s


def test_client_and_household_staffing_are_record_scoped_and_hide_firm_data():
    cs = client_staffing(FIRM, 1)
    assert cs["source"] == "capacity_planning.client_staffing"
    assert cs["firm_utilization_exposed"] is False and cs["employee_workload_exposed"] is False
    hs = household_staffing(FIRM, 1, [1, 2])
    assert hs["firm_utilization_exposed"] is False and "signals" in hs


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_capacity_planning()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# assign_work(work_id='x')\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_capacity_planning()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (CP_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit(",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_hr_scheduling_or_workforce_engine():
    composed = (CP_DIR / "service.py").read_text() + (CP_DIR / "panels.py").read_text()
    for forbidden in ("assign_work(", "assign_record(", "assign_reviewer(", "invite_user(",
                      "create_meeting(", "reschedule(", "book_resource(", "create_capacity_plan(",
                      "set_user_status(", "write_audit("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_owners():
    composed = (CP_DIR / "panels.py").read_text() + (CP_DIR / "service.py").read_text()
    assert "operations.capacity" in composed
    assert "work_queue" in composed


def test_no_fabricated_utilization_or_staffing():
    for p in registry.PANEL_REGISTRY:
        if p.source.startswith("capacity_planning.") and (
                "status" in p.key or "readiness" in p.key or "forecast" in p.key or "gaps" in p.key):
            assert p.derived, p.key
    s = capacity_summary(FIRM)
    assert "certified" not in s


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (CP_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("capacity_dashboards_composed", "capacity_panels_composed", "capacity_panel_failures",
                "capacity_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.capacity_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 23
    assert d["not_configured_domains"] == 4
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/capacity-planning", "/api/v1/capacity-planning/dashboards",
            "/api/v1/capacity-planning/dashboard/{key}", "/api/v1/capacity-planning/summary",
            "/api/v1/capacity-planning/registry", "/api/v1/capacity-planning/panel/{key}",
            "/api/v1/capacity-planning/metrics", "/capacity-planning/diagnostics"} <= paths


def test_routes_capability_gated():
    dep = require_any_capability("capacity.read", "analytics.executive")
    without = Principal(9, "no@e.com", "No", frozenset({"record.read_all"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=without)
    assert ei.value.status_code == 403
    assert dep(principal=Principal(10, "c@e.com", "C", frozenset({"capacity.read"}))) is not None
    assert dep(principal=Principal(11, "e@e.com", "E", frozenset({"analytics.executive"}))) is not None


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/capacity_planning.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit(", "assign_work("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_capacity_workload_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "capacity_workload" in ws


def test_ai_never_assigns_or_schedules():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("assign_work(", "assign_reviewer(", "create_meeting(", "set_user_status("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/ENTERPRISE_CAPACITY_PLANNING.md", "docs/WORKFORCE_REGISTRY.md",
                "docs/CAPACITY_REGISTRY.md", "docs/UTILIZATION_REGISTRY.md",
                "docs/RESOURCE_INTELLIGENCE_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-066-*.md"))
    assert adrs, "ADR-066 missing"
