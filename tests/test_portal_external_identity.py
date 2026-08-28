"""Real external identity provider for the client portal (compliance criterion #1, code side).

The portal's only provider was the deterministic local/test one, and its interface —
``verify_activation(assertion: str)`` — cannot express a real OIDC flow: a posted assertion has no
server-held ``state``/``nonce`` to bind to, which is exactly the token-substitution and replay exposure
the redirect flow prevents. These tests cover the redirect-capable interface, the Entra External ID
provider, and the two new auth routes.

MFA evidence is deliberately configuration-driven and FAILS CLOSED. ``PORTAL_OIDC_MFA_MODE`` names the
enforcement authority explicitly: ``claims`` (the default) proves MFA from the tenant's own observed
``amr``/``acr`` values and refuses sign-in when none are configured, and ``conditional_access`` delegates
enforcement to the Entra Conditional Access policy protecting the portal application — the correct mode
for a tenant whose validated production tokens carry no ``amr``/``acr``/``acrs`` at all. Neither mode is
inferred, an unknown mode proves nothing, and no other validation is relaxed in either.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from types import SimpleNamespace

import jwt
import pytest

from app.portal.identity_local import LocalTestIdentityProvider
from app.portal.identity_microsoft import (
    GENERIC_AUTH_ERROR,
    MFA_MODE_CLAIMS,
    MFA_MODE_CONDITIONAL_ACCESS,
    MICROSOFT_PROVIDER_KEY,
    MicrosoftExternalIdentityProvider,
    register_microsoft_provider_if_configured,
)
from app.portal.providers import PORTAL_IDENTITY_PROVIDERS, PortalIdentityResult

ISSUER = "https://contoso.ciamlogin.com/tenant"
CLIENT_ID = "client-id-under-test"


#: The claim set of a REAL validated production Entra External ID token for this deployment. Note what
#: is absent: no ``amr``, no ``acr``, no ``acrs``. Claim-based MFA evidence cannot exist here, which is
#: why the Conditional Access policy protecting the portal application is the enforcement authority.
PRODUCTION_TOKEN_CLAIMS = {
    "aud": CLIENT_ID,
    "email": "client@example.com",
    "exp": 2000003600,
    "iat": 2000000000,
    "iss": ISSUER,
    "name": "A Client",
    "nbf": 2000000000,
    "nonce": "N",
    "oid": "OID-PROD",
    "preferred_username": "client@example.com",
    "rh": "0.ARoA-opaque",
    "sid": "SID-1",
    "sub": "SUB-PROD",
    "tid": "TID-1",
    "uti": "UTI-1",
    "ver": "2.0",
}


def _provider(**kw):
    kw.setdefault("issuer", ISSUER)
    kw.setdefault("client_id", CLIENT_ID)
    kw.setdefault("client_secret", "not-a-real-secret")
    return MicrosoftExternalIdentityProvider(**kw)


def _fake_jwk_client():
    return lambda uri: type("K", (), {
        "get_signing_key_from_jwt": lambda self, t: type("S", (), {"key": "k"})()})()


def _stub_token(monkeypatch, provider, claims, *, decode_calls=None):
    """Stand in for discovery/JWKS/decode so a test can drive an exact claim set."""
    monkeypatch.setattr(provider, "_discovery", lambda: {"jwks_uri": "https://x/jwks"})
    monkeypatch.setattr("jwt.PyJWKClient", _fake_jwk_client())

    def _decode(*args, **kwargs):
        if decode_calls is not None:
            decode_calls.append(kwargs)
        return dict(claims)

    monkeypatch.setattr("jwt.decode", _decode)


def _configured_portal_env(monkeypatch):
    """A complete PORTAL_OIDC_* set with every MFA key cleared, for warning tests."""
    monkeypatch.setenv("PORTAL_OIDC_ISSUER", "https://tenant.ciamlogin.com/x/v2.0")
    monkeypatch.setenv("PORTAL_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("PORTAL_OIDC_CLIENT_SECRET", "sec")
    for key in ("PORTAL_OIDC_MFA_MODE", "PORTAL_OIDC_MFA_AMR_VALUES", "PORTAL_OIDC_MFA_ACR_VALUES"):
        monkeypatch.delenv(key, raising=False)


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
    assert p.mfa_mode == MFA_MODE_CLAIMS, "the default mode must be the fail-closed claims mode"
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


# --- MFA mode: which authority proves MFA ------------------------------------------

def test_mfa_mode_defaults_to_claims_and_conditional_access_is_never_inferred(monkeypatch):
    """Absent MFA configuration must NOT be read as "Conditional Access must be handling it"."""
    monkeypatch.delenv("PORTAL_OIDC_MFA_MODE", raising=False)
    p = _provider()
    assert p.mfa_mode == MFA_MODE_CLAIMS
    assert p.mfa_evidence_configured() is False
    _stub_token(monkeypatch, p, PRODUCTION_TOKEN_CLAIMS)
    with pytest.raises(ValueError) as exc:
        p._verify_id_token("token", expected_nonce="N")
    assert str(exc.value) == GENERIC_AUTH_ERROR


def test_mfa_mode_is_read_from_the_environment_and_normalised(monkeypatch):
    monkeypatch.setenv("PORTAL_OIDC_MFA_MODE", "  Conditional_Access ")
    assert _provider().mfa_mode == MFA_MODE_CONDITIONAL_ACCESS


def test_conditional_access_mode_accepts_the_real_production_token_shape(monkeypatch):
    """The production defect: a valid token carrying no amr/acr/acrs was being refused.

    Conditional Access is the enforcement authority for this deployment, so the token that survived
    the whole flow is proof enough — but ONLY because the operator named the mode."""
    assert not {"amr", "acr", "acrs"} & set(PRODUCTION_TOKEN_CLAIMS), (
        "the production evidence says these claims are absent; the fixture must reflect that")
    p = _provider(mfa_mode=MFA_MODE_CONDITIONAL_ACCESS)
    assert p.mfa_evidence_configured() is True
    calls = []
    _stub_token(monkeypatch, p, PRODUCTION_TOKEN_CLAIMS, decode_calls=calls)

    result = p._verify_id_token("token", expected_nonce="N")

    assert isinstance(result, PortalIdentityResult)
    assert result.subject == f"{MICROSOFT_PROVIDER_KEY}:OID-PROD"
    assert result.mfa_verified is True
    assert result.email == "client@example.com"
    # ...and nothing about the cryptographic validation was relaxed to get there.
    assert calls, "the ID token was never decoded"
    kwargs = calls[0]
    assert kwargs["issuer"] == ISSUER, "issuer validation was dropped"
    assert kwargs["audience"] == CLIENT_ID, "audience validation was dropped"
    assert set(kwargs["algorithms"]) == {"RS256", "ES256"}
    assert {"exp", "iss", "aud", "sub"} <= set(kwargs["options"]["require"])


@pytest.mark.parametrize("failure", ["InvalidSignatureError", "InvalidIssuerError",
                                     "InvalidAudienceError", "ExpiredSignatureError",
                                     "MissingRequiredClaimError"])
def test_conditional_access_mode_still_rejects_every_token_validation_failure(monkeypatch, failure):
    """Delegating MFA does not delegate anything else: the decode layer still gates entry."""
    p = _provider(mfa_mode=MFA_MODE_CONDITIONAL_ACCESS)
    monkeypatch.setattr(p, "_discovery", lambda: {"jwks_uri": "https://x/jwks"})
    monkeypatch.setattr("jwt.PyJWKClient", _fake_jwk_client())
    error = getattr(jwt.exceptions, failure)

    def boom(*a, **k):
        raise error("sub")

    monkeypatch.setattr("jwt.decode", boom)
    with pytest.raises(ValueError) as exc:
        p._verify_id_token("token", expected_nonce="N")
    assert str(exc.value) == GENERIC_AUTH_ERROR


def test_conditional_access_mode_still_rejects_a_mismatched_nonce(monkeypatch):
    p = _provider(mfa_mode=MFA_MODE_CONDITIONAL_ACCESS)
    _stub_token(monkeypatch, p, PRODUCTION_TOKEN_CLAIMS)
    with pytest.raises(ValueError):
        p._verify_id_token("token", expected_nonce="EXPECTED-DIFFERENT")
    with pytest.raises(ValueError):
        p._verify_id_token("token", expected_nonce="")      # no flow started


def test_conditional_access_mode_still_requires_an_immutable_subject(monkeypatch):
    p = _provider(mfa_mode=MFA_MODE_CONDITIONAL_ACCESS)
    claims = {k: v for k, v in PRODUCTION_TOKEN_CLAIMS.items() if k not in ("oid", "sub")}
    _stub_token(monkeypatch, p, claims)
    with pytest.raises(ValueError) as exc:
        p._verify_id_token("token", expected_nonce="N")
    assert str(exc.value) == GENERIC_AUTH_ERROR


def test_conditional_access_mode_never_falls_back_to_email_as_the_subject(monkeypatch):
    """The token still carries email/preferred_username; neither may become the identity key."""
    p = _provider(mfa_mode=MFA_MODE_CONDITIONAL_ACCESS)
    _stub_token(monkeypatch, p, PRODUCTION_TOKEN_CLAIMS)
    subject = p._verify_id_token("token", expected_nonce="N").subject
    assert "example.com" not in subject and subject.endswith("OID-PROD")


@pytest.mark.parametrize("mode", ["conditional-access", "conditionalaccess", "ca", "true", "on", "-"])
def test_an_unknown_mfa_mode_fails_closed(monkeypatch, mode):
    """A typo'd or invented mode must prove nothing — not fall back to either real mode."""
    p = _provider(mfa_mode=mode, mfa_amr_values={"mfa"})
    assert p.mfa_evidence_configured() is False
    assert p._mfa_verified({"amr": ["mfa"]}) is False, "an unknown mode accepted claim evidence"
    assert p._mfa_verified(PRODUCTION_TOKEN_CLAIMS) is False
    _stub_token(monkeypatch, p, {**PRODUCTION_TOKEN_CLAIMS, "amr": ["mfa"]})
    with pytest.raises(ValueError) as exc:
        p._verify_id_token("token", expected_nonce="N")
    assert str(exc.value) == GENERIC_AUTH_ERROR


