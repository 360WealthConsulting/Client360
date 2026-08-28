"""GET /portal — the authenticated client landing page.

``/portal/`` already rendered the dashboard (PAGE_NAMES[""]), but the bare ``/portal`` matched no
route because the catch-all pattern requires the trailing slash. This adds that one URL and nothing
else: same dashboard read model, same template, same context, same auth and gate.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import (
    documents,
    engine,
    people,
    portal_access_grants,
    portal_accounts,
    portal_messages,
    portal_threads,
    vault_documents,
)
from app.routes.portal import current_portal, portal_home
from app.security.models import Principal
from tests._portal_util import fake_request, render
from tests.test_portal_vault import _Env


@pytest.fixture
def env():
    e = _Env()
    try:
        yield e
    finally:
        e.cleanup()


def _open(principal):
    """Call the real route with the PortalPrincipal the middleware would have resolved."""
    req = fake_request("/portal")
    req.state.portal_principal = principal
    return portal_home(req, principal)


def _canonical_doc(person_id, name="INTERNAL-WORKPAPER.pdf"):
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            person_id=person_id, original_name=name, stored_name=f"{uuid.uuid4().hex}.pdf",
            storage_path=f"/srv/docs/{uuid.uuid4().hex}.pdf", size_bytes=11,
            sha256=uuid.uuid4().hex * 2, archived=False, status="active",
        ).returning(documents.c.id)).scalar_one()


# --- registration -------------------------------------------------------------------------------
def test_get_portal_is_registered_exactly_once():
    from app.main import app
    matches = [r for r in app.routes if getattr(r, "path", None) == "/portal"]
    assert len(matches) == 1
    assert sorted(matches[0].methods) == ["GET"]


def test_route_count_is_1184():  # +5 client email one-time-code sign-in (POST /portal/login, GET /portal/activate, GET+POST /portal/verify, POST /portal/verify/resend)  # -2 removed client Microsoft auth (/portal/auth/start, /portal/auth/callback)
    from app.main import app
    assert len(app.routes) == 1184  # +2 external IdP portal auth (start + callback)  # +1 staff client-search for the portal invite form (GET /admin/client-portal/client-search)  # +1 staff Add New Client creation (POST /admin/client-portal/create-client)  # +5 client email one-time-code sign-in (POST /portal/login, GET /portal/activate, GET+POST /portal/verify, POST /portal/verify/resend)  # -2 removed client Microsoft auth (/portal/auth/start, /portal/auth/callback)


def test_no_portal_dashboard_route_was_added():
    """The bare landing URL only -- /portal/dashboard is not part of this change."""
    from app.main import app
    assert not any(getattr(r, "path", None) == "/portal/dashboard" for r in app.routes)


# --- authentication ------------------------------------------------------------------------------
def test_unauthenticated_is_rejected_by_the_portal_dependency():
    """current_portal raises 401; the middleware turns that into 303 /portal/login for HTML."""
    from fastapi import HTTPException
    req = fake_request("/portal")
    req.state.portal_principal = None
    with pytest.raises(HTTPException) as exc:
        current_portal(req)
    assert exc.value.status_code == 401


def test_middleware_redirects_unauthenticated_html_to_portal_login():
    import inspect

    from app.security import middleware as mw
    src = inspect.getsource(mw.AuthenticationMiddleware.dispatch)
    assert 'RedirectResponse("/portal/login", 303)' in src
    assert 'request.url.path.startswith("/portal")' in src


def test_a_staff_session_is_not_portal_authentication():
    """A staff Principal carries no portal_principal, so the landing page refuses it."""
    from fastapi import HTTPException
    req = fake_request("/portal", state_principal=Principal(1, "s@f.test", "Staff",
                                                            frozenset({"client.read"})))
    req.state.portal_principal = None
    with pytest.raises(HTTPException) as exc:
        current_portal(req)
    assert exc.value.status_code == 401


def test_the_route_takes_no_client_identifier_from_the_browser():
    import inspect
    params = set(inspect.signature(portal_home).parameters)
    assert params == {"request", "principal"}
    for forbidden in ("person_id", "household_id", "account_id"):
        assert forbidden not in params


# --- rendering -----------------------------------------------------------------------------------
def test_a_authenticated_client_with_data_renders_the_dashboard(env):
    _, principal, pid, _ = env.account()
    env.staff_doc(pid, client_visible=True)
    html = render(_open(principal))
    assert "Welcome, Portal Client" in html
    assert 'href="/portal/documents"' in html      # existing navigation preserved
    assert 'href="/portal/messages"' in html
    assert 'href="/portal/tasks"' in html


def test_b_authenticated_client_with_no_data_renders_a_safe_empty_dashboard(env):
    _, principal, _pid, _ = env.account()
    html = render(_open(principal))
    assert "Welcome, Portal Client" in html
    assert "Traceback" not in html and "Error" not in html


def test_c_account_with_no_active_grant_renders_an_empty_dashboard(env):
    """No grant -> portal_scope resolves nothing -> every panel is empty. No email fallback."""
    account_id, principal, pid, _ = env.account()
    env.staff_doc(pid, client_visible=True)
    with engine.begin() as c:
        c.execute(portal_access_grants.delete().where(
            portal_access_grants.c.portal_account_id == account_id))
    html = render(_open(principal))
    assert "Welcome, Portal Client" in html
    from app.portal.service import dashboard
    data = dashboard(principal)
    assert data["documents"] == [] and data["messages"] == [] and data["tasks"] == []


# --- isolation -------------------------------------------------------------------------------------
def test_client_a_landing_page_shows_no_client_b_data(env):
    _, a_principal, a_pid, _ = env.account()
    _, _b_principal, b_pid, _ = env.account()
    env.staff_doc(a_pid, client_visible=True)
    b_doc = env.staff_doc(b_pid, client_visible=True)
    with engine.connect() as c:
        b_name = c.scalar(select(people.c.full_name).where(people.c.id == b_pid))

    from app.portal.service import dashboard
    data = dashboard(a_principal)
    assert b_doc not in {d["id"] for d in data["documents"]}
    html = render(_open(a_principal))
    assert str(b_doc) not in html or b_name not in html


def test_canonical_staff_document_is_absent_from_the_landing_page(env):
    """The 88305f5 vault-only path must hold on this surface too."""
    _, principal, pid, _ = env.account()
    _canonical_doc(pid, name="LANDING-LEAK.pdf")
    html = render(_open(principal))
    assert "LANDING-LEAK" not in html
    assert "/srv/docs/" not in html


def test_documents_panel_still_requires_the_documents_grant(env):
    _, principal, pid, _ = env.account(permissions={"messages": True})
    env.staff_doc(pid, client_visible=True)
    from app.portal.service import dashboard
    assert dashboard(principal)["documents"] == []
    render(_open(principal))          # renders without error


# --- gate + read-only ---------------------------------------------------------------------------------
def test_the_landing_path_is_subject_to_the_portal_gate():
    """The gate is applied by the middleware to every /portal path before the route runs."""
    import inspect

    from app.security import middleware as mw
    src = inspect.getsource(mw.AuthenticationMiddleware.dispatch)
    assert "portal_gate" in src and "evaluate(" in src
    from app.services.features import portal_gate
    assert callable(portal_gate.evaluate)


def test_rendering_the_landing_page_writes_no_business_data(env):
    """Session last-seen is touched by resolve_portal_session during AUTHENTICATION, which this
    test deliberately does not exercise -- the route is called with an already-resolved principal,
    so any change here would be a route/business write."""
    _, principal, pid, _ = env.account()
    env.staff_doc(pid, client_visible=True)
    _canonical_doc(pid)

    def counts():
        with engine.connect() as c:
            return {t.name: c.scalar(select(func.count()).select_from(t))
                    for t in (portal_accounts, portal_access_grants, people, documents,
                              vault_documents, portal_messages, portal_threads)}

    before = counts()
    render(_open(principal))
    render(_open(principal))
    assert counts() == before


def test_the_landing_page_reuses_the_existing_dashboard_read_model():
    import inspect
    src = inspect.getsource(portal_home)
    assert "portal_page(" in src            # delegates, no second read model
    assert "select(" not in src             # no direct table query in the route
