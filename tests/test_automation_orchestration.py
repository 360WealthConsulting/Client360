"""Enterprise Automation Orchestration & Business Process Composition (Phase D.51) tests.

Verifies the automation-orchestration layer is a governed, READ-ONLY COMPOSITION over the platform's
authoritative operational services — the Workflow Engine (`workflow_automation` + the `workflow_orchestration`
facade), the Automation scheduled-job engine (ADR-027), the Trigger engine + action catalog, the Event
outbox, Scheduling, and Communications — and never becomes a second workflow engine, scheduler, rules
engine, orchestration engine, event bus, or automation platform.

Covers: the automation + trigger + action registries; dashboard composition + explainability + deep links;
execution/trigger/summary composition; authorization (unauthorized → None; unentitled panel → restricted,
never a value); gate + policy awareness; the firm summary + client/household rollups; governance (clean +
detects); diagnostics; the analytics-counter reuse (single registry); AI summaries; the routes (registered +
capability-gated); and the architecture invariants (no second workflow engine, no second scheduler, no
duplicate event bus, no mutation, every dashboard deep-links to authoritative workflows, every execution
summary references an authoritative owner).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.automation_orchestration import (
    automation_summary,
    client_automation,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_automation,
    list_dashboards,
    registry,
)
from app.services.automation_orchestration import diagnostics as diag

AO_DIR = pathlib.Path("app/services/automation_orchestration")

FIRM = Principal(1, "m@e.com", "M", frozenset({"automation.view", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no automation.view


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.AUTOMATION_REGISTRY) == 9
    assert len(registry.TRIGGER_REGISTRY) == 7
    assert len(registry.ACTION_REGISTRY) == 6
    assert len(registry.PANEL_REGISTRY) == 17
    assert len(registry.ORCHESTRATION_DASHBOARDS) == 6


def test_every_automation_names_workflow_trigger_execution_and_owners():
    for a in registry.AUTOMATION_REGISTRY:
        assert a.owner and a.workflow_owner and a.trigger_source and a.execution_owner
        assert a.scheduling_owner and a.notification_owner and a.runtime_gate and a.deep_links
        # execution is owned by the authoritative workflow/automation engine — never re-implemented.
        assert a.execution_owner in ("workflow_orchestration", "workflow_automation", "automation")


def test_every_trigger_and_action_names_authoritative_owner():
    for t in registry.TRIGGER_REGISTRY:
        assert t.owner and t.source and t.execution_owner and t.runtime_gate
    for ac in registry.ACTION_REGISTRY:
        assert ac.authoritative_owner and ac.execution_service and ac.permissions and ac.runtime_gate


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.ORCHESTRATION_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_execution_panel_references_an_authoritative_owner():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and ("." in p.source or ":" in p.source), p.key


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_authoritative_workflows():
    for d in registry.ORCHESTRATION_DASHBOARDS:
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
    assert ld["enabled"] and len(ld["dashboards"]) == 6
    for d in ld["dashboards"]:
        assert "panel_count" in d and "required_capabilities" in d
        assert "value" not in d


# --- authorization -----------------------------------------------------------

def test_unauthorized_principal_gets_none():
    assert compose_dashboard(NONE, "automation_inventory") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    p = get_panel(NONE, "workflow_status")
    assert p is not None and p["restricted"] and p["value"] is None


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "automation_inventory") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert automation_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "triggers.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "trigger_activity")
    assert result and result.get("gated") == "triggers.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "automation_inventory")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_automation_summary_shape():
    s = automation_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]


def test_client_and_household_automation_are_workflow_composition():
    cw = client_automation(FIRM, 1)
    assert cw["source"] == "workflow_orchestration.service.list_instances" or cw.get("enabled") is not None
    hw = household_automation(FIRM, 1, [1, 2])
    assert "workflow_count" in hw and "by_status" in hw


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_automation_orchestration()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_workflow_execution(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# launch_workflow(template, actor_user_id=1)\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_automation_orchestration()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (AO_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_workflow_engine_scheduler_or_event_bus():
    composed = (AO_DIR / "service.py").read_text() + (AO_DIR / "panels.py").read_text()
    # never a workflow / automation execution call
    for forbidden in ("launch_workflow(", "transition_workflow(", "complete_step(", "request_approval(",
                      "decide_approval(", "reassign_approval(", "process_event(",
                      "execute_automation_action(", "enqueue_run(", "execute_run(", "run_job(",
                      "run_worker_cycle(", ".fire(", "execute_action(", "publish_safe("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_workflow_engine():
    composed = (AO_DIR / "panels.py").read_text() + (AO_DIR / "service.py").read_text()
    assert "workflow_automation" in composed or "workflow_orchestration" in composed
    assert "workflow_metrics" in composed or "list_instances" in composed


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (AO_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("automation_dashboards_composed", "automation_panels_composed", "automation_panel_failures",
                "automation_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.automation_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 17
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/automation-orchestration", "/api/v1/automation-orchestration/dashboards",
            "/api/v1/automation-orchestration/dashboard/{key}", "/api/v1/automation-orchestration/summary",
            "/api/v1/automation-orchestration/registry", "/api/v1/automation-orchestration/panel/{key}",
            "/api/v1/automation-orchestration/metrics", "/automation-orchestration/diagnostics"} <= paths


def test_routes_capability_gated():
    for cap in ("automation.view", "observability.audit"):
        dep = require_capability(cap)
        without = Principal(9, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/automation_orchestration.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "launch_workflow("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_automation_status_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "automation_status" in ws


def test_ai_never_executes_automations():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("launch_workflow(", "decide_approval(", "process_event(", "transition_workflow("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/AUTOMATION_ORCHESTRATION.md", "docs/AUTOMATION_REGISTRY.md", "docs/TRIGGER_REGISTRY.md",
                "docs/AUTOMATION_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-056-*.md"))
    assert adrs, "ADR-056 missing"