def test_claims_mode_is_unaffected_by_the_new_mode_switch(monkeypatch):
    """Explicit claims mode behaves exactly as before, including on the production token shape."""
    p = _provider(mfa_mode=MFA_MODE_CLAIMS, mfa_amr_values={"mfa"})
    assert p._mfa_verified({"amr": ["mfa"]}) is True
    assert p._mfa_verified({"amr": ["pwd"]}) is False
    _stub_token(monkeypatch, p, PRODUCTION_TOKEN_CLAIMS)
    with pytest.raises(ValueError):
        p._verify_id_token("token", expected_nonce="N")     # no amr/acr => unproven => refused


def test_a_returned_identity_is_always_mfa_verified_in_either_mode(monkeypatch):
    """Downstream contract: the provider raises rather than hand back an unverified identity, so
    ``sign_in_with_subject(..., mfa_verified)`` can never be reached with False from this path."""
    import inspect

    src = inspect.getsource(MicrosoftExternalIdentityProvider._verify_id_token)
    assert "mfa_verified=True" in src and "mfa_verified=False" not in src
    for provider in (_provider(mfa_amr_values={"mfa"}),
                     _provider(mfa_mode=MFA_MODE_CONDITIONAL_ACCESS)):
        _stub_token(monkeypatch, provider, {**PRODUCTION_TOKEN_CLAIMS, "amr": ["mfa"]})
        assert provider._verify_id_token("token", expected_nonce="N").mfa_verified is True


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

    # /portal/auth/start now refuses before minting anything unless the portal is production-ready
    # (so a gated portal cannot consume a client's single-use invitation). This test exercises the
    # AVAILABLE path, so it sets that precondition explicitly.
    monkeypatch.setattr("app.portal.gate.production_ready", lambda: True)

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
    for key in (*_PORTAL_OIDC_REQUIRED, "PORTAL_OIDC_MFA_MODE", "PORTAL_OIDC_MFA_AMR_VALUES",
                "PORTAL_OIDC_MFA_ACR_VALUES"):
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


