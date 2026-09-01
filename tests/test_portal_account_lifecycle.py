"""Portal account invite / revoke / re-invite lifecycle.

Production evidence: ``portal_accounts.normalized_email`` is UNIQUE and revoking deliberately keeps
the account row (it is the audit trail), so re-inviting a revoked client hit that constraint and
staff were shown ``IntegrityError`` as if it were an explanation. Revoking also left the outstanding
invitation live (``revoked_at`` NULL), so a revoked client could still activate from the email
already sitting in their inbox.

These tests pin the whole cycle, including that it can be repeated.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select

from app.db import (
    engine,
    households,
    people,
    portal_access_grants,
    portal_accounts,
    portal_invitations,
    portal_sessions,
)
from app.portal.service import (
    PortalAccountConflictError,
    accept_invitation,
    create_portal_session,
    invite_portal_account,
    resolve_portal_session,
    sign_in_with_subject,
)
from app.security.models import Principal
from tests._portal_util import seed_staff_user


def _client():
    """A fresh household + person with a unique email, so tests never collide in the shared DB."""
    sfx = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"LC HH {sfx}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"LC Client {sfx}",
                                              first_name="Life", last_name=f"Cycle{sfx}",
                                              active=True).returning(people.c.id)).scalar_one()
    return SimpleNamespace(sfx=sfx, household_id=hid, person_id=pid,
                           email=f"lifecycle-{sfx}@e.test")


def _invite(client, staff_id, **kw):
    return invite_portal_account(
        person_id=client.person_id, household_id=client.household_id, email=client.email,
        display_name=f"Client {client.sfx}", access_type="self", invited_by_user_id=staff_id,
        **kw)


def _account(account_id):
    with engine.connect() as c:
        return c.execute(select(portal_accounts).where(
            portal_accounts.c.id == account_id)).mappings().one()


def _invitations(account_id):
    with engine.connect() as c:
        return c.execute(select(portal_invitations).where(
            portal_invitations.c.portal_account_id == account_id)
            .order_by(portal_invitations.c.id)).mappings().all()


def _grants(account_id):
    with engine.connect() as c:
        return c.execute(select(portal_access_grants).where(
            portal_access_grants.c.portal_account_id == account_id)
            .order_by(portal_access_grants.c.id)).mappings().all()


def _active_grants(account_id):
    return [g for g in _grants(account_id) if g["inactive_date"] is None]


def _sessions(account_id):
    with engine.connect() as c:
        return c.execute(select(portal_sessions).where(
            portal_sessions.c.portal_account_id == account_id)).mappings().all()


def _revoke(account_id, staff_id, *, html=False):
    """Drive the real staff route, including its capability/scope dependencies' effects."""
    from app.routes.portal_admin import portal_admin_revoke
    request = SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}"),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"user-agent": "pytest", "accept": "text/html"} if html else {"user-agent": "pytest"},
        url=SimpleNamespace(path="/admin/client-portal/accounts/1/revoke"))
    principal = Principal(staff_id, "staff@example.com", "Staff",
                          frozenset({"client.read", "client.write",
                                     "record.read_all", "record.write_all"}))
    return portal_admin_revoke(account_id, request, principal=principal)


# --- first invitation --------------------------------------------------------------------

def test_a_first_invitation_creates_the_account_grant_and_invitation():
    client, staff = _client(), seed_staff_user()
    account_id, token = _invite(client, staff)

    account = _account(account_id)
    assert account["status"] == "invited"
    assert account["normalized_email"] == client.email.lower()
    assert account["auth_subject"] is None
    assert len(_invitations(account_id)) == 1
    assert len(_active_grants(account_id)) == 1
    assert token


# --- revoke before acceptance ------------------------------------------------------------

def test_revoke_before_acceptance_closes_invitation_grant_and_status():
    client, staff = _client(), seed_staff_user()
    account_id, _ = _invite(client, staff)

    _revoke(account_id, staff)

    assert _account(account_id)["status"] == "revoked"
    invitation = _invitations(account_id)[0]
    assert invitation["revoked_at"] is not None, "the outstanding invitation is still live"
    assert invitation["accepted_at"] is None
    assert _active_grants(account_id) == []


