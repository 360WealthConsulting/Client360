"""Real external identity provider for the client portal (compliance criterion #1, code side).

The portal's only provider was the deterministic local/test one, and its interface —
``verify_activation(assertion: str)`` — cannot express a real OIDC flow: a posted assertion has no
server-held ``state``/``nonce`` to bind to, which is exactly the token-substitution and replay exposure
the redirect flow prevents. These tests cover the redirect-capable interface, the Entra External ID
provider, and the two new auth routes.

MFA evidence is deliberately configuration-driven and FAILS CLOSED: the claim values Entra External ID
emits for a given user flow must be observed in the real tenant, so nothing is guessed here. With no
configuration, MFA cannot be proven and sign-in is refused.
"""
from __future__ import annotations

import pytest

from app.portal.identity_local import LocalTestIdentityProvider
from app.portal.identity_microsoft import (
    GENERIC_AUTH_ERROR,
    MICROSOFT_PROVIDER_KEY,
    MicrosoftExternalIdentityProvider,
    register_microsoft_provider_if_configured,
)
from app.portal.providers import PORTAL_IDENTITY_PROVIDERS, PortalIdentityResult

ISSUER = "https://contoso.ciamlogin.com/tenant"
CLIENT_ID = "client-id-under-test"


def _provider(**kw):
    kw.setdefault("issuer", ISSUER)
    kw.setdefault("client_id", CLIENT_ID)
    kw.setdefault("client_secret", "not-a-real-secret")
    return MicrosoftExternalIdentityProvider(**kw)


# --- interface separation -------------------------------------------------------

def test_local_provider_is_neither_redirect_capable_nor_production_capable():
    local = LocalTestIdentityProvider()
    assert local.supports_redirect_flow is False
    assert local.production_capable is False, "the synthetic provider must never satisfy production"


def test_microsoft_provider_is_redirect_and_production_capable():
    p = _provider()
    assert p.key == MICROSOFT_PROVIDER_KEY
    assert p.supports_redirect_flow is True
    assert p.production_capable is True


def test_microsoft_provider_refuses_a_posted_assertion():
    """A posted assertion cannot be bound to a server-held state/nonce, so it is refused outright."""
    with pytest.raises(ValueError) as exc:
        _provider().verify_activation("any.jwt.here")
    assert str(exc.value) == GENERIC_AUTH_ERROR


def test_registry_reports_production_capable_providers_excluding_local():
    saved = dict(PORTAL_IDENTITY_PROVIDERS._providers)
    try:
        PORTAL_IDENTITY_PROVIDERS._providers.clear()
        PORTAL_IDENTITY_PROVIDERS.register(LocalTestIdentityProvider())
        assert PORTAL_IDENTITY_PROVIDERS.production_capable() == ()
        PORTAL_IDENTITY_PROVIDERS.register(_provider())
        assert PORTAL_IDENTITY_PROVIDERS.production_capable() == (MICROSOFT_PROVIDER_KEY,)
    finally:
        PORTAL_IDENTITY_PROVIDERS._providers.clear()
        PORTAL_IDENTITY_PROVIDERS._providers.update(saved)


# --- configuration --------------------------------------------------------------

