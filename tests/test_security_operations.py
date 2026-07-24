"""Enterprise Security Operations, Identity Governance & Platform Security Intelligence (Phase D.54) tests.

Verifies the security-operations layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative
security owners — the Security metadata domain (service / providers / policies / incidents), the Identity
owner (`identity.list_identity_data`), the RBAC foundation, and the hash-chain audit log (`audit_export`) —
and never becomes a second IAM platform, identity provider, RBAC engine, authentication system, authorization
engine, MFA provider, audit-logging platform, or SIEM.

Covers: the identity + security registries; dashboard composition + explainability + deep links;
authentication/authorization/MFA/session/audit composition; authorization (unauthorized → None; unentitled
panel → restricted, never a value); gate + policy awareness; the firm summary + client/household rollups;
governance (clean + detects); diagnostics; the analytics-counter reuse (single registry); AI summaries; the
routes (registered + capability-gated); and the architecture invariants (no second IAM / RBAC / MFA / audit
platform, no duplicated identities, no mutation, every dashboard deep-links to authoritative security owners,
every summary references an authoritative owner).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.security_operations import (
    client_security,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_security,
    list_dashboards,
    registry,
    security_summary,
)
from app.services.security_operations import diagnostics as diag

SO_DIR = pathlib.Path("app/services/security_operations")

FIRM = Principal(1, "m@e.com", "M", frozenset({"security.view", "audit.read", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no security.view


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.IDENTITY_REGISTRY) == 6
    assert len(registry.SECURITY_REGISTRY) == 6
    assert len(registry.PANEL_REGISTRY) == 21
    assert len(registry.SECURITY_DASHBOARDS) == 7


def test_every_identity_class_names_authoritative_authentication_and_authorization_owner():
    for i in registry.IDENTITY_REGISTRY:
        assert i.authoritative_owner and i.authentication_owner and i.authorization_owner
        assert i.runtime_gate and i.deep_links


def test_every_security_domain_names_owner_provider_and_monitoring():
    for s in registry.SECURITY_REGISTRY:
        assert s.category and s.authoritative_owner and s.provider_owner and s.monitoring_owner
        assert s.runtime_gate and s.deep_links


def test_every_panel_registered_with_owner_source_deep_link_and_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES
    for d in registry.SECURITY_DASHBOARDS:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.panels
        assert d.required_capabilities and d.governing_services
        for pkey in d.panels:
            assert registry.panel_registered(pkey)


def test_every_summary_references_an_authoritative_owner():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and ("." in p.source or ":" in p.source), p.key


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_security_owners():
    for d in registry.SECURITY_DASHBOARDS:
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
    assert compose_dashboard(NONE, "authentication") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    p = get_panel(NONE, "security_overview")
    assert p is not None and p["restricted"] and p["value"] is None


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "authentication") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert security_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "audit.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "audit")
    assert result and result.get("gated") == "audit.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "authentication")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_security_summary_shape():
    s = security_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s and "dashboards" in s
    assert s["governing_services"]


def test_client_and_household_security_are_authorization_composition():
    cw = client_security(FIRM, 1)
    assert cw["source"] == "security.object_security.resolve_assignments" or cw.get("enabled") is not None
    hw = household_security(FIRM, 1, [1, 2])
    assert "assigned_users" in hw


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_security_operations()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_auth_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# create_session(1)\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_security_operations()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (SO_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_iam_rbac_mfa_or_audit_engine():
    composed = (SO_DIR / "service.py").read_text() + (SO_DIR / "panels.py").read_text()
    for forbidden in ("authenticate_claims(", "create_session(", "revoke_session(", "invite_user(",
                      "set_user_status(", "assign_role(", "compose_role(", "write_audit_event(",
                      "bootstrap_administrator(", "resolve_principal("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_security_owner():
    composed = (SO_DIR / "panels.py").read_text() + (SO_DIR / "service.py").read_text()
    assert "security." in composed and "identity" in composed  # the authoritative owners, not a second IAM
    assert "overview_metrics" in composed or "list_identity_data" in composed


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (SO_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("security_ops_dashboards_composed", "security_ops_panels_composed",
                "security_ops_panel_failures", "security_ops_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.security_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 21
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/security-operations", "/api/v1/security-operations/dashboards",
            "/api/v1/security-operations/dashboard/{key}", "/api/v1/security-operations/summary",
            "/api/v1/security-operations/registry", "/api/v1/security-operations/panel/{key}",
            "/api/v1/security-operations/metrics", "/security-operations/diagnostics"} <= paths


def test_routes_capability_gated():
    for cap in ("security.view", "observability.audit"):
        dep = require_capability(cap)
        without = Principal(9, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/security_operations.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "create_session("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_security_operations_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "security_operations" in ws


def test_ai_never_authenticates_or_elevates():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("create_session(", "assign_role(", "write_audit_event(", "authenticate_claims("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/SECURITY_OPERATIONS.md", "docs/IDENTITY_REGISTRY.md", "docs/SECURITY_REGISTRY.md",
                "docs/SECURITY_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-059-*.md"))
    assert adrs, "ADR-059 missing"
