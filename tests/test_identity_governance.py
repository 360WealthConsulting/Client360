"""Enterprise Identity, Access Governance & Authorization Intelligence (Phase D.65) tests.

Verifies the identity layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative identity /
role / capability / authentication / authorization owners — the Identity service (`list_identity_data`),
Security RBAC (role & capability resolution, authorization policies), Security Authentication (providers), the
Policy engine (policy coverage), and Security Authorization (record-scope decisions) — and never becomes a
second identity provider / authentication service / authorization engine / RBAC system / directory / SSO
platform / policy engine / user-management platform.

Covers: the five registries; registry integrity + duplicate-key prevention (incl. cross-registry) +
configured-owner validation + honest not_configured; dashboard composition + panel explainability + deep links;
authorization; runtime gates; policy enforcement; per-panel restriction; the firm summary + record-scoped
client/household authorization-context sections that expose only the principal's own decision and never infer
authorization or leak identities; governance; diagnostics; analytics reuse; AI summarize-only; and the
architecture invariants (no second identity provider / RBAC / authorization engine, no persistence, no
mutation, no fabricated identities, no unauthorized data exposure). Enforces the honesty invariants: a
capability inventory is not a grant, a role definition is not an assignment, coverage is not certification.
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.identity_governance import (
    client_authorization_context,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_authorization_context,
    identity_summary,
    list_dashboards,
    registry,
)
from app.services.identity_governance import diagnostics as diag

IG_DIR = pathlib.Path("app/services/identity_governance")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"identity.manage", "analytics.executive", "observability.audit",
                            "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))     # no identity.manage/executive


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.IDENTITY_REGISTRY) == 8
    assert len(registry.ROLE_REGISTRY) == 7
    assert len(registry.CAPABILITY_REGISTRY) == 6
    assert len(registry.AUTHENTICATION_REGISTRY) == 7
    assert len(registry.AUTHORIZATION_REGISTRY) == 7
    assert len(registry.PANEL_REGISTRY) == 33
    assert len(registry.IDENTITY_DASHBOARDS) == 8


def test_no_duplicate_registry_keys_across_all_registries():
    keys = [e.key for e in registry._all_entries()]
    assert len(keys) == len(set(keys))
    pk = [p.key for p in registry.PANEL_REGISTRY]
    assert len(pk) == len(set(pk))
    dk = [d.key for d in registry.IDENTITY_DASHBOARDS]
    assert len(dk) == len(set(dk))


def test_every_configured_entry_has_authoritative_owner():
    for e in registry._all_entries():
        assert e.owner and e.capabilities and e.deep_links and e.runtime_gate, e.key
        if e.config_status == registry.CONFIGURED:
            assert e.owner != registry.NOT_CONFIGURED, e.key


def test_not_configured_domains_reported_honestly():
    nc = set(registry.not_configured_domains())
    assert {"identity_lifecycle", "service_accounts", "account_provisioning", "birthright_roles",
            "role_certification", "segregation_of_duties", "entitlement_review", "sso_providers",
            "mfa_enforcement", "api_authentication", "password_management",
            "privileged_access_management", "authorization_certification"} == nc
    assert len(nc) == 13


def test_master_gates_distinct_and_present():
    for g in ("identity_governance.enabled", "authentication_landscape.enabled",
              "authorization_landscape.enabled", "identity_ai_summary.enabled"):
        assert g in gate.GATES
    assert "security.enabled" not in gate.GATES


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.IDENTITY_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        assert board["capability_inventory_is_not_a_grant"] is True
        assert board["role_definition_is_not_an_assignment"] is True
        assert board["governance_coverage_not_certification"] is True
        assert "not_configured_domains" in board
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_every_panel_has_owner_source_deep_link_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES


def test_derived_executive_posture_labeled_not_fabricated():
    p = get_panel(FIRM, "executive_identity_posture")
    assert p["derived"] is True
    assert p["value"]["governance_coverage_not_certification"] is True
    assert p["value"]["role_definition_is_not_an_assignment"] is True


def test_capability_inventory_disclaims_grant():
    p = get_panel(FIRM, "capability_inventory")
    assert p["value"]["capability_inventory_is_not_a_grant"] is True


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
    assert compose_dashboard(NONE, "identity_overview") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    # an analytics.executive principal without identity.manage sees an identity.manage panel restricted.
    exec_only = Principal(4, "e@e.com", "E", frozenset({"analytics.executive"}))
    p = get_panel(exec_only, "user_directory_coverage")
    assert p is not None and p["restricted"] and p["value"] is None and p["available"] is False


def test_not_configured_panel_is_available_false():
    for key in ("sso_availability", "mfa_enforcement_availability", "api_authentication_availability",
                "password_management_availability", "pam_availability", "access_review_availability"):
        p = get_panel(FIRM, key)
        assert p is not None and p["config_status"] == registry.NOT_CONFIGURED and p["available"] is False


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "identity_overview") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert identity_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate",
                        lambda n: False if n == "authentication_landscape.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "authentication_landscape")
    assert result and result.get("gated") == "authentication_landscape.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "identity_overview")
    assert result and result.get("denied") == "policy"


# --- summary + record-scoped sections ----------------------------------------

def test_identity_summary_governance_coverage_not_certification():
    s = identity_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s
    assert s["governance_coverage_not_certification"] is True
    assert s["capability_inventory_is_not_a_grant"] is True
    assert s["role_definition_is_not_an_assignment"] is True
    assert "not_configured_domains" in s


def test_record_scoped_authorization_context_exposes_only_own_decision():
    cd = client_authorization_context(FIRM, 1)
    assert cd["internal_identities_exposed"] is False and cd["privileged_roles_exposed"] is False
    assert cd["permission_map_exposed"] is False and cd["authorization_inferred"] is False
    assert isinstance(cd["signals"].get("principal_in_scope"), bool)
    hd = household_authorization_context(FIRM, 1, [1, 2])
    assert hd["authorization_inferred"] is False


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_identity_governance()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# assign_role(user_id=1, role_id=1)\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_identity_governance()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "__init__.py"):
        src = (IG_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit(",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_identity_or_rbac_engine():
    composed = (IG_DIR / "service.py").read_text() + (IG_DIR / "panels.py").read_text()
    for forbidden in ("invite_user(", "set_user_status(", "assign_role(", "compose_role(",
                      "register_policy(", "register_provider(", "create_session(", "revoke_session(",
                      "assign_record(", "write_audit("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_owners():
    composed = (IG_DIR / "panels.py").read_text() + (IG_DIR / "service.py").read_text()
    assert "list_identity_data" in composed
    assert "list_providers" in composed
    assert "record_in_scope" in composed


def test_no_fabricated_identity():
    # every value computed by the layer (identity_governance.compose) must be labeled derived.
    for p in registry.PANEL_REGISTRY:
        if p.source.startswith("identity_governance.compose"):
            assert p.derived, p.key
    p = get_panel(FIRM, "access_governance_readiness")
    assert p["value"]["governance_coverage_not_certification"] is True
    assert p["value"]["coverage_is_not_certification"] is True


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (IG_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


def test_no_secret_exposure_in_panels():
    # guard against ACTUAL exposure (field access / env reads), not docstring mentions that disclaim it.
    src = (IG_DIR / "panels.py").read_text()
    for forbidden in ("os.getenv", "os.environ", "session_hash", "token=",
                      '"auth_subject"', "'auth_subject'", '"password"', "'password'",
                      '"email"', "'email'", '"normalized_email"'):
        assert forbidden not in src, forbidden


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("identity_dashboards_composed", "identity_panels_composed", "identity_panel_failures",
                "identity_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.identity_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 33
    assert d["not_configured_domains"] == 13
    assert d["governance"]["ok"] is True
    assert d["capability_inventory_is_not_a_grant"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/identity-governance", "/api/v1/identity-governance/dashboards",
            "/api/v1/identity-governance/dashboard/{key}", "/api/v1/identity-governance/summary",
            "/api/v1/identity-governance/registry", "/api/v1/identity-governance/panel/{key}",
            "/api/v1/identity-governance/metrics", "/identity-governance/diagnostics"} <= paths


def test_routes_capability_gated():
    dep = require_any_capability("identity.manage", "analytics.executive")
    without = Principal(9, "no@e.com", "No", frozenset({"record.read_all"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=without)
    assert ei.value.status_code == 403
    assert dep(principal=Principal(10, "i@e.com", "I", frozenset({"identity.manage"}))) is not None
    assert dep(principal=Principal(11, "e@e.com", "E", frozenset({"analytics.executive"}))) is not None


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/identity_governance.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit(", "assign_role(",
                      "create_session("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_identity_access_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "identity_access" in ws


def test_executive_dashboard_reuses_existing_widgets():
    from app.services.executive_intelligence.registry import DASHBOARD_REGISTRY, WIDGET_REGISTRY
    d = next(d for d in DASHBOARD_REGISTRY if d.key == "enterprise_identity_access_governance")
    widget_keys = {w.key for w in WIDGET_REGISTRY}
    assert all(w in widget_keys for w in d.widgets)   # no new widget introduced
    assert len(WIDGET_REGISTRY) == 14


def test_client360_and_household_sections_registered():
    from app.services.client360.household import _SECTION_BUILDERS, HOUSEHOLD_SECTIONS
    from app.services.client360.registry import SECTIONS
    assert any(s.key == "authorization_context" for s in SECTIONS)
    assert ("authorization_context", "observability.view") in HOUSEHOLD_SECTIONS
    assert "authorization_context" in _SECTION_BUILDERS


def test_ai_never_authenticates_or_authorizes():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("authenticate(", "assign_role(", "grant(", "register_provider(", "create_session("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/ENTERPRISE_IDENTITY_GOVERNANCE.md", "docs/IDENTITY_REGISTRY.md",
                "docs/ROLE_REGISTRY.md", "docs/AUTHORIZATION_REGISTRY.md", "docs/IDENTITY_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-070-*.md"))
    assert adrs, "ADR-070 missing"