def test_a_revoked_invitation_can_no_longer_be_accepted():
    """The concrete risk: the client still has the activation email in their inbox."""
    client, staff = _client(), seed_staff_user()
    account_id, token = _invite(client, staff)
    _revoke(account_id, staff)

    with pytest.raises(ValueError):
        accept_invitation(token, f"microsoft:{client.sfx}", True)


def test_revoke_revokes_live_sessions():
    client, staff = _client(), seed_staff_user()
    account_id, token = _invite(client, staff)
    accept_invitation(token, f"microsoft:{client.sfx}", True)
    session_token = create_portal_session(account_id, device_fingerprint=f"dev-{client.sfx}")
    assert resolve_portal_session(session_token) is not None

    _revoke(account_id, staff)

    assert all(s["revoked_at"] is not None for s in _sessions(account_id))
    assert resolve_portal_session(session_token) is None, "an open browser still has portal access"


def test_revoke_leaves_no_stale_identity_access():
    """Status alone must block the bound subject; the row and its history are kept."""
    client, staff = _client(), seed_staff_user()
    account_id, token = _invite(client, staff)
    subject = f"microsoft:{client.sfx}"
    accept_invitation(token, subject, True)

    _revoke(account_id, staff)

    with pytest.raises(ValueError):
        sign_in_with_subject(subject, True)
    assert _account(account_id) is not None, "the account row was deleted"


def test_accepted_invitations_are_preserved_as_history():
    client, staff = _client(), seed_staff_user()
    account_id, token = _invite(client, staff)
    accept_invitation(token, f"microsoft:{client.sfx}", True)

    _revoke(account_id, staff)

    accepted = _invitations(account_id)[0]
    assert accepted["accepted_at"] is not None
    assert accepted["revoked_at"] is None, "an accepted invitation was rewritten as revoked"


# --- re-invite ---------------------------------------------------------------------------

def test_re_inviting_a_revoked_account_reuses_it_instead_of_raising():
    """The production failure: a second INSERT for the same normalized email."""
    client, staff = _client(), seed_staff_user()
    first_id, _ = _invite(client, staff)
    _revoke(first_id, staff)

    second_id, second_token = _invite(client, staff)

    assert second_id == first_id, "a re-invitation created a second portal account"
    assert second_token
    assert _account(second_id)["status"] == "invited"


def test_a_re_invitation_issues_a_fresh_token_and_keeps_the_old_one_dead():
    client, staff = _client(), seed_staff_user()
    account_id, first_token = _invite(client, staff)
    _revoke(account_id, staff)
    _, second_token = _invite(client, staff)

    assert second_token != first_token
    invitations = _invitations(account_id)
    assert len(invitations) == 2, "the historical invitation was deleted"
    assert invitations[0]["revoked_at"] is not None
    assert invitations[1]["revoked_at"] is None and invitations[1]["accepted_at"] is None
    assert invitations[1]["token_hash"] != invitations[0]["token_hash"]
    assert invitations[1]["expires_at"] > datetime.now(UTC)

    with pytest.raises(ValueError):
        accept_invitation(first_token, f"microsoft:{client.sfx}", True)


def test_a_re_invitation_restores_exactly_one_active_grant():
    client, staff = _client(), seed_staff_user()
    account_id, _ = _invite(client, staff)
    _revoke(account_id, staff)

    _invite(client, staff)

    active = _active_grants(account_id)
    assert len(active) == 1, f"expected one active grant, found {len(active)}"
    assert active[0]["access_type"] == "self"
    assert active[0]["household_id"] == client.household_id


def test_a_same_day_revoke_and_re_invite_does_not_violate_the_grant_unique_constraint():
    """uq_portal_access_grant includes effective_date, which defaults to today."""
    client, staff = _client(), seed_staff_user()
    account_id, _ = _invite(client, staff)
    _revoke(account_id, staff)

    _invite(client, staff)          # would raise IntegrityError on a blind re-INSERT

    rows = _grants(account_id)
    same_day = [g for g in rows if g["effective_date"] == date.today()]
    assert len(same_day) == 1, "a duplicate same-day grant row was created"
    assert same_day[0]["inactive_date"] is None


def test_a_re_invitation_clears_the_previous_identity_binding():
    client, staff = _client(), seed_staff_user()
    account_id, token = _invite(client, staff)
    old_subject = f"microsoft:{client.sfx}"
    accept_invitation(token, old_subject, True)
    _revoke(account_id, staff)

    _invite(client, staff)

    assert _account(account_id)["auth_subject"] is None
    assert _account(account_id)["mfa_enabled"] is False
    # The old credential must not get back in without accepting the NEW invitation.
    with pytest.raises(ValueError):
        sign_in_with_subject(old_subject, True)


