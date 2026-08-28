"""Client portal authentication by emailed one-time code.

Clients are not tenant users. Requiring a Microsoft identity to read their own documents was the
wrong bar, so client authentication is now possession of the mailbox the firm invited: a six-digit
code is emailed to the address on the portal account and typed back.

These tests pin the security properties that make that safe — the code is account-bound, single-use,
short-lived, attempt-bounded, never stored or logged or placed in a URL, and always sent to the
address on the account rather than one the browser supplied.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select, update

from app.db import (engine, households, people, portal_accounts, portal_email_verifications,
                    portal_invitations, portal_sessions)
from app.portal import email_auth
from app.portal.service import (INVITATION_TTL_HOURS, create_portal_session, invite_portal_account,
                                resolve_portal_session)
from app.security.models import Principal
from tests._portal_util import seed_staff_user


# --- harness ------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _capture_sent_codes(monkeypatch):
    """Intercept delivery. Every test reads the code from here — it exists nowhere else."""
    sent = []

    def _deliver(account, code, *, purpose):
        sent.append({"email": account["email"], "code": code, "purpose": purpose,
                     "account_id": account["id"]})
        return SimpleNamespace(delivered=True, status="sent", detail="Verification code sent.")

    monkeypatch.setattr(email_auth, "_deliver", _deliver)
    return sent


@pytest.fixture
def sent(_capture_sent_codes):
    return _capture_sent_codes


def _client():
    sfx = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"OTP HH {sfx}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"OTP {sfx}", active=True)
                        .returning(people.c.id)).scalar_one()
    return SimpleNamespace(sfx=sfx, household_id=hid, person_id=pid,
                           email=f"otp-{sfx}@e.test")


def _invite(client, staff=None):
    return invite_portal_account(
        person_id=client.person_id, household_id=client.household_id, email=client.email,
        display_name=f"Client {client.sfx}", access_type="self",
        invited_by_user_id=staff or seed_staff_user())


def _challenges(account_id):
    with engine.connect() as c:
        return c.execute(select(portal_email_verifications).where(
            portal_email_verifications.c.portal_account_id == account_id)
            .order_by(portal_email_verifications.c.id)).mappings().all()


def _account(account_id):
    with engine.connect() as c:
        return c.execute(select(portal_accounts).where(
            portal_accounts.c.id == account_id)).mappings().one()


def _age_challenge(account_id, minutes):
    """Push the newest challenge's expiry into the past."""
    with engine.begin() as c:
        newest = c.execute(select(portal_email_verifications.c.id).where(
            portal_email_verifications.c.portal_account_id == account_id)
            .order_by(portal_email_verifications.c.id.desc()).limit(1)).scalars().one()
        c.execute(update(portal_email_verifications)
                  .where(portal_email_verifications.c.id == newest)
                  .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=minutes)))


def _clear_cooldown(account_id):
    """Backdate sends so the resend cooldown does not dominate a test about something else."""
    with engine.begin() as c:
        c.execute(update(portal_email_verifications)
                  .where(portal_email_verifications.c.portal_account_id == account_id)
                  .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=1)))


def _activate(client, sent):
    """Invitation -> code -> verified session token."""
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    return account_id, email_auth.verify(account_id, sent[-1]["code"])


# --- invitation expiry --------------------------------------------------------------------

def test_a_new_invitation_expires_in_seven_days():
    assert INVITATION_TTL_HOURS == 168
    client = _client()
    account_id, _ = _invite(client)
    with engine.connect() as c:
        expires = c.execute(select(portal_invitations.c.expires_at).where(
            portal_invitations.c.portal_account_id == account_id)).scalars().one()
    remaining = expires - datetime.now(timezone.utc)
    assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7)


def test_the_invitation_email_states_the_seven_day_window():
    from app.services import email_delivery

    assert email_delivery.INVITATION_EXPIRY_DAYS * 24 == INVITATION_TTL_HOURS, \
        "the stated expiry and the real expiry have drifted apart"
    html, text = email_delivery.invitation_bodies("Ada", "https://app.test/portal/activate?x=1")
    assert "expires in 7 days" in text and "expires in 7 days" in html


