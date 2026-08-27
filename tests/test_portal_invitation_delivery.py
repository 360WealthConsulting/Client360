"""A newly created portal invitation must actually be deliverable to the intended client.

``invite_portal_account`` mints a random token, stores only ``SHA-256(token)``, and returns the raw
value once. ``portal_admin_invite_form`` assigned it to ``_token`` and dropped it — so the only
usable activation credential was destroyed at the moment of creation and no invitation could ever be
activated. There is no production-capable email channel to send it with
(``app/services/notification_providers.py`` registers ``email`` as a ``DisabledNotificationHook``),
so the link is handed to the staff member who created it, exactly once, through a server-side store.

What these tests hold in place: the link is built on the CANONICAL external origin (never the
inbound Host header), the token is URL-encoded, the database still holds only the hash, the raw
token never reaches an audit event or a redirect URL, the handoff cannot be read twice, and the
existing Microsoft authorization-code activation path still consumes the invitation unchanged.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.db import engine, households, people, portal_accounts, portal_invitations, users
from app.portal import invitation_handoff
from app.portal.service import _hash, accept_invitation, invite_portal_account
from app.routes.portal_admin import (
    HANDOFF_SESSION_KEY,
    portal_admin_home,
    portal_admin_invite_form,
)
from app.security.models import Principal

CANONICAL = "https://portal.example.com"


# --- seeding ---------------------------------------------------------------------

def _staff_user() -> int:
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"staff-{sfx}@example.com", normalized_email=f"staff-{sfx}@example.com",
            display_name="Invite Staff", auth_subject=f"staff-{sfx}", status="active")
            .returning(users.c.id)).scalar_one()


def _person_household():
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"Invite {sfx}", active=True)
                        .returning(people.c.id)).scalar_one()
        hid = c.execute(households.insert().values(name=f"HH {sfx}").returning(households.c.id)
                        ).scalar_one()
    return pid, hid


def _request(session=None):
    return SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}"),
        client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"},
        query_params={}, session={} if session is None else session,
        url_for=lambda name: "http://inbound-host.invalid/portal/login")


def _principal(uid):
    return Principal(uid, "staff@example.com", "Staff", frozenset({"client.write", "client.read",
                                                                   "record.write_all"}))


@pytest.fixture
def canonical_origin(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", CANONICAL)
    yield CANONICAL


def _invite(request, *, name="Jane Client"):
    pid, hid = _person_household()
    response = portal_admin_invite_form(
        request=request, person_id=pid, household_id=hid,
        email=f"jane-{uuid.uuid4().hex[:6]}@example.com", display_name=name,
        access_type="self", organization_id=None, principal=_principal(_staff_user()))
    return response, pid


def _collect(session):
    """Render the admin home the way the browser would, and return (html, context)."""
    request = _request(session)
    with patch("app.routes.portal_admin.templates.TemplateResponse") as render:
        portal_admin_home(request, principal=_principal(_staff_user()))
    return render.call_args.kwargs["context"]


# --- the activation URL ------------------------------------------------------------

def test_the_activation_url_uses_the_canonical_external_origin(canonical_origin):
    """Never the inbound Host header: a link built from an attacker-supplied Host would hand the
    client's single-use token to whatever origin that header named."""
    session: dict = {}
    _invite(_request(session))
    activation = _collect(session)["activation"]
    assert activation is not None, "no activation link was handed to the inviting staff member"
    assert activation["url"].startswith(f"{CANONICAL}/portal/login?invitation=")
    assert "inbound-host.invalid" not in activation["url"]


def test_the_activation_url_targets_the_existing_browser_activation_entry_point(canonical_origin):
    """/portal/login?invitation= is the existing wired path into auth/start → PKCE → callback."""
    session: dict = {}
    _invite(_request(session))
    assert "/portal/login?invitation=" in _collect(session)["activation"]["url"]


def test_the_raw_token_is_url_encoded_in_the_link(canonical_origin):
    """token_urlsafe output is already URL-safe, but the encoding must not be left to luck: a token
    containing &, =, /, +, ? or # would otherwise split or truncate the query string."""
    from app.routes.portal_admin import _activation_url
    hostile = "a b&c=d/e+f?g#h"
    url = _activation_url(_request(), hostile)
    assert url == f"{CANONICAL}/portal/login?invitation=a%20b%26c%3Dd%2Fe%2Bf%3Fg%23h"
    assert hostile not in url, "the raw token was placed in the query string unencoded"
    # ...and it survives the round trip intact.
    from urllib.parse import parse_qs, urlsplit
    assert parse_qs(urlsplit(url).query)["invitation"][0] == hostile


def test_the_link_carries_a_token_that_actually_activates_the_account(canonical_origin):
    """End to end: the link handed to staff must be the credential accept_invitation expects."""
    from urllib.parse import parse_qs, urlsplit
    session: dict = {}
    _, person_id = _invite(_request(session))
    url = _collect(session)["activation"]["url"]
    token = parse_qs(urlsplit(url).query)["invitation"][0]
    subject = f"microsoft:OID-{uuid.uuid4().hex[:10]}"      # unique: auth_subject is UNIQUE
    account_id = accept_invitation(token, subject, True)
    with engine.connect() as c:
        row = c.execute(select(portal_accounts.c.status, portal_accounts.c.auth_subject)
                        .where(portal_accounts.c.id == account_id)).mappings().one()
    assert row["status"] == "active" and row["auth_subject"] == subject