def test_claims_mode_without_mfa_claim_values_warns_that_signin_is_refused(monkeypatch):
    """Default (claims) mode with nothing configured refuses every sign-in — say so out loud, and
    point at the conditional-access alternative rather than leaving the operator to invent one."""
    from app.config import _portal_identity_warnings
    _configured_portal_env(monkeypatch)
    warnings = _portal_identity_warnings()
    assert len(warnings) == 1 and "fail-closed" in warnings[0]
    assert MFA_MODE_CLAIMS in warnings[0] and MFA_MODE_CONDITIONAL_ACCESS in warnings[0]
    monkeypatch.setenv("PORTAL_OIDC_MFA_AMR_VALUES", "mfa")
    assert _portal_identity_warnings() == []


def test_explicit_claims_mode_warns_exactly_like_the_default(monkeypatch):
    from app.config import _portal_identity_warnings
    _configured_portal_env(monkeypatch)
    monkeypatch.setenv("PORTAL_OIDC_MFA_MODE", MFA_MODE_CLAIMS)
    warnings = _portal_identity_warnings()
    assert len(warnings) == 1 and "fail-closed" in warnings[0]
    monkeypatch.setenv("PORTAL_OIDC_MFA_ACR_VALUES", "c1")
    assert _portal_identity_warnings() == []


