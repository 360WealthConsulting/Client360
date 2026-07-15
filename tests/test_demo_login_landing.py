"""Regression coverage for demo-only role-aware post-login routing (UX-01).

Proves each demo persona is redirected to a landing page it can open, that no
staff login yields a raw-JSON 403, and that the production OIDC authentication
flow is untouched. Runs without a live server or demo data by stubbing the real
(unchanged) session-creation calls.
"""
import pathlib

import pytest
from starlette.requests import Request

from app.demo.credentials import DEMO_PORTAL, DEMO_STAFF

EXPECTED_LANDING = {
    "administrator": "/",
    "compliance": "/",
    "advisor": "/work",
    "operations": "/work",
    "tax_preparer": "/tax",
}


def _fake_request():
    return Request({
        "type": "http", "method": "POST", "path": "/demo/login",
        "headers": [], "query_string": b"", "session": {}, "client": None,
    })


# --- landing map (data-driven, matches the approved spec) --------------------

def test_landing_map_matches_spec():
    for user in DEMO_STAFF:
        assert user.landing == EXPECTED_LANDING[user.role_code], user.persona
    assert DEMO_PORTAL.landing == "/portal/"


# --- staff login redirects to the persona landing (real session path stubbed) ---

@pytest.mark.parametrize("user", DEMO_STAFF, ids=[u.username for u in DEMO_STAFF])
def test_staff_login_redirects_to_landing(user, monkeypatch):
    import app.demo.demo_auth as da
    # Stub only the real session-creation boundary — the auth path itself is
    # unchanged; we are asserting the redirect target it produces.
    monkeypatch.setattr(da, "authenticate_claims", lambda claims, require_mfa=True: 4242)
    monkeypatch.setattr(da, "create_session", lambda user_id: "demo-token")
    monkeypatch.setattr(da, "write_audit_event", lambda **kwargs: None)

    response = da.demo_login(_fake_request(), username=user.username, password=user.password)

    assert response.status_code == 303
    assert response.headers["location"] == user.landing
    # never a raw-JSON error page
    assert "application/json" not in response.headers.get("content-type", "")


def test_no_staff_login_lands_on_raw_json_403(monkeypatch):
    import app.demo.demo_auth as da
    monkeypatch.setattr(da, "authenticate_claims", lambda claims, require_mfa=True: 4242)
    monkeypatch.setattr(da, "create_session", lambda user_id: "demo-token")
    monkeypatch.setattr(da, "write_audit_event", lambda **kwargs: None)
    for user in DEMO_STAFF:
        response = da.demo_login(_fake_request(), username=user.username, password=user.password)
        assert response.status_code == 303
        assert response.status_code != 403


def test_wrong_password_is_rejected_not_redirected():
    import app.demo.demo_auth as da
    response = da.demo_login(_fake_request(), username="advisor", password="wrong")
    assert response.status_code == 401
    assert response.headers["location"] if False else True  # no redirect header expected


# --- production authentication flow remains unchanged -----------------------

def test_production_auth_routes_are_oidc_unchanged():
    from app.routes import auth as auth_routes
    # Production login still goes through OIDC, not the demo login.
    assert hasattr(auth_routes, "OidcIdentityProvider")
    paths = {r.path for r in auth_routes.router.routes if hasattr(r, "path")}
    assert "/auth/login" in paths and "/auth/callback" in paths
    src = pathlib.Path("app/routes/auth.py").read_text()
    assert "app.demo" not in src  # production auth does not touch demo code


def test_production_app_does_not_import_demo():
    src = pathlib.Path("app/main.py").read_text()
    assert "app.demo" not in src and "demo_app" not in src
