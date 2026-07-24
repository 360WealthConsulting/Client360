"""Secure Client & Household Portal (Phase D.43) tests.

Covers the D.43 additive hardening of the existing Client Portal: the production/runtime gates (all off by
default, external production blocked until compliance sign-off), the declarative visibility registry +
governance invariants (no internal-only/prohibited field ever externally visible; account numbers masked),
the consent + electronic-delivery ledger, the masked/gated/fail-closed financial summary, the deterministic
local identity provider (production-guarded, never auto-links by email), internal-only diagnostics (no
identifiers), the appointment-request delegation, document-download scope (file-security), and the internal
admin surface (capability + record scope, no impersonation, token never returned). Deterministic local
doubles only — no external email/SMS/storage/signature/identity provider.
"""
import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import accounts, engine, households, people, portal_consents
from app.main import app
from app.portal import consent, diagnostics, financial, gate, governance, stats, visibility
from app.portal.appointments import request_appointment
from app.portal.identity_local import (
    LocalTestIdentityProvider,
    register_local_provider_if_permitted,
)
from app.portal.service import (
    accept_invitation,
    create_portal_session,
    invite_portal_account,
    portal_scope,
    resolve_portal_session,
)

# --- shared seeding (mirrors test_client_portal.py) --------------------------

def _seed_household(label="D43"):
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"{label} {suffix}").returning(households.c.id)).scalar_one()
        pids = [c.execute(insert(people).values(household_id=hid, full_name=f"{n} {suffix}", active=True)
                          .returning(people.c.id)).scalar_one() for n in ("Primary", "Joint")]
    return hid, pids, suffix


def _account(hid, pid, suffix, permissions=None, access_type="self", user_id=None):
    account_id, token = invite_portal_account(person_id=pid, household_id=hid,
        email=f"d43-{suffix}-{access_type}@example.com", display_name="D43 Client", access_type=access_type,
        invited_by_user_id=user_id, permissions=permissions)
    accept_invitation(token, f"local:d43-{suffix}-{access_type}", True)
    return account_id


def _principal(account_id):
    token = create_portal_session(account_id, device_fingerprint=f"dev-{uuid.uuid4()}")
    return resolve_portal_session(token)


# --- gates -------------------------------------------------------------------

def test_all_gates_off_by_default_and_production_blocked():
    status = gate.gate_status()
    # Every externally-facing capability defaults OFF; only mfa_required defaults ON.
    for name, on in status.items():
        assert on is (name == "portal.mfa_required"), name
    assert gate.portal_enabled() is False
    # External production access is blocked until the compliance sign-off gate is recorded.
    assert gate.production_ready() is False


# --- visibility registry + governance ----------------------------------------

def test_no_forbidden_field_is_externally_visible():
    for f in visibility.external_fields():
        assert f.external_visibility in visibility.EXTERNAL_STATES
    for key in ("internal.advisor_notes", "internal.compliance_reasoning", "internal.ai_assist_brief",
                "internal.work_queue", "internal.audit_history", "internal.net_worth",
                "internal.opportunity_revenue"):
        fld = visibility.field(key)
        assert fld is not None and fld.external_visibility in visibility.FORBIDDEN_STATES
        assert visibility.is_externally_visible(key) is False


def test_account_number_always_masked():
    acct = visibility.field("financial.account_number")
    assert acct.masking_rule == visibility.MASK_ACCOUNT
    masked = visibility.mask_account_number("1234567890")
    assert masked.endswith("7890") and "123456" not in masked
    assert visibility.mask_account_number("12") == "••••"      # too short → never leaks


def test_governance_clean():
    report = governance.validate_portal()
    assert report["ok"], report["findings"]


def test_visibility_coverage_declares_internal_and_prohibited():
    cov = visibility.coverage()
    assert cov["internal_only"] >= 5 and cov["prohibited"] >= 4 and cov["masked"] >= 1


# --- consent ledger ----------------------------------------------------------