def test_conditional_access_mode_needs_no_claim_values_and_warns_nothing(monkeypatch):
    """The distinction that matters: conditional-access mode is COMPLETE without claim values,
    so it must not inherit the claims-mode "every sign-in will be refused" warning."""
    from app.config import _portal_identity_warnings
    _configured_portal_env(monkeypatch)
    monkeypatch.setenv("PORTAL_OIDC_MFA_MODE", MFA_MODE_CONDITIONAL_ACCESS)
    assert _portal_identity_warnings() == []


def test_conditional_access_mode_warns_that_leftover_claim_values_are_ignored(monkeypatch):
    from app.config import _portal_identity_warnings
    _configured_portal_env(monkeypatch)
    monkeypatch.setenv("PORTAL_OIDC_MFA_MODE", MFA_MODE_CONDITIONAL_ACCESS)
    monkeypatch.setenv("PORTAL_OIDC_MFA_AMR_VALUES", "mfa")
    warnings = _portal_identity_warnings()
    assert len(warnings) == 1 and "IGNORED" in warnings[0]
    assert "PORTAL_OIDC_MFA_AMR_VALUES" in warnings[0]
    assert "refused" not in warnings[0], "conditional-access mode does not refuse sign-in"


def test_an_unknown_mfa_mode_warns_by_name_without_echoing_the_value(monkeypatch):
    from app.config import _portal_identity_warnings
    _configured_portal_env(monkeypatch)
    monkeypatch.setenv("PORTAL_OIDC_MFA_MODE", "conditional-access")
    monkeypatch.setenv("PORTAL_OIDC_MFA_AMR_VALUES", "mfa")
    warnings = _portal_identity_warnings()
    assert len(warnings) == 1 and "PORTAL_OIDC_MFA_MODE" in warnings[0]
    assert "refused" in warnings[0], "an unknown mode is fail-closed and must say so"
    assert "conditional-access" not in warnings[0], "the configured value was echoed back"


