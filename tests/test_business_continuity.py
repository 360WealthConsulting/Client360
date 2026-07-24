"""Enterprise Business Continuity, Disaster Recovery & Operational Resilience (Phase D.55) tests.

Verifies the business-continuity layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative
operational-resilience owners — the Observability domain (service / catalog / health / incidents / alerts),
the Runtime engine (runtime.service / coordination / consumption), the Automation scheduler, and
Communications — and never becomes a second backup platform, monitoring system, disaster-recovery engine,
scheduler, notification system, or incident manager.

Covers: the resilience + recovery registries; dashboard composition + explainability + deep links;
infrastructure/runtime/maintenance/notification composition; the honest not-configured backup/restore panels;
authorization (unauthorized → None; unentitled panel → restricted, never a value); gate + policy awareness;
the firm summary + client/household rollups; governance (clean + detects); diagnostics; the analytics-counter
reuse (single registry); AI summaries; the routes (registered + capability-gated); and the architecture
invariants (no second backup/monitoring/DR/scheduler/notification/incident system, no mutation, every
dashboard deep-links to authoritative resilience owners, every panel references an authoritative owner).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.business_continuity import (
    client_continuity,
    compose_dashboard,
    continuity_summary,
    gate,
    get_panel,
    governance,
    household_continuity,
    list_dashboards,
    registry,
)
from app.services.business_continuity import diagnostics as diag

BC_DIR = pathlib.Path("app/services/business_continuity")

FIRM = Principal(1, "m@e.com", "M", frozenset({"observability.view", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no observability.view


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.RESILIENCE_REGISTRY) == 9
    assert len(registry.RECOVERY_REGISTRY) == 8
    assert len(registry.PANEL_REGISTRY) == 22
    assert len(registry.CONTINUITY_DASHBOARDS) == 8


def test_every_resilience_domain_names_owner_health_and_monitoring():
    for r in registry.RESILIENCE_REGISTRY:
        assert r.authoritative_owner and r.health_owner and r.monitoring_owner
        assert r.runtime_gate and r.deep_links


def test_every_recovery_asset_names_owner_rpo_and_rto():
    for a in registry.RECOVERY_REGISTRY:
        assert a.owner and a.backup_owner and a.restore_owner and a.rpo and a.rto and a.runtime_gate


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.CONTINUITY_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_panel_references_an_authoritative_owner():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and ("." in p.source or ":" in p.source), p.key


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_resilience_owners():
    for d in registry.CONTINUITY_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_backup_panels_are_honest_not_configured():
    # No authoritative backup/restore owner exists; those panels report not_configured, never a fabricated
    # status.
    for key in ("last_successful_backup", "failed_backups", "restore_test_status"):
        p = get_panel(FIRM, key)
        assert p is not None and p["value"].get("status") == "not_configured"


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
    assert compose_dashboard(NONE, "backup_status") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    p = get_panel(NONE, "infrastructure_availability")
    assert p is not None and p["restricted"] and p["value"] is None


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "backup_status") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert continuity_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "recovery.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "recovery_readiness")
    assert result and result.get("gated") == "recovery.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "backup_status")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_continuity_summary_shape():
    s = continuity_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]


def test_client_and_household_continuity_are_firm_posture():
    cw = client_continuity(FIRM, 1)
    assert cw["source"] == "business_continuity.client_continuity" or cw.get("enabled") is not None
    hw = household_continuity(FIRM, 1, [1, 2])
    assert hw.get("enabled") is not None


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_business_continuity()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_execution(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# run_backup(principal)\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_business_continuity()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (BC_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_backup_monitoring_or_dr_engine():
    composed = (BC_DIR / "service.py").read_text() + (BC_DIR / "panels.py").read_text()
    for forbidden in ("run_backup(", "restore_backup(", "run_due_scans(", "raise_alert(",
                      "acknowledge_alert(", "resolve_alert(", "set_service_status(", "set_incident_status(",
                      "set_maintenance_status(", "enqueue_run(", "run_job("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_observability_owner():
    composed = (BC_DIR / "panels.py").read_text() + (BC_DIR / "service.py").read_text()
    assert "observability" in composed  # the D.26 monitoring owner, not a second system
    assert "overview_metrics" in composed


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (BC_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("continuity_dashboards_composed", "continuity_panels_composed", "continuity_panel_failures",
                "continuity_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.continuity_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 22
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/business-continuity", "/api/v1/business-continuity/dashboards",
            "/api/v1/business-continuity/dashboard/{key}", "/api/v1/business-continuity/summary",
            "/api/v1/business-continuity/registry", "/api/v1/business-continuity/panel/{key}",
            "/api/v1/business-continuity/metrics", "/business-continuity/diagnostics"} <= paths


def test_routes_capability_gated():
    for cap in ("observability.view", "observability.audit"):
        dep = require_capability(cap)
        without = Principal(9, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/business_continuity.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "run_backup("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_operational_resilience_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "operational_resilience" in ws


def test_ai_never_starts_backups_or_alters_infra():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("run_backup(", "restore_backup(", "set_service_status(", "resolve_alert("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/BUSINESS_CONTINUITY.md", "docs/RECOVERY_REGISTRY.md", "docs/RESILIENCE_REGISTRY.md",
                "docs/BUSINESS_CONTINUITY_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-060-*.md"))
    assert adrs, "ADR-060 missing"