def test_consent_record_idempotent_withdraw_and_audit():
    hid, pids, suffix = _seed_household()
    acct = _account(hid, pids[0], suffix)
    rid = f"req-{suffix}"
    cid = consent.record_consent(acct, "electronic_delivery", "v1", request_id=rid)
    assert consent.record_consent(acct, "electronic_delivery", "v1", request_id=rid) == cid  # idempotent
    assert consent.has_accepted(acct, "electronic_delivery") is True
    assert consent.electronic_delivery_active(acct) is True
    assert consent.withdraw_consent(acct, "electronic_delivery", request_id=rid) == cid
    assert consent.has_accepted(acct, "electronic_delivery") is False
    # unknown consent type is rejected
    with pytest.raises(ValueError):
        consent.record_consent(acct, "not_a_real_consent", "v1", request_id=rid)
    # exactly one ledger row for the (account, type, version) — no duplication
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_consents).where(
            portal_consents.c.portal_account_id == acct)) == 1


# --- financial summary: gating, scope, masking, freshness --------------------

def test_financial_summary_fails_closed_when_gate_off():
    hid, pids, suffix = _seed_household()
    acct = _account(hid, pids[0], suffix, permissions={"financial": True})
    principal = _principal(acct)
    summary = financial.financial_summary(principal)   # gate is off by default
    assert summary["enabled"] is False and summary["accounts"] == []


def test_financial_summary_masks_and_scopes_when_enabled(monkeypatch):
    hid, pids, suffix = _seed_household()
    account_number = f"AB{suffix}3456"      # suffix-unique; known last-4 for the masking assertion
    with engine.begin() as c:
        c.execute(insert(accounts).values(person_id=pids[0], custodian="Fidelity",
            account_number=account_number, account_name="Joint Brokerage", registration_type="Joint",
            total_value=100000, status="open"))
    acct = _account(hid, pids[0], suffix, permissions={"financial": True})
    principal = _principal(acct)
    monkeypatch.setattr(financial, "gate", lambda name: name == "portal.financial_summary_enabled")
    summary = financial.financial_summary(principal)
    assert summary["enabled"] is True and len(summary["accounts"]) == 1
    row = summary["accounts"][0]
    assert row["account_number_masked"].endswith("3456") and suffix not in row["account_number_masked"]
    assert row["current_value"] == 100000.0


def test_financial_summary_denies_without_financial_grant(monkeypatch):
    hid, pids, suffix = _seed_household()
    acct = _account(hid, pids[0], suffix, permissions={"messages": True})  # no financial grant
    principal = _principal(acct)
    monkeypatch.setattr(financial, "gate", lambda name: True)
    summary = financial.financial_summary(principal)
    assert summary["enabled"] is True and summary["accounts"] == []   # fail closed on missing grant


# --- local identity provider -------------------------------------------------

def test_local_identity_provider_verifies_and_respects_mfa_marker():
    p = LocalTestIdentityProvider()
    result = p.verify_activation("local:subject-123")
    assert result.subject == "local:subject-123" and result.mfa_verified is False
    assert result.email is None                                  # never auto-links by email
    assert p.verify_activation("local:subject-123:mfa").mfa_verified is True
    with pytest.raises(ValueError):
        p.verify_activation("bogus-assertion")


def test_local_provider_not_registered_in_production(monkeypatch):
    # When the compliance sign-off gate is set (production), the offline provider must NOT register.
    monkeypatch.setattr("app.portal.gate.gate", lambda name: name == "portal.production_signed_off")
    assert register_local_provider_if_permitted() is False


# --- appointment request delegation ------------------------------------------