def test_a_partial_configuration_warning_still_wins_over_the_mfa_mode_warning(monkeypatch):
    """A missing required key is the more actionable problem; only one warning is emitted."""
    from app.config import _portal_identity_warnings
    _configured_portal_env(monkeypatch)
    monkeypatch.delenv("PORTAL_OIDC_CLIENT_ID", raising=False)
    monkeypatch.setenv("PORTAL_OIDC_MFA_MODE", "nonsense")
    warnings = _portal_identity_warnings()
    assert len(warnings) == 1 and "PORTAL_OIDC_CLIENT_ID" in warnings[0]


def test_every_portal_oidc_key_is_documented_in_the_config_module():
    import app.config as config
    for key in ("PORTAL_OIDC_ISSUER", "PORTAL_OIDC_CLIENT_ID", "PORTAL_OIDC_CLIENT_SECRET",
                "PORTAL_OIDC_AUDIENCE", "PORTAL_OIDC_SCOPES", "PORTAL_OIDC_MFA_MODE",
                "PORTAL_OIDC_MFA_AMR_VALUES", "PORTAL_OIDC_MFA_ACR_VALUES"):
        assert key in (config.__doc__ or ""), f"{key} is read but not declared in app/config.py"
    assert "SECRET" in (config.__doc__ or "")
    doc = config.__doc__ or ""
    for mode in (MFA_MODE_CLAIMS, MFA_MODE_CONDITIONAL_ACCESS):
        assert mode in doc, f"the {mode} MFA mode is supported but not documented"


# --- TEMPORARY production diagnostic on portal_auth_callback -----------------------------
# The callback funnels every failure into one generic redirect, so a production activation
# failure was undiagnosable. These tests pin the diagnostic's two obligations: it must classify
# the failing stage, and it must be incapable of emitting anything sensitive.

DIAG_LOGGER = "client360.portal.auth"


@contextlib.contextmanager
def _capture_diagnostics():
    """Collect diagnostic records from the portal auth logger itself.

    Deliberately not pytest's log-capture fixture: app.observability.logging.configure_logging()
    sets ``propagate = False`` on the ``client360`` parent, so once any test in the suite has
    configured logging these records never reach the root logger that fixture attaches to.
    Handling the logger directly makes these assertions independent of suite order and of global
    logging state."""
    records = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger(DIAG_LOGGER)
    handler = _Collector()
    previous_level, previous_disabled = logger.level, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.disabled = False
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.disabled = previous_disabled

#: Sentinels seeded into every position the callback touches. None may ever reach the log.
SECRETS = {
    "code": "AUTHZ-CODE-b3f1c9",
    "state": "STATE-VALUE-77ab",
    "nonce": "NONCE-VALUE-91cd",
    "verifier": "PKCE-VERIFIER-4e2f",
    "invitation": "INVITE-TOKEN-d0a6",
    "subject": "microsoft:SUBJECT-OID-5521",
    "email": "client-secret-name@example.test",
    "id_token": "eyJhbGciOiJSUzI1NiJ9.PAYLOAD.SIGNATURE",
}


def _diag_session(*, invitation=False):
    session = {"portal_oidc_state": SECRETS["state"], "portal_oidc_nonce": SECRETS["nonce"],
               "portal_oidc_verifier": SECRETS["verifier"]}
    if invitation:
        session["portal_oidc_invitation"] = SECRETS["invitation"]
    return session


class _LoudError(ValueError):
    """An exception whose text carries every secret — proves str(exc) is never logged."""


def _loud():
    return _LoudError(" ".join(SECRETS.values()))


class _StubIdentity:
    subject = SECRETS["subject"]
    mfa_verified = True
    email = SECRETS["email"]


def _stub_provider(monkeypatch, exchange=None):
    class _P:
        def exchange_code(self, **kw):
            if exchange:
                raise exchange()
            return _StubIdentity()

    monkeypatch.setattr("app.portal.providers.PORTAL_IDENTITY_PROVIDERS.get", lambda key: _P())


