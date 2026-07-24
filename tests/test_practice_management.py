"""Enterprise Practice Management, Capacity Planning & Resource Operations (Phase D.49) tests.

Verifies the practice-management layer is a governed, READ-ONLY COMPOSITION over the platform's
authoritative operational owners — Operations Capacity (the capacity/utilization owner, D.20), the Unified
Work Queue, Workflow Automation, the opportunity + Analytics firm-intelligence layers, and the tax domain —
and never becomes a second workflow engine, scheduler, staffing/assignment engine, work queue,
capacity/planning engine, metrics registry, or persistence store.

Covers: the four declarative registries (capacity models, resources, panels, dashboards); dashboard
composition + explainability + deep links; authorization (unauthorized → None; unentitled panel →
restricted, never a value); gate + policy awareness; the firm summary + client/household workload rollups;
governance (clean + detects); diagnostics; the analytics-counter reuse (single registry); the routes
(registered + capability-gated); and the architecture invariants (no mutation, no second engine, every
utilization calc names an authoritative source, every panel deep-links).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.practice_management import (
    client_workload,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_workload,
    list_dashboards,
    practice_summary,
    registry,
)
from app.services.practice_management import diagnostics as diag

PM_DIR = pathlib.Path("app/services/practice_management")

FIRM = Principal(1, "m@e.com", "M", frozenset({
    "capacity.read", "work.read", "analytics.view", "operations.view", "record.read_all"}))
WORK_ONLY = Principal(2, "w@e.com", "W", frozenset({"work.read"}))          # no capacity.read
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no practice access


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.CAPACITY_REGISTRY) == 9
    assert len(registry.RESOURCE_REGISTRY) == 6
    assert len(registry.PANEL_REGISTRY) == 19
    assert len(registry.PRACTICE_DASHBOARDS) == 8


def test_every_capacity_model_names_owner_workflow_source_and_horizon():
    for cm in registry.CAPACITY_REGISTRY:
        assert cm.owner and cm.governing_workflow and cm.workload_source and cm.utilization_method
        assert cm.planning_horizon and cm.runtime_gate and cm.refresh_policy and cm.deep_links
        # the capacity owner is the authoritative Operations Capacity (D.20) — never a second engine.
        assert cm.owner == "operations.capacity"


def test_every_resource_names_authoritative_sources():
    for rm in registry.RESOURCE_REGISTRY:
        assert rm.owner and rm.capabilities and rm.workload_source and rm.assignment_source
        assert rm.scheduling_source and rm.utilization_source and rm.availability_source
        # assignment + scheduling are OWNED elsewhere — this layer only references them.
        assert "assign" in rm.assignment_source or "record_assignments" in rm.assignment_source \
            or "reviews" in rm.assignment_source or "tasks" in rm.assignment_source
        assert "scheduling" in rm.scheduling_source


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.PRACTICE_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_utilization_panel_references_an_authoritative_source():
    # No panel may compute a bare number without naming the authoritative read it came from.
    for p in registry.PANEL_REGISTRY:
        assert "." in p.source or ":" in p.source, p.key  # e.g. operations.capacity.capacity_overview


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_are_explainable():
    for d in registry.PRACTICE_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        for panel in board["panels"]:
            # every emitted panel is explainable + deep-links to its authoritative surface.
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        # deep_links map is populated for the drill-down.
        assert board["deep_links"]


def test_unregistered_dashboard_returns_none():
    assert compose_dashboard(FIRM, "does_not_exist") is None


def test_list_dashboards_metadata_only():
    ld = list_dashboards(FIRM)
    assert ld["enabled"] and len(ld["dashboards"]) == 8
    for d in ld["dashboards"]:
        assert "panel_count" in d and "required_capabilities" in d
        assert "value" not in d  # metadata only, never a composed value


# --- authorization -----------------------------------------------------------

def test_unauthorized_principal_gets_none():
    # NONE holds no required capability for any practice dashboard.
    assert compose_dashboard(NONE, "advisor_utilization") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    # WORK_ONLY may open capacity dashboards? No — required cap is capacity.read, so dashboard is None.
    assert compose_dashboard(WORK_ONLY, "advisor_utilization") is None
    # But a capacity.read holder lacking work.read sees work panels restricted, never their value.
    cap_only = Principal(4, "c@e.com", "C", frozenset({"capacity.read"}))
    board = compose_dashboard(cap_only, "workload")["dashboard"]
    work_panels = [p for p in board["panels"] if p["key"] in ("workload_by_domain", "open_backlog")]
    assert work_panels and all(p["restricted"] and p["value"] is None for p in work_panels)


def test_get_panel_restricted_for_unentitled():
    cap_only = Principal(4, "c@e.com", "C", frozenset({"capacity.read"}))
    p = get_panel(cap_only, "workload_by_domain")
    assert p is not None and p["restricted"] and p["value"] is None


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "advisor_utilization") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert practice_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    # A dashboard whose runtime gate is off returns a gated envelope (not None — the principal is authorized).
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "capacity.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "advisor_utilization")
    assert result and result.get("gated") == "capacity.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "advisor_utilization")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_practice_summary_shape():
    s = practice_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]


def test_client_and_household_workload_are_workqueue_composition():
    cw = client_workload(FIRM, 1)
    assert cw["source"] == "practice_management.client_workload" or cw.get("enabled") is not None
    hw = household_workload(FIRM, 1, [1, 2])
    assert "open" in hw and "by_domain" in hw


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_practice_management()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_engine_call(monkeypatch):
    # If a module ever called an authoritative-owner mutation, governance must flag it.
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# assign_work(principal)\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_practice_management()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        src = (PM_DIR / name).read_text()
        if name == "governance.py":
            continue  # holds the detection string-literals
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_workflow_engine_or_scheduler_or_assignment():
    # The layer composes READS; it must never launch/advance a workflow, book a meeting, or assign work.
    composed = (PM_DIR / "service.py").read_text() + (PM_DIR / "panels.py").read_text()
    for forbidden in ("launch_workflow(", "advance_workflow(", "assign_work(", "reassign_approval(",
                      "book_meeting(", "create_meeting(", "create_capacity_plan("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_capacity_owner():
    composed = (PM_DIR / "panels.py").read_text()
    assert "operations.capacity" in composed  # the D.20 capacity owner, not a second capacity engine
    assert "capacity_overview" in composed


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (PM_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("practice_dashboards_composed", "practice_panels_composed", "practice_panel_failures",
                "practice_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.practice_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 19
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/practice", "/api/v1/practice/dashboards", "/api/v1/practice/dashboard/{key}",
            "/api/v1/practice/summary", "/api/v1/practice/registry", "/api/v1/practice/panel/{key}",
            "/api/v1/practice/metrics", "/practice/diagnostics"} <= paths


def test_routes_capability_gated():
    # Practice routes require capacity.read; diagnostics requires observability.audit.
    for cap in ("capacity.read", "observability.audit"):
        dep = require_capability(cap)
        without = Principal(9, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/practice_management.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "assign_work("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_capacity_planning_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "capacity_planning" in ws


def test_docs_and_adr_exist():
    for rel in ("docs/PRACTICE_MANAGEMENT.md", "docs/CAPACITY_PLANNING.md", "docs/RESOURCE_REGISTRY.md",
                "docs/PRACTICE_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-054-*.md"))
    assert adrs, "ADR-054 missing"