def test_acceptance_works_after_a_re_invitation():
    client, staff = _client(), seed_staff_user()
    account_id, _ = _invite(client, staff)
    _revoke(account_id, staff)
    _, fresh_token = _invite(client, staff)

    new_subject = f"microsoft:{client.sfx}-second"
    assert accept_invitation(fresh_token, new_subject, True) == account_id

    account = _account(account_id)
    assert account["status"] == "active" and account["auth_subject"] == new_subject
    assert sign_in_with_subject(new_subject, True) == account_id


def test_repeated_revoke_and_re_invite_cycles_work():
    client, staff = _client(), seed_staff_user()
    account_id, token = _invite(client, staff)

    for cycle in range(3):
        subject = f"microsoft:{client.sfx}-cycle{cycle}"
        assert accept_invitation(token, subject, True) == account_id
        session_token = create_portal_session(account_id,
                                              device_fingerprint=f"dev-{client.sfx}-{cycle}")
        _revoke(account_id, staff)
        assert resolve_portal_session(session_token) is None
        assert _active_grants(account_id) == []
        new_id, token = _invite(client, staff)
        assert new_id == account_id

    assert len(_invitations(account_id)) == 4
    assert len(_active_grants(account_id)) == 1
    with engine.connect() as c:
        assert c.execute(select(portal_accounts).where(
            portal_accounts.c.normalized_email == client.email.lower())).mappings().all().__len__() == 1


# --- a live account is never silently reused ---------------------------------------------

@pytest.mark.parametrize("accept_first", [False, True])
def test_inviting_an_account_that_is_still_live_is_refused_safely(accept_first):
    client, staff = _client(), seed_staff_user()
    account_id, token = _invite(client, staff)
    if accept_first:
        accept_invitation(token, f"microsoft:{client.sfx}", True)
    before = _account(account_id)

    with pytest.raises(PortalAccountConflictError) as exc:
        _invite(client, staff)

    message = str(exc.value)
    assert client.email in message and "Revoke it" in message
    for leak in ("IntegrityError", "psycopg", "UNIQUE", "constraint", "SELECT", "INSERT",
                 "portal_accounts", "Traceback"):
        assert leak not in message, f"the staff message leaks {leak}"
    # Nothing was created or overwritten.
    after = _account(account_id)
    assert after["auth_subject"] == before["auth_subject"]
    assert after["status"] == before["status"]
    assert len(_invitations(account_id)) == 1
    assert len(_active_grants(account_id)) == 1


def test_the_same_email_cannot_be_repointed_at_a_different_client():
    other, client, staff = _client(), _client(), seed_staff_user()
    account_id, _ = _invite(client, staff)
    _revoke(account_id, staff)

    with pytest.raises(PortalAccountConflictError):
        invite_portal_account(
            person_id=other.person_id, household_id=other.household_id, email=client.email,
            display_name="Someone Else", access_type="self", invited_by_user_id=staff)

    assert _account(account_id)["person_id"] == client.person_id


def test_normalized_email_uniqueness_is_still_enforced_by_the_database():
    """The fix must not have relaxed the constraint that surfaced the bug."""
    from sqlalchemy.exc import IntegrityError

    client, staff = _client(), seed_staff_user()
    account_id, _ = _invite(client, staff)
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(insert(portal_accounts).values(
                person_id=client.person_id, email=client.email.upper(),
                normalized_email=client.email.lower(), display_name="Dupe", status="invited"))
    assert _account(account_id)["status"] == "invited"


def test_no_portal_account_is_ever_deleted_by_the_lifecycle():
    import inspect

    from app.portal import service
    from app.routes import portal_admin
    for src in (inspect.getsource(service.invite_portal_account),
                inspect.getsource(service.revoke_account_access),
                inspect.getsource(portal_admin.portal_admin_revoke)):
        code = "\n".join(line.split("#")[0] for line in src.splitlines())
        assert "portal_accounts.delete" not in code and ".delete()" not in code


