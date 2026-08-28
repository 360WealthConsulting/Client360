"""Portal UX integration — one coherent 360Plus client shell: branded nav across Documents / Upload /
Messages / Profile / Logout, active-state highlighting, browser sign-out, and no leakage of internal
identifiers, tokens, or stack traces to the client.
"""
from __future__ import annotations

from app.portal.service import create_portal_session, resolve_portal_session
from app.routes.portal import (
    portal_documents_page,
    portal_logout_browser,
    portal_page,
)
from tests._portal_util import fake_request, render, seed_portal_account, seed_staff_user


def test_shell_nav_is_coherent_and_branded(portal_documents_upload_on, portal_messaging_on):
    """The nav is now gate-aware, so the enabled-surface shell is asserted with those gates ON."""
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_page("", fake_request("/portal/"), principal))
    # 360Plus brand + the coherent primary nav + a sign-out control.
    assert "360Plus" in html
    for label, href in [("Documents", "/portal/documents"), ("Upload", "/portal/upload"),
                        ("Messages", "/portal/messages"), ("Profile", "/portal/profile")]:
        assert f'href="{href}"' in html and f">{label}</a>" in html
    assert 'action="/portal/logout"' in html and "Sign out" in html


def test_active_nav_item_is_marked(portal_master_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_documents_page(fake_request("/portal/documents"), principal))
    assert '<a href="/portal/documents" class="active" aria-current="page">Documents</a>' in html


def test_browser_logout_revokes_session_and_redirects_to_login():
    account_id, _, _, _ = seed_portal_account(seed_staff_user())
    token = create_portal_session(account_id, device_fingerprint="ux-logout")
    assert resolve_portal_session(token) is not None
    req = fake_request("/portal/logout", "POST", session={"portal_session_token": token})
    resp = portal_logout_browser(req)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portal/login"
    assert resolve_portal_session(token) is None        # session truly invalidated server-side


def test_client_pages_do_not_leak_tokens_or_stack_traces():
    account_id, principal, _, _ = seed_portal_account(seed_staff_user())
    token = create_portal_session(account_id, device_fingerprint="ux-leak")
    for html in (render(portal_page("", fake_request("/portal/"), principal)),
                 render(portal_documents_page(fake_request("/portal/documents"), principal))):
        assert token not in html                        # never render the raw session token
        assert "Traceback" not in html and "storage_uri" not in html
        assert "portal_session_token" not in html