# --- the database still holds only the hash -----------------------------------------

def test_the_database_stores_only_the_token_hash(canonical_origin):
    from urllib.parse import parse_qs, urlsplit
    session: dict = {}
    _invite(_request(session))
    token = parse_qs(urlsplit(_collect(session)["activation"]["url"]).query)["invitation"][0]
    with engine.connect() as c:
        row = c.execute(select(portal_invitations).order_by(portal_invitations.c.id.desc())
                        ).mappings().first()
    stored = {str(v) for v in row.values()}
    assert token not in stored, "the raw invitation token was persisted in plaintext"
    assert row["token_hash"] == _hash(token)
    assert not any(hasattr(row, col) for col in ("token", "raw_token", "activation_url"))


def test_no_plaintext_token_column_exists_on_portal_invitations():
    """A regression guard against 'just add a column' as a future delivery shortcut."""
    columns = set(portal_invitations.c.keys())
    for forbidden in ("token", "raw_token", "invitation_url", "activation_url", "token_plain"):
        assert forbidden not in columns, f"portal_invitations gained a plaintext column: {forbidden}"


# --- the raw token never leaks -------------------------------------------------------

def test_the_raw_token_never_reaches_the_redirect_url(canonical_origin):
    session: dict = {}
    response, _ = _invite(_request(session))
    token = session[HANDOFF_SESSION_KEY]
    assert response.status_code == 303
    location = response.headers["location"]
    assert "invitation" not in location and "token" not in location
    activation_url = invitation_handoff.take(token) or {}
    assert activation_url.get("url", "") not in location


def test_the_raw_token_never_reaches_audit_metadata(canonical_origin):
    """The audit event records who invited whom — never the credential."""
    captured = []
    session: dict = {}
    with patch("app.routes.portal_admin.write_audit_event",
               side_effect=lambda **kw: captured.append(kw)):
        _invite(_request(session))
    url = _collect(session)["activation"]["url"]
    token = url.split("invitation=")[1]
    assert captured, "the invitation was not audited at all"
    for event in captured:
        serialized = repr(event)
        assert token not in serialized, "the raw token was written into an audit event"
        assert "invitation" not in repr(event.get("metadata") or {})


def test_the_session_cookie_holds_an_opaque_handle_not_the_link(canonical_origin):
    """Starlette's session is SIGNED, not encrypted — a token stored there would reach the browser."""
    session: dict = {}
    _invite(_request(session))
    handle = session[HANDOFF_SESSION_KEY]
    assert isinstance(handle, str) and handle
    assert "http" not in handle and "invitation" not in handle and "/" not in handle


# --- the handoff is genuinely one-time ------------------------------------------------

def test_the_activation_link_cannot_be_retrieved_twice(canonical_origin):
    session: dict = {}
    _invite(_request(session))
    assert _collect(session)["activation"] is not None      # first load shows it...
    assert _collect(session)["activation"] is None          # ...every later load does not
    assert HANDOFF_SESSION_KEY not in session, "the handle survived the read"


def test_a_fresh_admin_page_load_shows_no_activation_link(canonical_origin):
    assert _collect({})["activation"] is None


def test_the_store_forgets_an_uncollected_link_after_its_ttl():
    handle = invitation_handoff.stash({"url": "https://x/portal/login?invitation=t"}, now=1000.0)
    assert invitation_handoff.take(handle, now=1000.0 + invitation_handoff.HANDOFF_TTL_SECONDS - 1)
    handle = invitation_handoff.stash({"url": "https://x/portal/login?invitation=t"}, now=2000.0)
    assert invitation_handoff.take(
        handle, now=2000.0 + invitation_handoff.HANDOFF_TTL_SECONDS + 1) is None


def test_an_unknown_or_reused_handle_yields_nothing():
    assert invitation_handoff.take(None) is None
    assert invitation_handoff.take("") is None
    assert invitation_handoff.take("not-a-real-handle") is None
    handle = invitation_handoff.stash({"url": "u"})
    assert invitation_handoff.take(handle) == {"url": "u"}
    assert invitation_handoff.take(handle) is None, "a handle was redeemable twice"


def test_the_store_is_bounded():
    for _ in range(invitation_handoff.MAX_PENDING + 20):
        invitation_handoff.stash({"url": "u"})
    assert invitation_handoff.pending_count() <= invitation_handoff.MAX_PENDING


# --- delivery failure is safe ----------------------------------------------------------