def test_provider_requires_issuer_and_client_id(monkeypatch):
    for var in ("PORTAL_OIDC_ISSUER", "PORTAL_OIDC_CLIENT_ID", "PORTAL_OIDC_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError):
        MicrosoftExternalIdentityProvider()


def test_registration_is_a_no_op_when_unconfigured(monkeypatch):
    for var in ("PORTAL_OIDC_ISSUER", "PORTAL_OIDC_CLIENT_ID", "PORTAL_OIDC_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    assert MicrosoftExternalIdentityProvider.is_configured() is False
    assert register_microsoft_provider_if_configured() is False, "must never fail startup"


def test_is_configured_requires_all_three_values(monkeypatch):
    monkeypatch.setenv("PORTAL_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PORTAL_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.delenv("PORTAL_OIDC_CLIENT_SECRET", raising=False)
    assert MicrosoftExternalIdentityProvider.is_configured() is False
    monkeypatch.setenv("PORTAL_OIDC_CLIENT_SECRET", "s")
    assert MicrosoftExternalIdentityProvider.is_configured() is True


# --- MFA: fail closed, never guessed ---------------------------------------------

def test_mfa_cannot_be_proven_without_configured_tenant_claim_values():
    """The core guard: an unconfigured deployment must not be able to assert MFA."""
    p = _provider()
    assert p.mfa_evidence_configured() is False
    assert p._mfa_verified({"amr": ["mfa", "otp", "hwk"]}) is False, (
        "MFA was inferred from claim values that were never configured — the workforce AMR "
        "interpretation must not be assumed for External ID")
    assert p._mfa_verified({"acr": "strong"}) is False


def test_mfa_accepted_only_for_configured_amr_values():
    p = _provider(mfa_amr_values={"mfa"})
    assert p.mfa_evidence_configured() is True
    assert p._mfa_verified({"amr": ["mfa"]}) is True
    assert p._mfa_verified({"amr": ["pwd"]}) is False
    assert p._mfa_verified({"amr": "mfa"}) is True          # scalar claim form
    assert p._mfa_verified({}) is False


def test_mfa_accepted_only_for_configured_acr_values():
    p = _provider(mfa_acr_values={"c1"})
    assert p._mfa_verified({"acr": "c1"}) is True
    assert p._mfa_verified({"acrs": ["c1"]}) is True
    assert p._mfa_verified({"acr": "c0"}) is False


# --- immutable subject ------------------------------------------------------------

def test_subject_prefers_the_immutable_object_id_and_is_namespaced():
    p = _provider()
    assert p._immutable_subject({"oid": "OID-1", "sub": "SUB-1"}) == f"{MICROSOFT_PROVIDER_KEY}:OID-1"
    assert p._immutable_subject({"sub": "SUB-1"}) == f"{MICROSOFT_PROVIDER_KEY}:SUB-1"
    assert p._immutable_subject({}) == ""


def test_email_is_never_the_identity_key():
    """Email may ride along for a secondary invitation check; it must not form the subject."""
    p = _provider()
    subject = p._immutable_subject({"oid": "OID-1", "email": "client@example.com",
                                    "preferred_username": "client@example.com"})
    assert "example.com" not in subject
    assert subject.endswith("OID-1")


# --- token validation failures all map to one generic error ------------------------

@pytest.mark.parametrize("claims_error", ["signature", "issuer", "audience", "expired", "malformed"])
def test_every_token_validation_failure_is_generic(monkeypatch, claims_error):
    p = _provider(mfa_amr_values={"mfa"})
    monkeypatch.setattr(p, "_discovery", lambda: {"jwks_uri": "https://x/jwks"})

    def boom(*a, **k):
        raise Exception(f"internal detail: {claims_error}")

    monkeypatch.setattr("jwt.PyJWKClient", boom)
    with pytest.raises(ValueError) as exc:
        p._verify_id_token("token", expected_nonce="n")
    assert str(exc.value) == GENERIC_AUTH_ERROR
    assert claims_error not in str(exc.value), "internal failure detail leaked to the caller"


def test_nonce_mismatch_is_rejected(monkeypatch):
    p = _provider(mfa_amr_values={"mfa"})
    monkeypatch.setattr(p, "_discovery", lambda: {"jwks_uri": "https://x/jwks"})
    monkeypatch.setattr("jwt.PyJWKClient", lambda uri: type("K", (), {
        "get_signing_key_from_jwt": lambda self, t: type("S", (), {"key": "k"})()})())
    monkeypatch.setattr("jwt.decode", lambda *a, **k: {"sub": "S", "nonce": "OTHER", "amr": ["mfa"]})
    with pytest.raises(ValueError):
        p._verify_id_token("token", expected_nonce="EXPECTED")


def test_missing_mfa_is_rejected_even_with_a_valid_token(monkeypatch):
    p = _provider(mfa_amr_values={"mfa"})
    monkeypatch.setattr(p, "_discovery", lambda: {"jwks_uri": "https://x/jwks"})
    monkeypatch.setattr("jwt.PyJWKClient", lambda uri: type("K", (), {
        "get_signing_key_from_jwt": lambda self, t: type("S", (), {"key": "k"})()})())
    monkeypatch.setattr("jwt.decode", lambda *a, **k: {"sub": "S", "nonce": "N", "amr": ["pwd"]})
    with pytest.raises(ValueError):
        p._verify_id_token("token", expected_nonce="N")


def test_valid_token_yields_an_mfa_verified_identity(monkeypatch):
    p = _provider(mfa_amr_values={"mfa"})
    monkeypatch.setattr(p, "_discovery", lambda: {"jwks_uri": "https://x/jwks"})
    monkeypatch.setattr("jwt.PyJWKClient", lambda uri: type("K", (), {
        "get_signing_key_from_jwt": lambda self, t: type("S", (), {"key": "k"})()})())
    monkeypatch.setattr("jwt.decode", lambda *a, **k: {
        "oid": "OID-9", "sub": "SUB-9", "nonce": "N", "amr": ["mfa"], "email": "c@example.com"})
    result = p._verify_id_token("token", expected_nonce="N")
    assert isinstance(result, PortalIdentityResult)
    assert result.subject == f"{MICROSOFT_PROVIDER_KEY}:OID-9"
    assert result.mfa_verified is True


# --- authorization URL carries state, nonce and PKCE --------------------------------

def test_authorization_url_includes_state_nonce_and_pkce(monkeypatch):
    p = _provider()
    monkeypatch.setattr(p, "_discovery", lambda: {"authorization_endpoint": "https://idp/authorize"})
    url = p.authorization_url(state="ST", nonce="NO", redirect_uri="https://app/cb",
                              code_challenge="CH")
    for expected in ("state=ST", "nonce=NO", "code_challenge=CH", "code_challenge_method=S256",
                     "response_type=code", f"client_id={CLIENT_ID}"):
        assert expected in url, f"authorization URL missing {expected}"


def test_token_endpoint_failure_does_not_leak_the_response(monkeypatch):
    p = _provider()
    monkeypatch.setattr(p, "_discovery", lambda: {"token_endpoint": "https://idp/token"})

    class _Resp:
        status_code = 400
        text = "AADSTS70008: the code contained SECRETVALUE"

        def json(self):
            return {"error": "invalid_grant", "error_description": self.text}

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())
    with pytest.raises(ValueError) as exc:
        p.exchange_code(code="c", redirect_uri="https://app/cb", code_verifier="v",
                        expected_nonce="n")
    assert str(exc.value) == GENERIC_AUTH_ERROR
    assert "SECRETVALUE" not in str(exc.value)


# --- production_ready() invariant ----------------------------------------------------

def test_production_ready_requires_a_production_capable_provider(portal_gates):
    """Sign-off must not imply usable external access when nobody could authenticate."""
    from app.portal.gate import production_ready
    portal_gates({"portal.enabled", "portal.production_signed_off"})
    assert production_ready() is False, "production_ready() ignored the missing external IdP"


def test_production_ready_is_not_satisfied_by_the_local_provider(portal_gates):
    from app.portal.gate import production_ready
    saved = dict(PORTAL_IDENTITY_PROVIDERS._providers)
    try:
        PORTAL_IDENTITY_PROVIDERS.register(LocalTestIdentityProvider())
        portal_gates({"portal.enabled", "portal.production_signed_off",
                      "portal.local_identity_provider_enabled"})
        assert production_ready() is False, "the synthetic provider satisfied production readiness"
    finally:
        PORTAL_IDENTITY_PROVIDERS._providers.clear()
        PORTAL_IDENTITY_PROVIDERS._providers.update(saved)


def test_production_ready_true_only_with_gates_and_a_real_provider(
        portal_gates, production_identity_provider):
    from app.portal.gate import production_ready
    portal_gates({"portal.enabled", "portal.production_signed_off"})
    assert production_ready() is True


# --- the two new auth routes ----------------------------------------------------------

def _session_request(session=None, **kw):
    """A minimal request carrying a mutable session and the ``url_for`` the auth routes call."""
    from tests._portal_util import fake_request
    req = fake_request(session=session if session is not None else {}, **kw)
    req.url_for = lambda name: "https://app.test/portal/auth/callback"
    return req


def test_auth_routes_exist_and_are_publicly_reachable():
    from app.main import app
    from app.security.middleware import PUBLIC_EXACT
    from app.services.features import portal_gate

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/portal/auth/start" in paths and "/portal/auth/callback" in paths
    # Reached BEFORE a portal session exists, so both layers must let them through.
    assert "/portal/auth/start" in PUBLIC_EXACT and "/portal/auth/callback" in PUBLIC_EXACT
    assert portal_gate.is_exempt("/portal/auth/start")
    assert portal_gate.is_exempt("/portal/auth/callback")


def test_start_redirects_to_login_when_no_production_provider_is_registered():
    from app.routes.portal import portal_auth_start
    response = portal_auth_start(_session_request())
    assert response.status_code == 303
    assert response.headers["location"] == "/portal/login?error=unavailable"


def test_start_mints_state_nonce_and_verifier_into_the_session(monkeypatch):
    from app.routes import portal as portal_routes

    class _P:
        def authorization_url(self, **kw):
            _P.seen = kw
            return "https://idp/authorize?x=1"

    monkeypatch.setattr("app.portal.providers.PORTAL_IDENTITY_PROVIDERS.get", lambda key: _P())
    session = {}
    req = _session_request(session)
    response = portal_routes.portal_auth_start(req, invitation="INV-TOKEN")
    assert response.status_code == 303
    for key in ("portal_oidc_state", "portal_oidc_nonce", "portal_oidc_verifier"):
        assert session.get(key), f"{key} was not held server-side"
    assert session["portal_oidc_invitation"] == "INV-TOKEN"
    # The verifier must never travel to the IdP — only its S256 challenge.
    assert _P.seen["code_challenge"] and _P.seen["code_challenge"] != session["portal_oidc_verifier"]
    assert _P.seen["state"] == session["portal_oidc_state"]
    assert _P.seen["nonce"] == session["portal_oidc_nonce"]


def test_callback_rejects_a_mismatched_state():
    from app.routes.portal import portal_auth_callback
    session = {"portal_oidc_state": "EXPECTED", "portal_oidc_nonce": "N",
               "portal_oidc_verifier": "V"}
    response = portal_auth_callback(_session_request(session), code="c", state="ATTACKER")
    assert response.status_code == 303
    assert response.headers["location"] == "/portal/login?error=failed"
    assert "portal_session_token" not in session


def test_callback_rejects_when_no_flow_was_started():
    """A replayed callback with no server-side state must fail closed."""
    from app.routes.portal import portal_auth_callback
    response = portal_auth_callback(_session_request({}), code="c", state="anything")
    assert response.headers["location"] == "/portal/login?error=failed"


def test_callback_consumes_state_so_it_cannot_be_replayed(monkeypatch):
    from app.routes.portal import portal_auth_callback
    session = {"portal_oidc_state": "S", "portal_oidc_nonce": "N", "portal_oidc_verifier": "V"}
    portal_auth_callback(_session_request(session), code="c", state="S")
    assert "portal_oidc_state" not in session, "state survived the callback and could be replayed"
    assert "portal_oidc_verifier" not in session


def test_callback_failure_never_creates_a_session(monkeypatch):
    from app.routes.portal import portal_auth_callback

    class _P:
        def exchange_code(self, **kw):
            raise ValueError(GENERIC_AUTH_ERROR)

    monkeypatch.setattr("app.portal.providers.PORTAL_IDENTITY_PROVIDERS.get", lambda key: _P())
    session = {"portal_oidc_state": "S", "portal_oidc_nonce": "N", "portal_oidc_verifier": "V"}
    response = portal_auth_callback(_session_request(session), code="c", state="S")
    assert response.headers["location"] == "/portal/login?error=failed"
    assert "portal_session_token" not in session


def test_callback_redirect_target_is_fixed_and_not_attacker_controlled():
    """No open redirect: success and failure both go to fixed in-app paths."""
    import inspect

    from app.routes import portal as portal_routes
    src = inspect.getsource(portal_routes.portal_auth_callback)
    assert '"/portal"' in src and '"/portal/login?error=failed"' in src
    for attacker_param in ("next", "return_to", "redirect_to", "returnUrl"):
        assert f'request.query_params.get("{attacker_param}")' not in src


# --- repeat sign-in by immutable subject ------------------------------------------------

def test_sign_in_with_subject_requires_mfa_and_an_active_account():
    from app.portal.service import sign_in_with_subject
    with pytest.raises(ValueError):
        sign_in_with_subject("microsoft:UNKNOWN", True)          # unknown subject
    with pytest.raises(ValueError, match="MFA"):
        sign_in_with_subject("microsoft:ANY", False)             # MFA not proven
    with pytest.raises(ValueError):
        sign_in_with_subject("", True)                           # empty subject


def test_sign_in_never_falls_back_to_email_matching():
    """The staff path binds by email fallback; for external clients that is a takeover primitive."""
    import inspect

    from app.portal import service
    src = inspect.getsource(service.sign_in_with_subject)
    assert "normalized_email" not in src and "email" not in src.split('"""')[2]
    assert "auth_subject" in src


# --- configuration declaration ----------------------------------------------------------

def test_unset_portal_identity_config_is_silent(monkeypatch):
    """No production IdP is a normal state, not a misconfiguration."""
    from app.config import _PORTAL_OIDC_REQUIRED, _portal_identity_warnings
    for key in (*_PORTAL_OIDC_REQUIRED, "PORTAL_OIDC_MFA_AMR_VALUES", "PORTAL_OIDC_MFA_ACR_VALUES"):
        monkeypatch.delenv(key, raising=False)
    assert _portal_identity_warnings() == []


def test_partial_portal_identity_config_warns_by_name_only(monkeypatch):
    from app.config import _portal_identity_warnings
    monkeypatch.setenv("PORTAL_OIDC_ISSUER", "https://tenant.ciamlogin.com/x/v2.0")
    monkeypatch.setenv("PORTAL_OIDC_CLIENT_SECRET", "super-secret-value")
    monkeypatch.delenv("PORTAL_OIDC_CLIENT_ID", raising=False)
    warnings = _portal_identity_warnings()
    assert len(warnings) == 1 and "PORTAL_OIDC_CLIENT_ID" in warnings[0]
    assert "super-secret-value" not in warnings[0], "a secret value leaked into a startup warning"


def test_configured_without_mfa_claim_values_warns_that_signin_is_refused(monkeypatch):
    from app.config import _portal_identity_warnings
    monkeypatch.setenv("PORTAL_OIDC_ISSUER", "https://tenant.ciamlogin.com/x/v2.0")
    monkeypatch.setenv("PORTAL_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("PORTAL_OIDC_CLIENT_SECRET", "sec")
    monkeypatch.delenv("PORTAL_OIDC_MFA_AMR_VALUES", raising=False)
    monkeypatch.delenv("PORTAL_OIDC_MFA_ACR_VALUES", raising=False)
    warnings = _portal_identity_warnings()
    assert len(warnings) == 1 and "fail-closed" in warnings[0]
    monkeypatch.setenv("PORTAL_OIDC_MFA_AMR_VALUES", "mfa")
    assert _portal_identity_warnings() == []


def test_every_portal_oidc_key_is_documented_in_the_config_module():
    import app.config as config
    for key in ("PORTAL_OIDC_ISSUER", "PORTAL_OIDC_CLIENT_ID", "PORTAL_OIDC_CLIENT_SECRET",
                "PORTAL_OIDC_AUDIENCE", "PORTAL_OIDC_SCOPES",
                "PORTAL_OIDC_MFA_AMR_VALUES", "PORTAL_OIDC_MFA_ACR_VALUES"):
        assert key in (config.__doc__ or ""), f"{key} is read but not declared in app/config.py"
    assert "SECRET" in (config.__doc__ or "")
