"""Authentication redirect URIs must come from configuration, not the inbound Host header.

``request.url_for()`` builds its base from ``scope["scheme"]`` and the Host header. Nothing in the
application validates that header (no ``TrustedHostMiddleware``; ``ALLOWED_HOSTS`` is documented but
unread), so before this change a spoofed Host produced a spoofed OAuth ``redirect_uri``. These tests
pin the replacement: a validated ``PUBLIC_BASE_URL`` origin, applied identically at both legs of the
authorization-code flow, failing closed rather than degrading to the Host header.
"""
from __future__ import annotations

import pytest

from app.security.origin import (
    CanonicalOriginError,
    canonical_origin,
    canonical_origin_status,
    external_url,
    validate_origin,
)

PROD_ORIGIN = "https://app.360wealthconsulting.com"
PROD_CALLBACK = "https://app.360wealthconsulting.com/portal/auth/callback"


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "development")
    return monkeypatch


def _request(host="app.360wealthconsulting.com", scheme="https", headers=None):
    """A request whose ``url_for`` behaves like Starlette's: scheme + Host header + routed path."""
    from tests._portal_util import fake_request
    req = fake_request()
    req.headers = {**(headers or {}), "host": host}
    req.url_for = lambda name: f"{scheme}://{host}/portal/auth/callback"
    return req


# --- 1, 2: the canonical production callback -------------------------------------------

def test_production_callback_is_https(clean_env):
    clean_env.setenv("PUBLIC_BASE_URL", PROD_ORIGIN)
    clean_env.setenv("CLIENT360_ENVIRONMENT", "production")
    assert external_url(_request(), "portal_auth_callback").startswith("https://")


def test_portal_callback_is_exactly_the_registered_uri(clean_env):
    clean_env.setenv("PUBLIC_BASE_URL", PROD_ORIGIN)
    clean_env.setenv("CLIENT360_ENVIRONMENT", "production")
    built = external_url(_request(), "portal_auth_callback")
    assert built == PROD_CALLBACK
    assert not built.endswith("/"), "a trailing slash breaks exact redirect-URI matching"


def test_trailing_slash_on_the_configured_origin_is_normalized_away(clean_env):
    clean_env.setenv("PUBLIC_BASE_URL", PROD_ORIGIN + "/")
    assert external_url(_request(), "portal_auth_callback") == PROD_CALLBACK


# --- 3, 4: both legs must agree byte for byte -------------------------------------------

def test_auth_start_and_token_exchange_send_the_identical_redirect_uri(clean_env, monkeypatch):
    """Microsoft rejects the exchange when the two differ. Capture what each leg actually sends."""
    from app.routes import portal as portal_routes

    clean_env.setenv("PUBLIC_BASE_URL", PROD_ORIGIN)
    clean_env.setenv("CLIENT360_ENVIRONMENT", "production")
    # /portal/auth/start refuses before minting anything unless the portal is production-ready, so a
    # gated portal cannot consume a client's single-use invitation. This test drives the AVAILABLE
    # path; the redirect-URI assertions below are unchanged.
    monkeypatch.setattr("app.portal.gate.production_ready", lambda: True)
    sent = {}

    class _P:
        def authorization_url(self, **kw):
            sent["start"] = kw["redirect_uri"]
            return "https://idp.example/authorize"

        def exchange_code(self, **kw):
            sent["exchange"] = kw["redirect_uri"]
            raise ValueError("stop here — the redirect_uri is what we are asserting")

    monkeypatch.setattr("app.portal.providers.PORTAL_IDENTITY_PROVIDERS.get", lambda key: _P())

    session = {}
    start_req = _request()
    start_req.scope = {}
    start_req.session = session
    portal_routes.portal_auth_start(start_req)

    cb_req = _request()
    cb_req.session = session
    portal_routes.portal_auth_callback(cb_req, code="c", state=session["portal_oidc_state"])

    assert sent["start"] == PROD_CALLBACK
    assert sent["exchange"] == PROD_CALLBACK
    assert sent["start"] == sent["exchange"]


# --- 5, 6, 7: spoofing ------------------------------------------------------------------

def test_spoofed_host_cannot_change_the_callback(clean_env):
    clean_env.setenv("PUBLIC_BASE_URL", PROD_ORIGIN)
    spoofed = _request(host="attacker.example")
    assert external_url(spoofed, "portal_auth_callback") == PROD_CALLBACK
    assert "attacker.example" not in external_url(spoofed, "portal_auth_callback")


def test_spoofed_x_forwarded_host_cannot_change_the_callback(clean_env):
    clean_env.setenv("PUBLIC_BASE_URL", PROD_ORIGIN)
    req = _request(host="attacker.example", headers={"x-forwarded-host": "attacker.example"})
    assert external_url(req, "portal_auth_callback") == PROD_CALLBACK


def test_spoofed_x_forwarded_proto_cannot_downgrade_the_callback(clean_env):
    """Even if the scheme reaching the app is http, the configured origin decides."""
    clean_env.setenv("PUBLIC_BASE_URL", PROD_ORIGIN)
    req = _request(scheme="http", headers={"x-forwarded-proto": "http"})
    built = external_url(req, "portal_auth_callback")
    assert built == PROD_CALLBACK and built.startswith("https://")


# --- 8, 9: validation and fail-closed ---------------------------------------------------