def _fail_at(monkeypatch, stage):
    """Arrange the callback to fail at exactly ``stage``. Returns the session to pass in."""
    if stage == "state_validation":
        return _diag_session()                       # driven with a mismatched state below
    if stage == "provider_lookup":
        def _boom(key):
            raise _loud()
        monkeypatch.setattr("app.portal.providers.PORTAL_IDENTITY_PROVIDERS.get", _boom)
        return _diag_session()
    if stage == "redirect_uri":
        _stub_provider(monkeypatch)
        monkeypatch.setattr("app.security.origin.external_url",
                            lambda *a, **k: (_ for _ in ()).throw(_loud()))
        return _diag_session()
    if stage == "exchange_code":
        _stub_provider(monkeypatch, exchange=_loud)
        return _diag_session()
    if stage == "accept_invitation":
        _stub_provider(monkeypatch)
        monkeypatch.setattr("app.routes.portal.accept_invitation",
                            lambda *a, **k: (_ for _ in ()).throw(_loud()))
        return _diag_session(invitation=True)
    if stage == "sign_in_with_subject":
        _stub_provider(monkeypatch)
        monkeypatch.setattr("app.portal.service.sign_in_with_subject",
                            lambda *a, **k: (_ for _ in ()).throw(_loud()))
        return _diag_session()
    if stage == "create_portal_session":
        _stub_provider(monkeypatch)
        monkeypatch.setattr("app.portal.service.sign_in_with_subject", lambda *a, **k: 1)
        monkeypatch.setattr("app.routes.portal.create_portal_session",
                            lambda *a, **k: (_ for _ in ()).throw(_loud()))
        return _diag_session()
    raise AssertionError(f"unhandled stage {stage}")


DIAG_STAGES = ["state_validation", "provider_lookup", "redirect_uri", "exchange_code",
               "accept_invitation", "sign_in_with_subject", "create_portal_session"]


@pytest.mark.parametrize("stage", DIAG_STAGES)
def test_every_callback_failure_stage_still_redirects_generically(stage, monkeypatch):
    """Requirement: browser behaviour is unchanged — one generic redirect, no session."""
    from app.routes.portal import portal_auth_callback

    session = _fail_at(monkeypatch, stage)
    sent_state = "MISMATCH" if stage == "state_validation" else SECRETS["state"]
    with _capture_diagnostics() as records:
        response = portal_auth_callback(_session_request(session), code=SECRETS["code"],
                                        state=sent_state)
    assert response.status_code == 303
    assert response.headers["location"] == "/portal/login?error=failed"
    assert "portal_session_token" not in session


@pytest.mark.parametrize("stage", DIAG_STAGES)
def test_every_callback_failure_stage_is_classified(stage, monkeypatch):
    from app.routes.portal import portal_auth_callback

    session = _fail_at(monkeypatch, stage)
    sent_state = "MISMATCH" if stage == "state_validation" else SECRETS["state"]
    with _capture_diagnostics() as records:
        portal_auth_callback(_session_request(session), code=SECRETS["code"], state=sent_state)
    messages = [r.getMessage() for r in records]
    assert len(messages) == 1, f"expected exactly one diagnostic line, got {len(messages)}"
    assert f"stage={stage}" in messages[0]
    expected_exc = "InvalidState" if stage == "state_validation" else "_LoudError"
    assert f"exception={expected_exc}" in messages[0]
    assert messages[0].startswith("portal_auth_callback_failed ")


@pytest.mark.parametrize("stage", DIAG_STAGES)
def test_no_sensitive_value_reaches_the_diagnostic_log(stage, monkeypatch):
    """The failing exception's text contains every secret; none may be emitted."""
    from app.routes.portal import portal_auth_callback

    session = _fail_at(monkeypatch, stage)
    sent_state = "MISMATCH" if stage == "state_validation" else SECRETS["state"]
    with _capture_diagnostics() as records:
        portal_auth_callback(_session_request(session), code=SECRETS["code"], state=sent_state)
    assert records, "the diagnostic emitted nothing to inspect"
    captured = "\n".join(f"{r.getMessage()} {r.args!r} {r.exc_text!r}" for r in records)
    for label, secret in SECRETS.items():
        assert secret not in captured, f"the {label} value reached the log"
    assert "user-agent" not in captured.lower() and "Bearer" not in captured