def test_appointment_request_gated_and_delegates_to_messaging(monkeypatch):
    hid, pids, suffix = _seed_household()
    acct = _account(hid, pids[0], suffix, permissions={"messages": True})
    principal = _principal(acct)
    # gate off → not available
    with pytest.raises(PermissionError):
        request_appointment(principal, person_id=pids[0], household_id=hid,
                            preferred_window="next week", reason="review")
    # gate on → delegates as a secure-message thread (advisor books the real meeting)
    monkeypatch.setattr("app.portal.appointments.gate", lambda name: True)
    thread_id = request_appointment(principal, person_id=pids[0], household_id=hid,
                                    preferred_window="next week", reason="review")
    assert isinstance(thread_id, int)


# --- diagnostics: internal-only, no identifiers ------------------------------

def test_diagnostics_shape_is_internal_and_low_cardinality():
    stats.reset_stats()
    d = diagnostics.portal_diagnostics()
    assert {"enabled", "production_ready", "gates", "stats", "visibility_coverage", "governance"} <= set(d)
    assert d["governance"]["ok"] is True
    # aggregates only — never per-account identifiers/tokens
    import json
    blob = json.dumps(d)
    assert "@example.com" not in blob and "token" not in blob.lower()


# --- external scope resolver: household access is not blanket -----------------

def test_self_grant_does_not_reach_other_household():
    hid, pids, suffix = _seed_household()
    acct = _account(hid, pids[0], suffix, access_type="self",
                    permissions={"messages": True, "documents": True})
    other_hid, other_pids, _ = _seed_household("Other")
    scope = portal_scope(acct)
    assert hid in scope["household_ids"]
    assert other_hid not in scope["household_ids"]
    assert not (set(other_pids) & scope["person_ids"])   # never reaches another household's members


# --- route wiring + auth fork ------------------------------------------------

def test_external_routes_registered_and_require_portal_principal():
    from fastapi import HTTPException, Request

    from app.routes.portal import current_portal
    paths = {getattr(r, "path", None) for r in app.routes}
    for p in ("/portal/financial", "/api/v1/portal/financial", "/api/v1/portal/consents",
              "/api/v1/portal/appointments", "/api/v1/portal/appointments/request",
              "/api/v1/portal/documents/{document_id}/download", "/portal/security", "/portal/preferences"):
        assert p in paths, p
    # The shared portal dependency rejects a request with no resolved portal principal (fail closed).
    req = Request({"type": "http", "method": "GET", "path": "/portal/financial", "headers": [],
                   "query_string": b"", "state": {}})
    with pytest.raises(HTTPException) as exc:
        current_portal(req)
    assert exc.value.status_code == 401


def test_internal_admin_surface_on_staff_fork_and_capability_guarded():
    import inspect

    from app.routes import portal_admin
    # Admin routes live under /admin/client-portal (staff fork), never under the external /portal fork.
    admin_paths = {getattr(r, "path", None) for r in app.routes if getattr(r, "path", "").startswith("/admin/client-portal")}
    assert {"/admin/client-portal", "/admin/client-portal/invite", "/admin/client-portal/diagnostics",
            "/admin/client-portal/accounts/{account_id}/revoke",
            "/admin/client-portal/accounts/{account_id}/preview"} <= admin_paths
    assert not any(getattr(r, "path", "").startswith("/portal/") and "client-portal" in getattr(r, "path", "")
                   for r in app.routes)
    # Every admin endpoint is capability-guarded; diagnostics specifically requires observability.audit.
    src = inspect.getsource(portal_admin)
    assert 'require_capability("observability.audit")' in src
    assert 'require_capability("client.write")' in src and 'require_capability("client.read")' in src
    # Record-level scope enforced on invite/revoke (staff cannot invite outside their scope).
    assert src.count("record_in_scope(principal") >= 3


def test_admin_invite_never_returns_activation_token():
    # The admin invite endpoint returns only an account id + status; the raw token is delivered
    # out-of-band and never appears in the response body (verified at the service boundary).
    import inspect

    from app.routes import portal_admin
    src = inspect.getsource(portal_admin.portal_admin_invite)
    assert "_token" in src and "return {\"account_id\"" in src and "token" not in src.split("return")[1]