def test_an_existing_invitation_keeps_its_original_expiry():
    """Historical rows are not rewritten by the new default."""
    client = _client()
    account_id, _ = invite_portal_account(
        person_id=client.person_id, household_id=client.household_id, email=client.email,
        display_name="Legacy", access_type="self", invited_by_user_id=seed_staff_user(),
        expires_hours=72)
    with engine.connect() as c:
        expires = c.execute(select(portal_invitations.c.expires_at).where(
            portal_invitations.c.portal_account_id == account_id)).scalars().one()
    assert expires - datetime.now(timezone.utc) < timedelta(days=4)


# --- activation ---------------------------------------------------------------------------

def test_activation_emails_a_code_to_the_invited_address(sent):
    client = _client()
    account_id, token = _invite(client)

    returned_id, delivery = email_auth.start_activation(token)

    assert returned_id == account_id and delivery.delivered
    assert sent[-1]["email"] == client.email
    assert sent[-1]["purpose"] == email_auth.PURPOSE_ACTIVATION
    assert re.fullmatch(r"\d{6}", sent[-1]["code"])


def test_the_correct_code_activates_the_account_and_creates_a_session(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)

    session_token = email_auth.verify(account_id, sent[-1]["code"])

    account = _account(account_id)
    assert account["status"] == "active"
    assert account["auth_method"] == "email_code"
    assert account["auth_subject"] is None, "an email address was stuffed into the subject column"
    assert account["mfa_enabled"] is True
    principal = resolve_portal_session(session_token)
    assert principal is not None and principal.account_id == account_id


def test_activation_does_not_consume_the_invitation_until_the_code_is_verified(sent):
    """A mail scanner that fetches the link must not burn the client's activation."""
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)

    with engine.connect() as c:
        assert c.execute(select(portal_invitations.c.accepted_at).where(
            portal_invitations.c.portal_account_id == account_id)).scalars().one() is None

    email_auth.verify(account_id, sent[-1]["code"])
    with engine.connect() as c:
        assert c.execute(select(portal_invitations.c.accepted_at).where(
            portal_invitations.c.portal_account_id == account_id)).scalars().one() is not None


def test_activation_no_longer_requires_a_microsoft_subject(sent):
    client = _client()
    account_id, _ = _activate(client, sent)
    assert _account(account_id)["auth_subject"] is None


@pytest.mark.parametrize("bad", ["000000", "999999", "12345", "1234567", "", "abcdef", None])
def test_an_incorrect_code_is_rejected(sent, bad):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    real = sent[-1]["code"]
    if bad == real:
        pytest.skip("collided with the real code")

    with pytest.raises(email_auth.EmailAuthError):
        email_auth.verify(account_id, bad)
    assert _account(account_id)["status"] == "invited", "a wrong code activated the account"