def test_the_category_is_derived_from_the_class_hierarchy_never_the_message():
    from app.routes.portal import _auth_failure_category

    class _Custom(ValueError):
        pass

    assert _auth_failure_category(_Custom("anything at all")) == "validation"
    assert _auth_failure_category(TimeoutError("x")) == "network"
    assert _auth_failure_category(Exception("x")) == "unexpected"

    class _Nasty(Exception):
        args = ("secret",)

        def __str__(self):
            raise AssertionError("__str__ must never be called by the diagnostic")

    assert _auth_failure_category(_Nasty()) == "unexpected"


def test_the_diagnostic_only_ever_emits_known_stage_literals():
    from app.routes.portal import _log_auth_failure

    with _capture_diagnostics() as records:
        _log_auth_failure("attacker=injected value", "ValueError", "validation")
    assert len(records) == 1
    assert "stage=unknown" in records[0].getMessage()
    assert "attacker" not in records[0].getMessage()


def test_the_diagnostic_never_raises():
    """A broken logger must not turn a handled auth failure into a 500."""
    from app.routes import portal as portal_routes

    class _Broken:
        def warning(self, *a, **k):
            raise RuntimeError("logging backend down")

    original = portal_routes.logger
    portal_routes.logger = _Broken()
    try:
        portal_routes._log_auth_failure("exchange_code", "ValueError", "validation")
    finally:
        portal_routes.logger = original


def test_the_callback_never_logs_exception_text_or_request_data():
    """Source-level guard: the diagnostic call sites may pass only the class name."""
    import inspect

    from app.routes import portal as portal_routes
    src = inspect.getsource(portal_routes.portal_auth_callback)
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    for forbidden in ("str(exc)", "repr(exc)", "exc.args", "%s\" % exc", "exc_info",
                      "format(exc)", "{exc}", "exception=True"):
        assert forbidden not in code, f"the callback may log {forbidden}"
    assert code.count("_log_auth_failure(") == 2
    assert "_log_auth_failure(stage, type(exc).__name__, _auth_failure_category(exc))" in code


def test_a_successful_callback_is_unchanged_and_logs_nothing(monkeypatch):
    """The success path still activates the invitation, sets the session and redirects to /portal."""
    from tests._portal_util import seed_staff_user
    from app.db import engine, households, people
    from app.portal.service import invite_portal_account
    from app.routes.portal import portal_auth_callback
    from sqlalchemy import insert

    sfx = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"Diag HH {sfx}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Diag {sfx}", active=True)
                        .returning(people.c.id)).scalar_one()
    _, invitation = invite_portal_account(
        person_id=pid, household_id=hid, email=f"diag-{sfx}@e.test", display_name="Diag Client",
        access_type="self", invited_by_user_id=seed_staff_user(), permissions={"documents": True})

    subject = f"microsoft:DIAG-{sfx}"

    class _P:
        def exchange_code(self, **kw):
            return SimpleNamespace(subject=subject, mfa_verified=True)

    monkeypatch.setattr("app.portal.providers.PORTAL_IDENTITY_PROVIDERS.get", lambda key: _P())
    session = {"portal_oidc_state": "S", "portal_oidc_nonce": "N", "portal_oidc_verifier": "V",
               "portal_oidc_invitation": invitation}
    with _capture_diagnostics() as records:
        response = portal_auth_callback(_session_request(session), code="c", state="S")

    assert response.status_code == 303
    assert response.headers["location"] == "/portal"
    assert session.get("portal_session_token"), "the successful callback established no session"
    assert records == [], "a successful sign-in emitted a failure diagnostic"
