"""Browser smoke tests — the unauthenticated surface.

Covers what a real browser can verify without a session: the server serves, static
assets load, and protected records redirect an unauthenticated visitor to login.

Authenticated end-to-end flows (search -> profile -> notes -> tasks) are NOT here:
the app authenticates through an external identity provider and exposes no
test-login path, so a browser cannot obtain a session without a product decision
on a test-authentication strategy. Tracked in docs/E2E.md (Authenticated E2E).
"""
from __future__ import annotations


def test_health_endpoint_serves(page, live_server):
    response = page.goto(f"{live_server}/health")
    assert response is not None and response.ok
    assert "ok" in page.content().lower()


def test_static_stylesheet_loads(page, live_server):
    response = page.goto(f"{live_server}/static/css/workspace.css")
    assert response is not None and response.ok


def test_unauthenticated_record_redirects_to_login(page, live_server):
    # AuthenticationMiddleware redirects a browser (Accept: text/html) GET of a
    # protected record to the login route; the visitor must never see client data.
    page.goto(f"{live_server}/people/1")
    assert "/auth/login" in page.url


def test_unauthenticated_home_redirects_to_login(page, live_server):
    page.goto(f"{live_server}/")
    assert "/auth/login" in page.url