def test_an_expired_code_is_rejected(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    _age_challenge(account_id, minutes=1)

    with pytest.raises(email_auth.EmailAuthError):
        email_auth.verify(account_id, sent[-1]["code"])


def test_a_used_code_cannot_be_replayed(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    code = sent[-1]["code"]
    email_auth.verify(account_id, code)

    with pytest.raises(email_auth.EmailAuthError):
        email_auth.verify(account_id, code)
    assert _challenges(account_id)[-1]["consumed_at"] is not None


def test_attempts_are_bounded_and_the_code_dies_when_they_run_out(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    real = sent[-1]["code"]
    wrong = "111111" if real != "111111" else "222222"

    for _ in range(email_auth.MAX_ATTEMPTS):
        with pytest.raises(email_auth.EmailAuthError):
            email_auth.verify(account_id, wrong)

    challenge = _challenges(account_id)[-1]
    assert challenge["attempts"] == email_auth.MAX_ATTEMPTS
    assert challenge["invalidated_at"] is not None
    # Even the RIGHT code no longer works: guessing cannot be continued against this challenge.
    with pytest.raises(email_auth.EmailAuthError):
        email_auth.verify(account_id, real)


# --- resend -------------------------------------------------------------------------------

def test_a_resend_invalidates_the_previous_code(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    first = sent[-1]["code"]
    _clear_cooldown(account_id)

    email_auth.resend(account_id)
    second = sent[-1]["code"]

    assert _challenges(account_id)[0]["invalidated_at"] is not None
    if first != second:
        with pytest.raises(email_auth.EmailAuthError):
            email_auth.verify(account_id, first)
    assert email_auth.verify(account_id, second)


def test_resend_is_rate_limited_within_the_window(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    before = len(sent)

    for _ in range(email_auth.MAX_SENDS_PER_WINDOW + 3):
        _clear_cooldown(account_id)
        email_auth.resend(account_id)

    assert len(sent) - before <= email_auth.MAX_SENDS_PER_WINDOW, "resend is not bounded"


def test_an_immediate_resend_is_refused_by_the_cooldown(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    before = len(sent)

    email_auth.resend(account_id)                 # no cooldown clearing: too soon

    assert len(sent) == before, "the cooldown did not apply"


# --- code storage & exposure --------------------------------------------------------------

def test_the_raw_code_is_never_persisted(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    code = sent[-1]["code"]

    challenge = _challenges(account_id)[-1]
    assert code not in str(dict(challenge))
    assert challenge["code_hash"] != code
    assert len(challenge["code_hash"]) == 64
    # Not a bare digest either: an unkeyed sha256 of six digits is a lookup table away from useless.
    import hashlib
    assert challenge["code_hash"] != hashlib.sha256(code.encode()).hexdigest()


def test_the_code_hash_is_bound_to_the_account():
    """The same code for two accounts must not produce the same stored value."""
    assert email_auth._code_hash(1, "123456") != email_auth._code_hash(2, "123456")


def test_a_code_issued_for_one_account_cannot_verify_another(sent, monkeypatch):
    """Both accounts are handed the SAME code value, so only the account binding can separate them.

    Deliberately not left to chance: with random codes this passes even if lookup were by hash, so
    the code is fixed and account 2 is given no challenge of its own. A hash-keyed lookup would find
    account 1's live row and activate the wrong account."""
    monkeypatch.setattr(email_auth, "generate_code", lambda: "424242")
    first, second = _client(), _client()
    first_id, first_token = _invite(first)
    second_id, _ = _invite(second)
    email_auth.start_activation(first_token)
    assert sent[-1]["code"] == "424242"

    with pytest.raises(email_auth.EmailAuthError):
        email_auth.verify(second_id, "424242")
    assert _account(second_id)["status"] == "invited", "another account's code activated this one"

    # The same value still works for the account it was issued to — the refusal was about identity.
    assert email_auth.verify(first_id, "424242")
    assert _account(first_id)["status"] == "active"


def test_the_raw_code_never_reaches_the_logs(sent):
    """Every client360 log record produced during a full activation is inspected."""
    records = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    root, app_log = logging.getLogger(), logging.getLogger("client360")
    handler = _Collector()
    for lg in (root, app_log):
        lg.addHandler(handler)
    try:
        client = _client()
        account_id, token = _invite(client)
        email_auth.start_activation(token)
        code = sent[-1]["code"]
        email_auth.verify(account_id, code)
    finally:
        for lg in (root, app_log):
            lg.removeHandler(handler)

    blob = "\n".join(f"{r.getMessage()} {r.args!r}" for r in records)
    assert code not in blob, "a verification code was written to the log"


def test_the_code_is_never_placed_in_a_url():
    """No route builds a URL carrying the code, and the form posts it in a body."""
    import inspect

    from app.routes import portal as portal_routes
    src = inspect.getsource(portal_routes)
    for pattern in ("?code=", "code={", "&code=", "verification_code="):
        assert pattern not in src, f"a route builds a URL containing {pattern}"
    verify_tpl = open("app/templates/portal/verify.html", encoding="utf-8").read()
    assert 'method="post"' in verify_tpl
    assert 'method="get"' not in verify_tpl.lower()


def test_the_destination_address_comes_from_the_account_not_the_request(sent):
    """There is no parameter anywhere that can redirect a code to another mailbox."""
    import inspect

    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    assert sent[-1]["email"] == client.email

    # The destination is read from the account row, and _issue takes no address parameter at all.
    src = inspect.getsource(email_auth._issue)
    assert 'account["normalized_email"]' in src
    assert "email" not in inspect.signature(email_auth._issue).parameters
    assert "email" not in inspect.signature(email_auth.verify).parameters
    assert "email" not in inspect.signature(email_auth.resend).parameters
    challenge = _challenges(account_id)[-1]
    assert challenge["sent_to_email"] == client.email.lower()


# --- repeat login -------------------------------------------------------------------------

def test_repeat_login_by_email_and_code(sent):
    client = _client()
    account_id, _ = _activate(client, sent)
    _clear_cooldown(account_id)

    returned_id, delivery = email_auth.start_login(client.email)
    assert returned_id == account_id and delivery.delivered
    assert sent[-1]["purpose"] == email_auth.PURPOSE_LOGIN

    session_token = email_auth.verify(account_id, sent[-1]["code"])
    assert resolve_portal_session(session_token).account_id == account_id


def test_login_is_case_and_whitespace_insensitive_on_the_address(sent):
    client = _client()
    account_id, _ = _activate(client, sent)
    _clear_cooldown(account_id)

    returned_id, _ = email_auth.start_login(f"  {client.email.upper()}  ")
    assert returned_id == account_id


@pytest.mark.parametrize("address", ["nobody-at-all@e.test", "", "   ", "not-an-email"])
def test_an_unknown_address_produces_no_code_and_no_signal(sent, address):
    before = len(sent)
    account_id, delivery = email_auth.start_login(address)
    assert account_id is None and delivery is None
    assert len(sent) == before


def test_an_invited_but_never_activated_account_cannot_use_repeat_login(sent):
    """Sign-in is for accounts that exist; activation is what an invitation is for."""
    client = _client()
    _invite(client)
    before = len(sent)

    account_id, _ = email_auth.start_login(client.email)

    assert account_id is None
    assert len(sent) == before


def test_a_revoked_account_gets_the_same_silence_as_an_unknown_address(sent):
    client = _client()
    account_id, _ = _activate(client, sent)
    _revoke(account_id)
    before = len(sent)

    result = email_auth.start_login(client.email)

    assert result == (None, None)
    assert len(sent) == before


def test_the_generic_message_is_identical_for_every_outcome():
    """One sentence, used for a real send and for nothing at all."""
    assert "If an active portal account exists" in email_auth.GENERIC_SENT_MESSAGE
    for forbidden in ("not found", "unknown", "revoked", "inactive", "no account", "does not"):
        assert forbidden not in email_auth.GENERIC_SENT_MESSAGE.lower()


# --- revocation ---------------------------------------------------------------------------

def _revoke(account_id):
    from app.portal.service import revoke_account_access
    with engine.begin() as c:
        c.execute(portal_accounts.update().where(portal_accounts.c.id == account_id)
                  .values(status="revoked"))
        return revoke_account_access(c, account_id)


def test_revocation_invalidates_an_outstanding_code(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)
    code = sent[-1]["code"]

    closed = _revoke(account_id)

    assert closed["codes_invalidated"] == 1
    assert _challenges(account_id)[-1]["invalidated_at"] is not None
    with pytest.raises(email_auth.EmailAuthError):
        email_auth.verify(account_id, code)


def test_revocation_invalidates_the_active_session(sent):
    client = _client()
    account_id, session_token = _activate(client, sent)
    assert resolve_portal_session(session_token) is not None

    _revoke(account_id)

    assert resolve_portal_session(session_token) is None
    with engine.connect() as c:
        assert all(row["revoked_at"] is not None for row in c.execute(select(portal_sessions).where(
            portal_sessions.c.portal_account_id == account_id)).mappings())


def test_a_revoked_account_cannot_request_a_code(sent):
    client = _client()
    account_id, token = _invite(client)
    _revoke(account_id)
    before = len(sent)

    with pytest.raises(email_auth.EmailAuthError):
        email_auth.start_activation(token)
    assert email_auth.resend(account_id) is None
    assert len(sent) == before


# --- isolation ----------------------------------------------------------------------------

def test_two_portal_accounts_stay_isolated(sent):
    first, second = _client(), _client()
    first_id, first_session = _activate(first, sent)
    second_id, second_session = _activate(second, sent)

    assert first_id != second_id
    assert resolve_portal_session(first_session).account_id == first_id
    assert resolve_portal_session(second_session).account_id == second_id
    _revoke(first_id)
    assert resolve_portal_session(second_session) is not None, "revoking one closed the other"


def test_an_invitation_cannot_activate_a_different_account(sent):
    first, second = _client(), _client()
    first_id, first_token = _invite(first)
    second_id, _ = _invite(second)

    email_auth.start_activation(first_token)
    code = sent[-1]["code"]

    with pytest.raises(email_auth.EmailAuthError):
        email_auth.verify(second_id, code)
    assert _account(second_id)["status"] == "invited"


def test_a_session_exists_only_after_a_verified_code(sent):
    client = _client()
    account_id, token = _invite(client)
    email_auth.start_activation(token)

    with engine.connect() as c:
        assert c.execute(select(portal_sessions).where(
            portal_sessions.c.portal_account_id == account_id)).mappings().all() == []

    email_auth.verify(account_id, sent[-1]["code"])
    with engine.connect() as c:
        assert len(c.execute(select(portal_sessions).where(
            portal_sessions.c.portal_account_id == account_id)).mappings().all()) == 1


# --- browser routes -----------------------------------------------------------------------

def _req(session=None, path="/portal/login"):
    return SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}", principal=None,
                              portal_principal=None, demo_mode=False),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"user-agent": "pytest", "accept": "text/html"},
        query_params={}, session={} if session is None else session,
        url=SimpleNamespace(path=path),
        url_for=lambda name: "https://app.test/portal/auth/callback")


def test_the_activation_route_moves_the_token_out_of_the_url(sent):
    from app.routes.portal import portal_activate

    client = _client()
    _, token = _invite(client)
    session = {}

    first = portal_activate(_req(session), invitation=token)
    assert first.status_code == 303
    assert first.headers["location"] == "/portal/activate", "the token stayed in the address bar"
    assert token not in first.headers["location"]

    second = portal_activate(_req(session))
    assert second.headers["location"] == "/portal/verify?sent=1"
    assert sent[-1]["email"] == client.email


def test_the_full_browser_activation_reaches_the_portal(sent):
    from app.routes.portal import portal_activate, portal_verify_submit

    client = _client()
    account_id, token = _invite(client)
    session = {}
    portal_activate(_req(session), invitation=token)
    portal_activate(_req(session))

    response = portal_verify_submit(_req(session), code=sent[-1]["code"])

    assert response.status_code == 303 and response.headers["location"] == "/portal"
    assert resolve_portal_session(session["portal_session_token"]).account_id == account_id
    assert "portal_auth_account_id" not in session, "the challenge outlived the sign-in"


def test_a_wrong_code_in_the_browser_re_renders_without_a_session(sent):
    from app.routes.portal import portal_activate, portal_verify_submit

    client = _client()
    _, token = _invite(client)
    session = {}
    portal_activate(_req(session), invitation=token)
    portal_activate(_req(session))
    real = sent[-1]["code"]

    response = portal_verify_submit(_req(session), code="000000" if real != "000000" else "111111")

    assert response.status_code == 400
    assert "portal_session_token" not in session
    body = response.body.decode()
    assert email_auth.GENERIC_VERIFY_ERROR in body
    assert real not in body, "the page echoed the real code"


def test_the_login_form_response_is_identical_for_known_and_unknown_addresses(sent):
    from app.routes.portal import portal_login_submit

    client = _client()
    _activate(client, sent)
    _clear_cooldown(_account_id_for(client.email))

    known = portal_login_submit(_req({}), email=client.email)
    unknown = portal_login_submit(_req({}), email=f"nobody-{uuid.uuid4().hex[:8]}@e.test")

    assert known.status_code == unknown.status_code == 303
    assert known.headers["location"] == unknown.headers["location"] == "/portal/verify?sent=1"


def _account_id_for(email):
    with engine.connect() as c:
        return c.execute(select(portal_accounts.c.id).where(
            portal_accounts.c.normalized_email == email.lower())).scalars().one()


def test_a_verify_page_reached_without_a_challenge_goes_back_to_login():
    from app.routes.portal import portal_verify

    assert portal_verify(_req({})).headers["location"] == "/portal/login"


def test_the_masked_address_never_shows_the_full_local_part(sent):
    from app.routes.portal import _mask_email

    masked = _mask_email("firstname.lastname@example.com")
    assert masked.endswith("@example.com")
    assert "firstname.lastname" not in masked and "•" in masked


def test_no_client_authentication_template_offers_microsoft():
    for name in ("login.html", "verify.html"):
        body = open(f"app/templates/portal/{name}", encoding="utf-8").read()
        for affordance in ("Sign in with Microsoft", "/portal/auth/start", "Microsoft", "Entra",
                           "OIDC", "MSAL", "Azure", "Graph"):
            assert affordance not in body, f"portal/{name} offers {affordance!r}"


def test_the_client_auth_routes_are_reachable_before_a_session_exists():
    from app.main import app
    from app.security.middleware import PUBLIC_EXACT
    from app.services.features import portal_gate

    paths = {getattr(r, "path", "") for r in app.routes}
    for path in ("/portal/login", "/portal/activate", "/portal/verify", "/portal/verify/resend"):
        assert path in paths
        assert path in PUBLIC_EXACT, f"{path} is not reachable pre-session"
        assert portal_gate.is_exempt(path), f"{path} is behind the portal data gate"


# --- staff/admin Microsoft authentication is untouched ------------------------------------

def test_staff_microsoft_authentication_is_unchanged():
    """The staff fork keeps its own Microsoft sign-in; only the CLIENT model changed."""
    import inspect

    from app.main import app
    from app.routes import auth as staff_auth

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/auth/login" in paths and "/auth/callback" in paths
    src = inspect.getsource(staff_auth)
    # Staff sign-in still runs the external OIDC authorization-code flow through its own provider
    # and its own session service — none of which the client path touches.
    assert "OidcIdentityProvider" in src
    assert "authenticate_claims" in src and "create_session" in src
    # ...and the client OTP module cannot reach into the staff identity path at all.
    client_src = inspect.getsource(email_auth)
    for staff_only in ("app.routes.auth", "OidcIdentityProvider", "authenticate_claims",
                       "microsoft_identity", "app.security.service"):
        assert staff_only not in client_src, f"the client path references {staff_only}"


def test_the_client_flow_never_touches_the_staff_mailbox_or_a_delegated_token():
    import inspect

    src = inspect.getsource(email_auth)
    for forbidden in ("acquire_token_silent", "microsoft_accounts", "MICROSOFT_CLIENT_ID",
                      "MICROSOFT_CLIENT_SECRET", "/me/sendMail"):
        assert forbidden not in src, f"the client code path references {forbidden}"
    from app.services import email_delivery
    send_src = inspect.getsource(email_delivery.send_portal_verification_code)
    assert "_acquire_app_token()" in send_src, "the code email is not sent app-only"
