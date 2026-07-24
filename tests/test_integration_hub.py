"""Enterprise Integration Hub & Connected Platform Governance (Phase D.53) tests.

Verifies the integration-hub layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative
integration owners — the D.24 Integration Platform (service / sync / connectors / webhooks / api / events),
the Event outbox + Event registry, and the M365 / insurance / signature connectors — and never becomes a
second integration platform, ESB, API gateway, synchronization engine, webhook processor, message broker, or
event bus.

Covers: the integration + connector registries; dashboard composition + explainability + deep links;
sync/webhook/auth composition; authorization (unauthorized → None; unentitled panel → restricted, never a
value); gate + policy awareness; the firm summary + client/household rollups; governance (clean + detects);
diagnostics; the analytics-counter reuse (single registry); AI summaries; the routes (registered +
capability-gated); and the architecture invariants (no second integration platform / synchronization engine
/ webhook processor / API gateway, no mutation, no outbound HTTP, every dashboard deep-links to authoritative
connector owners, every synchronization summary references an authoritative owner).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.integration_hub import (
    client_integrations,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_integrations,
    integration_summary,
    list_dashboards,
    registry,
)
from app.services.integration_hub import diagnostics as diag

IH_DIR = pathlib.Path("app/services/integration_hub")

FIRM = Principal(1, "m@e.com", "M", frozenset({"integration.view", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no integration.view


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.INTEGRATION_REGISTRY) == 18
    assert len(registry.CONNECTOR_REGISTRY) == 9
    assert len(registry.PANEL_REGISTRY) == 19
    assert len(registry.INTEGRATION_DASHBOARDS) == 7


def test_every_integration_names_authoritative_connection_auth_and_sync_owner():
    for i in registry.INTEGRATION_REGISTRY:
        assert i.authoritative_owner and i.connection_owner and i.authentication_owner
        assert i.synchronization_owner and i.provider_type and i.runtime_gate and i.deep_links


def test_every_connector_names_protocol_auth_and_owners():
    for c in registry.CONNECTOR_REGISTRY:
        assert c.protocol and c.authentication and c.polling_owner and c.webhook_owner
        assert c.retry_owner and c.monitoring_owner and c.runtime_gate


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.INTEGRATION_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_sync_summary_references_an_authoritative_owner():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and ("." in p.source or ":" in p.source), p.key


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_connector_owners():
    for d in registry.INTEGRATION_DASHBOARDS:
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
    assert compose_dashboard(NONE, "integrations") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    p = get_panel(NONE, "integration_overview")
    assert p is not None and p["restricted"] and p["value"] is None


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "integrations") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert integration_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "synchronization.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "synchronization")
    assert result and result.get("gated") == "synchronization.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "integrations")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_integration_summary_shape():
    s = integration_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]


def test_client_and_household_integrations_are_lineage_composition():
    cw = client_integrations(FIRM, 1)
    assert cw["source"] == "governance.mdm.person_lineage" or cw.get("enabled") is not None
    hw = household_integrations(FIRM, 1, [1, 2])
    assert "source_systems" in hw


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_integration_hub()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_sync_trigger(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# run_sync(principal, 1)\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_integration_hub()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


def test_governance_detects_outbound_http(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "panels.py":
            s = s + "\nimport httpx\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_integration_hub()
    assert any(f["type"] == "outbound_http_client" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (IH_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_integration_platform_sync_or_webhook_engine():
    composed = (IH_DIR / "service.py").read_text() + (IH_DIR / "panels.py").read_text()
    for forbidden in ("run_sync(", "run_due_syncs(", "create_connector(", "create_endpoint(",
                      "record_delivery(", "verify_endpoint(", "create_api_client(", "invoke_port(",
                      "get_microsoft_access_token(", "apply_signature_event(", "publish_safe("):
        assert forbidden not in composed, forbidden


def test_no_outbound_http_client():
    for name in ("service.py", "panels.py", "model.py", "registry.py"):
        src = (IH_DIR / name).read_text()
        assert not re.search(r"\bhttpx\b|\brequests\.(get|post|put|delete)\b|aiohttp", src), name


def test_composes_the_authoritative_integration_platform():
    composed = (IH_DIR / "panels.py").read_text() + (IH_DIR / "service.py").read_text()
    assert "integration." in composed  # the D.24 integration owner, not a second platform
    assert "overview_metrics" in composed or "list_connectors" in composed


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (IH_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("integration_dashboards_composed", "integration_panels_composed",
                "integration_panel_failures", "integration_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.integration_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 19
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/integration-hub", "/api/v1/integration-hub/dashboards",
            "/api/v1/integration-hub/dashboard/{key}", "/api/v1/integration-hub/summary",
            "/api/v1/integration-hub/registry", "/api/v1/integration-hub/panel/{key}",
            "/api/v1/integration-hub/metrics", "/integration-hub/diagnostics"} <= paths


def test_routes_capability_gated():
    for cap in ("integration.view", "observability.audit"):
        dep = require_capability(cap)
        without = Principal(9, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/integration_hub.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "run_sync("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_integration_health_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "integration_health" in ws


def test_ai_never_reconnects_or_invokes():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("run_sync(", "get_microsoft_access_token(", "apply_signature_event(", "invoke_port("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/INTEGRATION_HUB.md", "docs/INTEGRATION_REGISTRY.md", "docs/CONNECTOR_REGISTRY.md",
                "docs/INTEGRATION_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-058-*.md"))
    assert adrs, "ADR-058 missing"