@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "data:text/html,x",
    "//attacker.example",                                  # scheme-relative
    "https://user:pw@app.example.com",                     # embedded credentials
    "https://app.example.com/some/path",                   # path
    "https://app.example.com/?next=https://evil.example",  # query
    "https://app.example.com/#frag",                       # fragment
    "https://",                                            # no host
    "not a url",
])
def test_malformed_origin_is_refused(clean_env, bad):
    clean_env.setenv("PUBLIC_BASE_URL", bad)
    with pytest.raises(CanonicalOriginError):
        canonical_origin()
    ok, err = canonical_origin_status()
    assert ok is False and err, "startup must report the problem instead of raising"


def test_an_empty_value_means_unconfigured_not_malformed(clean_env):
    """PUBLIC_BASE_URL= in an env file is "not set", which is a valid dev posture, not an error."""
    clean_env.setenv("PUBLIC_BASE_URL", "   ")
    assert canonical_origin() is None
    ok, err = canonical_origin_status()
    assert ok is False and err is None
    # ...but in production, unconfigured still fails closed at the point of use.
    clean_env.setenv("CLIENT360_ENVIRONMENT", "production")
    with pytest.raises(CanonicalOriginError):
        external_url(_request(), "portal_auth_callback")


def test_malformed_origin_never_degrades_to_the_host_header(clean_env):
    """Fail closed: a broken value must not silently fall back to url_for()."""
    clean_env.setenv("PUBLIC_BASE_URL", "https://app.example.com/oops/path")
    with pytest.raises(CanonicalOriginError):
        external_url(_request(host="attacker.example"), "portal_auth_callback")


def test_http_origin_is_refused_in_production_but_allowed_in_development(clean_env):
    clean_env.setenv("PUBLIC_BASE_URL", "http://app.example.com")
    assert validate_origin("http://app.example.com", production=False) == "http://app.example.com"
    clean_env.setenv("CLIENT360_ENVIRONMENT", "production")
    with pytest.raises(CanonicalOriginError, match="https"):
        canonical_origin()


def test_production_without_a_canonical_origin_fails_closed(clean_env):
    clean_env.setenv("CLIENT360_ENVIRONMENT", "production")
    with pytest.raises(CanonicalOriginError):
        external_url(_request(host="attacker.example"), "portal_auth_callback")


def test_auth_start_reports_unavailable_when_the_origin_is_unusable(clean_env, monkeypatch):
    """The route must not 500, and must not fall back to a Host-derived URI."""
    from app.routes import portal as portal_routes

    class _P:
        def authorization_url(self, **kw):
            raise AssertionError("must not reach the provider with an unusable origin")

    monkeypatch.setattr("app.portal.providers.PORTAL_IDENTITY_PROVIDERS.get", lambda key: _P())
    clean_env.setenv("PUBLIC_BASE_URL", "javascript:alert(1)")
    req = _request()
    req.session = {}
    response = portal_routes.portal_auth_start(req)
    assert response.status_code == 303
    assert response.headers["location"] == "/portal/login?error=unavailable"


# --- 10: local/dev fallback -------------------------------------------------------------

def test_development_without_a_canonical_origin_still_uses_url_for(clean_env):
    req = _request(host="localhost:8000", scheme="http")
    assert external_url(req, "portal_auth_callback") == "http://localhost:8000/portal/auth/callback"


def test_a_port_in_the_origin_is_preserved(clean_env):
    clean_env.setenv("PUBLIC_BASE_URL", "https://staging.example.com:8443")
    assert external_url(_request(), "portal_auth_callback") == \
        "https://staging.example.com:8443/portal/auth/callback"


# --- 11: no open redirect ---------------------------------------------------------------

def test_the_route_target_is_server_selected_not_caller_supplied():
    """external_url takes a route NAME. A caller-supplied absolute URL cannot pass through it."""
    import inspect

    from app.routes import portal as portal_routes
    for fn in (portal_routes.portal_auth_start, portal_routes.portal_auth_callback):
        src = inspect.getsource(fn)
        assert 'external_url(request, "portal_auth_callback")' in src or \
               'redirect_uri = external_url' in src, f"{fn.__name__} must use the helper"
    src = inspect.getsource(external_url)
    assert "url_for(route_name)" in src, "the path must come from the app's own routing table"


def test_an_unknown_route_name_cannot_be_smuggled_in_as_a_url(clean_env):
    """Passing a URL where a route name belongs must not produce that URL."""
    clean_env.setenv("PUBLIC_BASE_URL", PROD_ORIGIN)
    req = _request()
    req.url_for = lambda name: (_ for _ in ()).throw(Exception("no such route: " + name))
    with pytest.raises(Exception, match="no such route"):
        external_url(req, "https://evil.example/steal")


# --- posture the fix does NOT change ----------------------------------------------------

def test_the_app_still_installs_no_trusted_host_middleware(clean_env):
    """Documents the remaining gap: ALLOWED_HOSTS is deployment metadata nothing enforces.

    The canonical origin removes the Host header from AUTH redirect URIs, which is what mattered
    here. It does not make the Host header trustworthy in general."""
    from app.main import app
    names = {m.cls.__name__ for m in app.user_middleware}
    assert "TrustedHostMiddleware" not in names, \
        "if this is added, update docs/CLIENT_PORTAL_SECURITY.md and this test"


def test_staff_oidc_still_uses_url_for(clean_env):
    """Staff behaviour is deliberately unchanged — the registered staff callback URI could not be
    established from repository facts, so it was reported rather than guessed."""
    import inspect

    from app.routes import auth as auth_routes
    src = inspect.getsource(auth_routes)
    assert 'request.url_for("auth_callback")' in src
    assert "external_url" not in src