def test_the_lifecycle_is_transactional():
    """A failure part-way through a re-invite must leave nothing half-applied."""
    import inspect

    from app.portal import service
    src = inspect.getsource(service.invite_portal_account)
    assert src.count("engine.begin()") == 1, "the re-invite spans more than one transaction"
    assert "with_for_update()" in src, "the account row is not locked while it is re-invited"


# --- browser surfaces --------------------------------------------------------------------

def _form_request():
    return SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}", principal=None,
                              demo_mode=False),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"user-agent": "pytest", "accept": "text/html"},
        query_params={}, session={},
        url=SimpleNamespace(path="/admin/client-portal"),
        url_for=lambda name: "http://host.invalid/portal/login")


def _staff_principal(uid):
    return Principal(uid, "staff@example.com", "Staff",
                     frozenset({"client.read", "client.write",
                                "record.read_all", "record.write_all"}))


def test_the_invite_form_never_shows_a_database_exception_to_staff():
    """The production banner literally read "IntegrityError"."""
    from urllib.parse import unquote

    from app.routes.portal_admin import portal_admin_invite_form

    client, staff = _client(), seed_staff_user()
    _invite(client, staff)                      # first account, still live

    response = portal_admin_invite_form(
        request=_form_request(), person_id=str(client.person_id), email=client.email,
        access_type="self", principal=_staff_principal(staff))

    assert response.status_code == 303
    banner = unquote(response.headers["location"].split("error=", 1)[1])
    assert "Revoke it" in banner, banner
    for leak in ("IntegrityError", "psycopg", "sqlalchemy", "UNIQUE", "constraint", "SELECT",
                 "INSERT", "DETAIL:", "Traceback", "portal_accounts"):
        assert leak not in banner, f"the staff banner leaks {leak}"


def test_an_unexpected_invite_failure_shows_a_fixed_sentence_not_a_class_name(monkeypatch):
    from urllib.parse import unquote

    from sqlalchemy.exc import IntegrityError

    from app.routes.portal_admin import INVITE_FAILED_ERROR, portal_admin_invite_form

    client, staff = _client(), seed_staff_user()

    def _boom(**kw):
        raise IntegrityError("INSERT INTO portal_accounts ...", {}, Exception("duplicate key"))

    monkeypatch.setattr("app.routes.portal_admin.invite_portal_account", _boom)
    response = portal_admin_invite_form(
        request=_form_request(), person_id=str(client.person_id), email=client.email,
        access_type="self", principal=_staff_principal(staff))

    banner = unquote(response.headers["location"].split("error=", 1)[1])
    assert banner == INVITE_FAILED_ERROR
    for leak in ("IntegrityError", "duplicate key", "INSERT", "portal_accounts"):
        assert leak not in banner


def test_browser_revoke_redirects_back_to_the_admin_page_with_a_banner():
    """Clicking Revoke used to render the raw JSON body in the address bar."""
    from urllib.parse import unquote

    client, staff = _client(), seed_staff_user()
    account_id, _ = _invite(client, staff)

    response = _revoke(account_id, staff, html=True)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/admin/client-portal?revoked=")
    assert client.sfx in unquote(location)
    assert "{" not in location and "account_id" not in location


def test_an_api_caller_still_receives_the_json_body():
    client, staff = _client(), seed_staff_user()
    account_id, _ = _invite(client, staff)

    body = _revoke(account_id, staff, html=False)

    assert body["account_id"] == account_id and body["status"] == "revoked"
    assert body["invitations_revoked"] == 1 and body["grants_inactivated"] == 1


def test_the_revoked_banner_renders_on_the_admin_page():
    from app.routes.portal_admin import portal_admin_home

    client, staff = _client(), seed_staff_user()
    _invite(client, staff)
    request = _form_request()
    request.query_params = {"revoked": f"Client {client.sfx}"}
    html = portal_admin_home(request, principal=_staff_principal(staff)).body.decode()

    assert "Portal access revoked for" in html and f"Client {client.sfx}" in html


def test_the_error_banner_is_no_longer_prefixed_with_could_not_invite():
    """Revoke reports through the same banner, so an invite-only prefix would be wrong."""
    from app.routes.portal_admin import portal_admin_home

    staff = seed_staff_user()
    request = _form_request()
    request.query_params = {"error": "Portal account not found"}
    html = portal_admin_home(request, principal=_staff_principal(staff)).body.decode()

    assert "Portal account not found" in html
    assert "Could not invite:" not in html