def test_a_link_that_cannot_be_built_still_invites_and_warns(monkeypatch):
    """PUBLIC_BASE_URL missing in production: the account IS created; only the convenience link is
    lost. Staff are told, and nothing half-created is left behind."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr("app.security.origin._is_production", lambda: True)
    session: dict = {}
    response, person_id = _invite(_request(session))
    assert response.status_code == 303
    assert "invited=" in response.headers["location"]
    assert "error=Activation+link+unavailable" in response.headers["location"]
    assert HANDOFF_SESSION_KEY not in session
    with engine.connect() as c:
        row = c.execute(select(portal_accounts.c.status)
                        .where(portal_accounts.c.person_id == person_id)).mappings().first()
    assert row and row["status"] == "invited", "the invitation was rolled back by a link failure"


def test_a_request_without_a_session_does_not_break_the_invite(canonical_origin):
    """Direct service/test callers have no session middleware; inviting must still succeed."""
    request = _request()
    del request.session
    response, person_id = _invite(request)
    assert response.status_code == 303 and "invited=" in response.headers["location"]
    with engine.connect() as c:
        row = c.execute(select(portal_accounts.c.status)
                        .where(portal_accounts.c.person_id == person_id)).mappings().first()
    assert row and row["status"] == "invited"


# --- nothing else moved -----------------------------------------------------------------

def test_the_json_invite_endpoint_still_returns_no_token():
    """POST /invite is a general API response and must never carry the credential."""
    import inspect

    from app.routes import portal_admin
    src = inspect.getsource(portal_admin.portal_admin_invite)
    assert '"account_id": account_id, "status": "invited"' in src
    assert "raw_token" not in src and "_activation_url" not in src


def test_microsoft_activation_still_consumes_the_invitation_through_the_callback(canonical_origin):
    """The existing authorization-code path is unchanged: the callback pops the session invitation
    and calls accept_invitation with the verified subject."""
    import inspect

    from app.routes import portal as portal_routes
    src = inspect.getsource(portal_routes.portal_auth_callback)
    assert 'request.session.pop("portal_oidc_invitation", None)' in src
    assert "accept_invitation(invitation, identity.subject, identity.mfa_verified)" in src
    assert "verify_activation" not in src, "the refused posted-assertion path reappeared"


def test_repeat_sign_in_for_an_already_bound_account_is_unaffected(canonical_origin):
    """An accepted account signs in by immutable subject — no invitation involved, before or after."""
    from app.portal.service import sign_in_with_subject
    session: dict = {}
    _invite(_request(session))
    from urllib.parse import parse_qs, urlsplit
    token = parse_qs(urlsplit(_collect(session)["activation"]["url"]).query)["invitation"][0]
    subject = f"microsoft:OID-{uuid.uuid4().hex[:8]}"
    account_id = accept_invitation(token, subject, True)
    assert sign_in_with_subject(subject, True) == account_id      # repeat sign-in, no invitation
    with pytest.raises(ValueError):
        accept_invitation(token, subject, True)                   # and the token is spent


def test_existing_accepted_accounts_are_untouched_by_the_change(canonical_origin):
    """A pre-existing active account keeps its status and subject binding."""
    pid, hid = _person_household()
    account_id, raw = invite_portal_account(
        person_id=pid, household_id=hid, email=f"pre-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Pre Existing", access_type="self", invited_by_user_id=_staff_user())
    subject = f"microsoft:OID-{uuid.uuid4().hex[:8]}"
    accept_invitation(raw, subject, True)
    _invite(_request({}))                                          # unrelated new invitation
    with engine.connect() as c:
        row = c.execute(select(portal_accounts.c.status, portal_accounts.c.auth_subject)
                        .where(portal_accounts.c.id == account_id)).mappings().one()
    assert row["status"] == "active" and row["auth_subject"] == subject


# --- the staff-facing page actually renders it -------------------------------------------

def _render_admin_home(session) -> str:
    """The real template output, not just the context."""
    request = _request(session)
    request.url = SimpleNamespace(path="/admin/client-portal")
    response = portal_admin_home(request, principal=_principal(_staff_user()))
    return response.body.decode("utf-8")


def test_the_page_shows_the_link_once_with_an_explicit_sensitivity_warning(canonical_origin):
    session: dict = {}
    _invite(_request(session), name="Jane Client")
    html = _render_admin_home(session)
    assert f"{CANONICAL}/portal/login?invitation=" in html, "the link was not rendered"
    assert "shown once" in html
    assert "sensitive, single-use invitation credential" in html
    assert "Jane Client" in html
    # ...and it is gone on the very next load.
    later = _render_admin_home(session)
    assert "invitation=" not in later, "the activation link survived to a later page load"
    assert "sensitive, single-use invitation credential" not in later


def test_the_rendered_link_is_not_a_clickable_staff_navigation_target(canonical_origin):
    """It goes in a readonly field to be copied — never an <a href> staff might click into,
    which would consume the client's single-use invitation with the STAFF browser session."""
    session: dict = {}
    _invite(_request(session))
    html = _render_admin_home(session)
    assert 'href="https://portal.example.com/portal/login?invitation=' not in html
    assert "<input readonly value=" in html


def test_a_hostile_display_name_cannot_inject_markup_into_the_panel(canonical_origin):
    session: dict = {}
    _invite(_request(session), name='<img src=x onerror="alert(1)">')
    html = _render_admin_home(session)
    assert "<img src=x" not in html and 'onerror="alert(1)"' not in html
    assert "&lt;img" in html or "&#34;" in html
