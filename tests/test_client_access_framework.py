"""360Plus Client Feature & Access Control — comprehensive + adversarial tests.

Covers the 20 required scenarios (entitlement/default behavior, overrides, firm-ceiling precedence,
INTERNAL_ONLY/BETA, fail-closed unknown feature, direct-API enforcement, client isolation, staff RBAC,
audit, status semantics) plus adversarial bypass attempts. Service-level tests use arbitrary subject ids
(the control tables key on subject_type+subject_id, no FK to the entity), while route/portal tests use
real households + portal accounts.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select

from app.db import (
    audit_events,
    client_feature_overrides,
    client_product_entitlements,
    client_status,
    engine,
    firm_feature_controls,
    households,
    people,
)
from app.security.models import Principal
from app.services.features import portal_gate
from app.services.features import service as feat
from app.services.features.enforcement import require_client_feature
from tests._portal_util import seed_portal_account, seed_staff_user

STAFF_CAPS = frozenset({"client.read", "client.write", "record.read_all", "record.write_all",
                        "configuration.view", "configuration.admin"})


@pytest.fixture(autouse=True)
def _isolate_controls():
    """The caf01 control tables are global state; reset them before each test so firm-state / override
    changes in one test can never leak into another (and firm defaults come from the code catalog)."""
    for t in (firm_feature_controls, client_feature_overrides, client_product_entitlements, client_status):
        with engine.begin() as c:
            c.execute(delete(t))
    yield


def _sid():
    """A fresh, isolated subject id for service-level precedence tests."""
    return int(uuid.uuid4().int % 1_000_000_000)


def _household():
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"HH {sfx}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"P {sfx}", active=True)
                        .returning(people.c.id)).scalar_one()
    return hid, pid


def _staff(caps=STAFF_CAPS):
    return Principal(seed_staff_user(), "staff@e.test", "Staff", frozenset(caps))


def _audits(action, entity_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            (audit_events.c.action == action) & (audit_events.c.entity_id == str(entity_id))))


def _allowed(subject_type, subject_id, feature, actor="client"):
    return feat.effective_access(subject_type, subject_id, feature, actor=actor).allowed


def _reason(subject_type, subject_id, feature, actor="client"):
    return feat.effective_access(subject_type, subject_id, feature, actor=actor).reason


def _fake_portal_request(principal):
    return SimpleNamespace(state=SimpleNamespace(portal_principal=principal))


# 1 — Core entitlement/default behavior --------------------------------------
def test_core_default_allowed():
    hid = _sid()
    assert _allowed("household", hid, "secure_messaging")           # core baseline + firm enabled
    assert _reason("household", hid, "secure_messaging") == "product_default"


# 2 — Wealth entitlement behavior --------------------------------------------
def test_wealth_entitlement_behavior():
    hid, _ = _household()
    staff = _staff()
    feat.set_firm_state("wealth_dashboard", "enabled", actor_user_id=staff.user_id)   # admin turns it on
    assert not _allowed("household", hid, "wealth_dashboard")        # no entitlement yet → denied
    assert _reason("household", hid, "wealth_dashboard") == "entitlement_required"
    feat.grant_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    assert _allowed("household", hid, "wealth_dashboard")            # entitled → allowed
    feat.revoke_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "wealth_dashboard")        # revoked → denied again


# 3 — Business entitlement behavior ------------------------------------------
def test_business_entitlement_behavior():
    oid = _sid()
    staff = _staff()
    feat.set_firm_state("quickbooks", "enabled", actor_user_id=staff.user_id)
    assert not _allowed("organization", oid, "quickbooks")
    feat.grant_entitlement("organization", oid, "business", actor_user_id=staff.user_id)
    assert _allowed("organization", oid, "quickbooks")


# 4 — Wealth + Business combination ------------------------------------------
def test_wealth_and_business_combination():
    hid, oid = _sid(), _sid()
    staff = _staff()
    feat.set_firm_state("financial_planning", "enabled", actor_user_id=staff.user_id)
    feat.set_firm_state("payroll", "enabled", actor_user_id=staff.user_id)
    feat.grant_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    feat.grant_entitlement("organization", oid, "business", actor_user_id=staff.user_id)
    assert _allowed("household", hid, "financial_planning")
    assert _allowed("organization", oid, "payroll")
    # cross-tier isolation: the household is NOT business-entitled, the org is NOT wealth-entitled
    assert not _allowed("household", hid, "payroll")
    assert not _allowed("organization", oid, "financial_planning")


# 5 — Per-client ENABLE override (within an entitled product) ----------------
def test_per_client_enable_override_requires_entitlement():
    hid = _sid()
    staff = _staff()
    feat.set_firm_state("monte_carlo", "enabled", actor_user_id=staff.user_id)
    feat.set_override("household", hid, "monte_carlo", "enable", actor_user_id=staff.user_id)
    # Decision #1: ENABLE is NOT a shadow entitlement — no Wealth ⇒ denied.
    assert not _allowed("household", hid, "monte_carlo")
    assert _reason("household", hid, "monte_carlo") == "entitlement_required"
    # Grant Wealth first, THEN the ENABLE override takes effect.
    feat.grant_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    assert _allowed("household", hid, "monte_carlo")
    assert _reason("household", hid, "monte_carlo") == "client_enabled"


# 6 — Per-client DISABLE override --------------------------------------------
def test_per_client_disable_override():
    hid = _sid()
    staff = _staff()
    assert _allowed("household", hid, "secure_messaging")          # core default on
    feat.set_override("household", hid, "secure_messaging", "disable", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "secure_messaging")      # DISABLE beats product default
    assert _reason("household", hid, "secure_messaging") == "client_disabled"


# 7 — INHERIT behavior --------------------------------------------------------
def test_inherit_clears_override():
    hid = _sid()
    staff = _staff()
    feat.set_override("household", hid, "secure_messaging", "disable", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "secure_messaging")
    feat.set_override("household", hid, "secure_messaging", "inherit", actor_user_id=staff.user_id)
    assert _allowed("household", hid, "secure_messaging")          # back to product default
    assert feat.override("household", hid, "secure_messaging") is None    # row removed


# 8 — Global DISABLED beats client ENABLE ------------------------------------
def test_global_disabled_beats_client_enable():
    hid = _sid()
    staff = _staff()
    feat.set_override("household", hid, "document_download", "enable", actor_user_id=staff.user_id)
    assert _allowed("household", hid, "document_download")
    feat.set_firm_state("document_download", "disabled", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "document_download")     # firm ceiling wins over override
    assert _reason("household", hid, "document_download") == "firm_disabled"


# 9 — INTERNAL_ONLY cannot be accessed by clients ----------------------------
def test_internal_only_denied_to_clients():
    hid = _sid()
    staff = _staff()
    feat.grant_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    feat.set_firm_state("tax_planning", "internal_only", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "tax_planning", actor="client")
    assert _reason("household", hid, "tax_planning", actor="client") == "internal_only"
    assert _allowed("household", hid, "tax_planning", actor="staff")     # internal actor may see it


# 10 — BETA only reaches explicitly eligible clients -------------------------
def test_beta_reaches_only_enabled_clients():
    hid = _sid()
    staff = _staff()
    feat.grant_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    feat.set_firm_state("retirement_planning", "beta", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "retirement_planning")   # beta not auto-exposed
    assert _reason("household", hid, "retirement_planning") == "beta_not_eligible"
    feat.set_override("household", hid, "retirement_planning", "enable", actor_user_id=staff.user_id)
    assert _allowed("household", hid, "retirement_planning")       # explicit enable = beta-eligible


# 11 — Unknown feature fails closed ------------------------------------------
def test_unknown_feature_fails_closed():
    hid = _sid()
    d = feat.effective_access("household", hid, "totally_made_up_feature")
    assert d.allowed is False and d.reason == "unknown_feature"


# 12 — Direct URL/API access denied when feature disabled --------------------
def test_direct_api_denied_when_feature_disabled():
    _, principal, _, hid = seed_portal_account(seed_staff_user())
    dep = require_client_feature("document_download")
    assert dep(_fake_portal_request(principal)) is principal         # allowed by default (core baseline)
    staff = _staff()
    feat.set_override("household", hid, "document_download", "disable", actor_user_id=staff.user_id)
    with pytest.raises(HTTPException) as ei:
        dep(_fake_portal_request(principal))                         # direct call denied server-side
    assert ei.value.status_code == 403
    # and via a firm-wide disable
    feat.set_override("household", hid, "document_download", "inherit", actor_user_id=staff.user_id)
    feat.set_firm_state("document_download", "disabled", actor_user_id=staff.user_id)
    with pytest.raises(HTTPException) as ei:
        dep(_fake_portal_request(principal))
    assert ei.value.status_code == 403
    feat.set_firm_state("document_download", "enabled", actor_user_id=staff.user_id)   # restore for others


# 13 — Client A's controls cannot affect Client B ----------------------------
def test_client_isolation():
    a, b = _sid(), _sid()
    staff = _staff()
    feat.set_override("household", a, "secure_messaging", "disable", actor_user_id=staff.user_id)
    assert not _allowed("household", a, "secure_messaging")
    assert _allowed("household", b, "secure_messaging")             # B unaffected


# 14 — Unauthorized staff cannot modify controls -----------------------------
def test_unauthorized_staff_cannot_modify():
    from app.routes.client_access import set_client_feature_override, set_feature_control
    from app.security.dependencies import require_capability
    # No client.write → the capability dependency itself denies.
    without = Principal(seed_staff_user(), "x@e.test", "X", frozenset({"client.read"}))
    with pytest.raises(HTTPException) as ei:
        require_capability("client.write")(principal=without)
    assert ei.value.status_code == 403
    # No configuration.admin → cannot set firm state.
    with pytest.raises(HTTPException) as ei:
        require_capability("configuration.admin")(principal=without)
    assert ei.value.status_code == 403
    # Has client.write but NO record scope → the route's scope guard denies (403), nothing written.
    hid, _ = _household()
    unscoped = Principal(seed_staff_user(), "u@e.test", "U", frozenset({"client.write"}))
    req = SimpleNamespace(state=SimpleNamespace(request_id="t"))
    with pytest.raises(HTTPException) as ei:
        set_client_feature_override("household", hid, req, feature_key="secure_messaging",
                                    state="disable", principal=unscoped)
    assert ei.value.status_code == 403
    assert feat.override("household", hid, "secure_messaging") is None
    assert set_feature_control  # imported (route exists)


# 15 — Authorized staff can modify permitted controls ------------------------
def test_authorized_staff_can_modify():
    from app.routes.client_access import set_client_feature_override
    hid, _ = _household()
    staff = _staff()
    req = SimpleNamespace(state=SimpleNamespace(request_id="t"))
    resp = set_client_feature_override("household", hid, req, feature_key="secure_messaging",
                                       state="disable", principal=staff)
    assert resp.status_code == 303
    assert feat.override("household", hid, "secure_messaging") == "disable"


# 16 — Audit events created for changes --------------------------------------
def test_audit_events_created():
    hid = _sid()
    staff = _staff()
    feat.set_status("household", hid, "needs_review", "prospect", actor_user_id=staff.user_id)
    feat.grant_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    feat.set_override("household", hid, "monte_carlo", "enable", actor_user_id=staff.user_id)
    feat.set_firm_state("monte_carlo", "beta", actor_user_id=staff.user_id)
    assert _audits("client.status.changed", hid) >= 1
    assert _audits("client.entitlement.granted", hid) >= 1
    assert _audits("client.feature.override_set", hid) >= 1
    assert _audits("firm.feature.state_changed", "monte_carlo") >= 1


# 17 — Existing behavior does not regress ------------------------------------
def test_core_client_can_default_no_regression():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    # A normal client keeps Core access with no configuration at all.
    assert feat.client_can(principal, "document_download")
    assert feat.client_can(principal, "secure_messaging")
    assert feat.client_can(principal, "document_upload")


# 18 — Status alone does not grant product access ----------------------------
def test_status_does_not_grant_access():
    hid = _sid()
    staff = _staff()
    feat.set_firm_state("schwab_accounts", "enabled", actor_user_id=staff.user_id)
    feat.set_status("household", hid, "active", actor_user_id=staff.user_id)   # active, but no entitlement
    assert not _allowed("household", hid, "schwab_accounts")        # status active ≠ wealth entitlement
    assert _allowed("household", hid, "secure_messaging")           # core unaffected by status


# 19 — Inactive/Historical handling is safe ----------------------------------
def test_inactive_status_is_safe():
    hid = _sid()
    staff = _staff()
    before_core = _allowed("household", hid, "secure_messaging")
    before_wealth = _allowed("household", hid, "wealth_dashboard")
    feat.set_status("household", hid, "inactive", "archive", actor_user_id=staff.user_id)
    assert _allowed("household", hid, "secure_messaging") == before_core       # unchanged
    assert _allowed("household", hid, "wealth_dashboard") == before_wealth     # still denied, no accidental grant


# 20 — Needs Review handling is safe -----------------------------------------
def test_needs_review_status_is_safe():
    hid = _sid()
    staff = _staff()
    feat.set_status("household", hid, "needs_review", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "schwab_accounts")        # no accidental grant
    assert _allowed("household", hid, "secure_messaging")           # core still works


# --- adversarial bypass attempts --------------------------------------------

def test_adversarial_override_cannot_bypass_firm_disabled():
    hid = _sid()
    staff = _staff()
    feat.set_firm_state("document_download", "disabled", actor_user_id=staff.user_id)
    feat.set_override("household", hid, "document_download", "enable", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "document_download")      # enable cannot re-open a firm-disabled feature
    feat.set_firm_state("document_download", "enabled", actor_user_id=staff.user_id)


def test_adversarial_override_cannot_bypass_internal_only():
    hid = _sid()
    staff = _staff()
    feat.grant_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    feat.set_firm_state("tax_planning", "internal_only", actor_user_id=staff.user_id)
    feat.set_override("household", hid, "tax_planning", "enable", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "tax_planning", actor="client")   # never exposed to a client


def test_adversarial_client_cannot_use_unknown_or_unentitled_feature():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    assert feat.client_can(principal, "no_such_feature") is False          # fail-closed
    # A wealth feature is denied to a normal (core-only) client even by direct check.
    assert feat.client_can(principal, "schwab_accounts") is False


def test_adversarial_staff_cannot_grant_invalid_values():
    hid = _sid()
    staff = _staff()
    with pytest.raises(ValueError):
        feat.set_firm_state("secure_messaging", "sideways", actor_user_id=staff.user_id)
    with pytest.raises(ValueError):
        feat.grant_entitlement("household", hid, "platinum", actor_user_id=staff.user_id)
    with pytest.raises(ValueError):
        feat.set_override("household", hid, "not_a_feature", "enable", actor_user_id=staff.user_id)
    with pytest.raises(ValueError):
        feat.revoke_entitlement("household", hid, "core", actor_user_id=staff.user_id)   # core is baseline


# === Decision #1: product entitlement is the boundary (above overrides) ======

def test_entitlement_boundary_is_the_authority():
    hid = _sid()
    staff = _staff()
    feat.set_firm_state("monte_carlo", "enabled", actor_user_id=staff.user_id)
    feat.set_override("household", hid, "monte_carlo", "enable", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "monte_carlo")            # ENABLE without Wealth → denied
    assert _reason("household", hid, "monte_carlo") == "entitlement_required"
    feat.grant_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    assert _allowed("household", hid, "monte_carlo")               # entitled → override now effective
    feat.revoke_entitlement("household", hid, "wealth", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "monte_carlo")           # boundary re-applies despite ENABLE


# === Decision #2: lifecycle status gates portal access ======================

def test_status_gates_portal_access():
    _, principal, _, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    assert feat.client_can(principal, "secure_messaging")          # active (no row) → open
    cases = [("inactive", None, False), ("inactive", "archive", False), ("needs_review", None, False),
             ("active", "prospect", False), ("inactive", "active", True), ("active", None, True)]
    for status, disp, expected_open in cases:
        feat.set_status("household", hid, status, disp, actor_user_id=staff.user_id)
        assert feat.client_can(principal, "secure_messaging") is expected_open, (status, disp)
        assert feat.portal_status_open("household", hid) is expected_open


def test_status_is_not_a_feature_grant():
    # Setting status active never grants a product the client isn't entitled to (status ≠ entitlement).
    hid = _sid()
    staff = _staff()
    feat.set_firm_state("schwab_accounts", "enabled", actor_user_id=staff.user_id)
    feat.set_status("household", hid, "active", actor_user_id=staff.user_id)
    assert not _allowed("household", hid, "schwab_accounts")        # still needs Wealth


# === Decision #7: master portal_access kill switch ==========================

def test_kill_switch_per_client_disables_everything():
    _, principal, _, hid = seed_portal_account(seed_staff_user())
    _, other, _, _ = seed_portal_account(seed_staff_user())
    staff = _staff()
    assert feat.client_can(principal, "secure_messaging")
    feat.set_override("household", hid, "portal_access", "disable", actor_user_id=staff.user_id)
    assert not feat.client_can(principal, "secure_messaging")       # ALL normal functionality denied
    assert not feat.client_can(principal, "document_vault")
    assert not feat.client_can(principal, "portal_access")
    assert feat.client_can(other, "secure_messaging")              # isolation — other client unaffected


def test_kill_switch_firm_wide_disables_all_clients():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    staff = _staff()
    feat.set_firm_state("portal_access", "disabled", actor_user_id=staff.user_id)
    assert not feat.client_can(principal, "secure_messaging")
    assert not feat.client_can(principal, "document_download")
    # generic client route also blocked by the master gate
    assert not portal_gate.evaluate(principal, "/portal/", "GET")[0]
    # but auth/logout stays reachable so the user can be informed / sign out
    assert portal_gate.evaluate(principal, "/api/v1/portal/auth/logout", "POST")[0]
    assert portal_gate.evaluate(principal, "/portal/logout", "POST")[0]


# === Decision #3: centralized per-route enforcement, no bypass ==============

def test_portal_gate_enforces_every_core_feature_on_direct_request(
        portal_messaging_on, portal_documents_download_on, portal_documents_upload_on, production_identity_provider):
    # This case walks the messaging/download/upload surfaces, so exactly those three child gates
    # are opened; the assertion under test is the PER-CLIENT Core feature override, not the gates.
    _, principal, _, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    cases = [
        ("secure_messaging", "/api/v1/portal/messages", "POST"),
        ("secure_messaging", "/portal/messages/3", "GET"),
        ("document_download", "/api/v1/portal/documents/5/download", "GET"),
        ("document_download", "/api/portal/documents/5/download", "GET"),
        ("document_upload", "/api/portal/documents", "POST"),
        ("document_upload", "/portal/upload", "POST"),
        ("document_upload", "/api/v1/portal/requests/9/upload", "POST"),
        ("document_vault", "/api/v1/portal/documents", "GET"),
        ("document_vault", "/portal/documents", "GET"),
        ("profile_editing", "/api/portal/profile", "PATCH"),
        ("profile_editing", "/portal/profile", "POST"),
        ("client_requests", "/api/v1/portal/requests", "GET"),
        ("portal_notifications", "/api/v1/portal/notifications", "GET"),
    ]
    for feature, path, method in cases:
        assert portal_gate.evaluate(principal, path, method)[0], f"default-allow failed: {method} {path}"
        feat.set_override("household", hid, feature, "disable", actor_user_id=staff.user_id)
        allowed, _reason, mapped = portal_gate.evaluate(principal, path, method)
        assert not allowed and mapped == feature, f"NOT enforced: {feature} on {method} {path}"
        feat.set_override("household", hid, feature, "inherit", actor_user_id=staff.user_id)


def test_portal_gate_maps_document_methods_correctly():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    # POST to the documents collection is an upload; GET is a vault read.
    assert portal_gate.feature_for_request("/api/portal/documents", "POST") == "document_upload"
    assert portal_gate.feature_for_request("/api/portal/documents", "GET") == "document_vault"
    # auth endpoints are exempt from any feature gate
    assert portal_gate.is_exempt("/api/v1/portal/auth/logout")
    assert portal_gate.is_exempt("/portal/login")


# === Decision #4: business access is per-organization =======================

def test_business_access_is_per_organization():
    staff = _staff()
    abc, xyz = _sid(), _sid()
    feat.set_firm_state("quickbooks", "enabled", actor_user_id=staff.user_id)
    feat.grant_entitlement("organization", abc, "business", actor_user_id=staff.user_id)
    assert _allowed("organization", abc, "quickbooks")             # ABC entitled
    assert not _allowed("organization", xyz, "quickbooks")         # XYZ not — isolated per org


def test_client_can_business_requires_explicit_in_scope_org():
    _, principal, _, _ = seed_portal_account(seed_staff_user())    # self account, no org grants
    staff = _staff()
    feat.set_firm_state("quickbooks", "enabled", actor_user_id=staff.user_id)
    assert feat.client_can(principal, "quickbooks", organization_id=None) is False   # fail-closed
    assert feat.client_can(principal, "quickbooks", organization_id=_sid()) is False  # not this client's org


def test_business_multi_org_client_does_not_leak_across_orgs():
    # John is associated with ABC (has Business) and XYZ (does not). He may use ABC Business, not XYZ.
    from sqlalchemy import insert as _insert

    from app.db import portal_access_grants
    from app.portal.service import (
        accept_invitation,
        create_portal_session,
        invite_portal_account,
        resolve_portal_session,
    )
    from app.services import organization_service as orgsvc

    org_staff = Principal(seed_staff_user(), "o@e.test", "O",
                          frozenset(STAFF_CAPS | {"organization.read", "organization.write"}))
    sfx = uuid.uuid4().hex[:8]
    abc = orgsvc.create_organization(org_staff, name=f"ABC {sfx}", ein=None)["organization_id"]
    xyz = orgsvc.create_organization(org_staff, name=f"XYZ {sfx}", ein=None)["organization_id"]
    hid, pid = _household()
    account_id, token = invite_portal_account(
        person_id=pid, household_id=hid, email=f"john-{sfx}@e.test", display_name="John",
        access_type="employer_admin", invited_by_user_id=org_staff.user_id,
        permissions={"benefits": True, "documents": True, "messages": True}, organization_id=abc)
    accept_invitation(token, f"subj-{sfx}", True)
    with engine.begin() as c:      # second org association (XYZ) — distinct access_type to satisfy the
        c.execute(_insert(portal_access_grants).values(   # (account,household,person,access_type,date) uniqueness
            portal_account_id=account_id, household_id=hid, person_id=pid, organization_id=xyz,
            access_type="delegated", permissions={"benefits": True},
            granted_by_user_id=org_staff.user_id))
    john = resolve_portal_session(create_portal_session(account_id, device_fingerprint=f"d-{sfx}"))

    feat.set_firm_state("quickbooks", "enabled", actor_user_id=org_staff.user_id)
    feat.grant_entitlement("organization", abc, "business", actor_user_id=org_staff.user_id)   # only ABC
    assert feat.client_can(john, "quickbooks", organization_id=abc) is True      # ABC business OK
    assert feat.client_can(john, "quickbooks", organization_id=xyz) is False     # XYZ must NOT leak


# === Decision #8: staff sees WHY a feature is unavailable ===================

def test_feature_report_explanations():
    hid = _sid()
    staff = _staff()
    feat.set_firm_state("schwab_accounts", "enabled", actor_user_id=staff.user_id)   # enabled but unentitled
    rep = {r["feature"]: r for r in feat.feature_report("household", hid)}
    assert rep["schwab_accounts"]["explanation"] == "Requires 360Plus Wealth"
    assert rep["secure_messaging"]["explanation"] == "Included with 360Plus Core"
    feat.set_firm_state("payroll", "disabled", actor_user_id=staff.user_id)
    rep = {r["feature"]: r for r in feat.feature_report("household", hid)}
    assert rep["payroll"]["explanation"] == "Disabled firm-wide"


def test_subject_access_summary_reports_closed_reasons():
    hid, _ = _household()
    staff = _staff()
    feat.set_status("household", hid, "needs_review", actor_user_id=staff.user_id)
    summary = feat.subject_access_summary("household", hid)
    assert summary["portal_open"] is False
    assert any("Needs Review" in r for r in summary["reasons"])
