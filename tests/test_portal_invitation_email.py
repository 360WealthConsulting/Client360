"""The portal invitation is actually emailed to the client — Microsoft Graph, application identity.

Until now an invitation existed only as a link handed to the staff member once. Email delivery is
the normal path; the one-time handoff stays as the fallback for when delivery is off, unconfigured
or refused.

The security model of the raw token is unchanged and is the point of most of these tests: it exists
for one frame — long enough to build the activation URL, send it, and stash it once server-side —
and is never persisted, logged, audited, redirected, or returned as JSON. The database still stores
only its SHA-256.

No test sends real email. ``send_portal_invitation`` takes an injectable transport, and the route
threads one through, so the Graph HTTP call is never made here.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app.db import audit_events, engine, households, people, portal_invitations, users
from app.portal import invitation_handoff
from app.portal.service import _hash
from app.routes.portal_admin import HANDOFF_SESSION_KEY, portal_admin_invite_form
from app.security.models import Principal
from app.services import email_delivery as ED

CANONICAL = "https://portal.example.com"


class _Response:
    def __init__(self, status_code=202):
        self.status_code = status_code
        self.text = "graph body: SHOULD-NEVER-BE-SHOWN"


def _recorder(status_code=202):
    """A fake Graph transport that records the call instead of making it."""
    calls = []

    def transport(token, sender, payload):
        calls.append({"token": token, "sender": sender, "payload": payload})
        return _Response(status_code)

    return transport, calls


@pytest.fixture
def mail_configured(monkeypatch):
    monkeypatch.setenv("PORTAL_EMAIL_ENABLED", "true")
    monkeypatch.setenv("PORTAL_EMAIL_SENDER", "clientservices@example.com")
    monkeypatch.setenv("PORTAL_EMAIL_TENANT_ID", "mail-tenant-under-test")
    monkeypatch.setenv("PORTAL_EMAIL_CLIENT_ID", "mail-client-under-test")
    monkeypatch.setenv("PORTAL_EMAIL_CLIENT_SECRET", "not-a-real-mail-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", CANONICAL)
    # Never reach MSAL/Entra in tests.
    monkeypatch.setattr(ED, "_acquire_app_token", lambda: "test-app-token")
    yield


def _staff(caps=("client.read", "client.write", "record.read_all", "record.write_all")):
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"staff-{sfx}@example.com", normalized_email=f"staff-{sfx}@example.com",
            display_name="Mail Staff", auth_subject=f"staff-{sfx}", status="active")
            .returning(users.c.id)).scalar_one()
    return Principal(uid, f"staff-{sfx}@example.com", "Mail Staff", frozenset(caps))


def _client(sfx, *, first="Michael", last=None, email=None):
    last = last or f"Mailed{sfx}"
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"HH {sfx}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last}",
            primary_email=email or f"client-{sfx}@example.com",
            normalized_email=email or f"client-{sfx}@example.com",
            active=True, household_id=hid).returning(people.c.id)).scalar_one()
    return pid


def _req(session=None):
    return SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}"),
        client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"},
        session={} if session is None else session,
        url_for=lambda name: "http://inbound-host.invalid/portal/login")


def _invite(*, transport, principal=None, email=None, session=None, sfx=None):
    sfx = sfx or uuid.uuid4().hex[:8]
    principal = principal or _staff()
    pid = _client(sfx)
    session = {} if session is None else session
    response = portal_admin_invite_form(
        request=_req(session), person_id=str(pid), email=email or f"to-{sfx}@example.com",
        access_type="self", principal=principal, mail_transport=transport)
    return SimpleNamespace(response=response, session=session, person_id=pid, sfx=sfx)


def _handoff(session):
    return invitation_handoff.take(session.get(HANDOFF_SESSION_KEY))


# --- delivery happens, to the right person, with the right link -------------------------------

def test_sending_an_invitation_invokes_the_mail_service(mail_configured):
    transport, calls = _recorder()
    _invite(transport=transport)
    assert len(calls) == 1, "the mail service was not invoked"
    assert calls[0]["sender"] == "clientservices@example.com", "not sent from the firm mailbox"


def test_the_recipient_is_the_email_staff_entered(mail_configured):
    transport, calls = _recorder()
    sfx = uuid.uuid4().hex[:8]
    _invite(transport=transport, email=f"typed-{sfx}@example.com", sfx=sfx)
    recipients = calls[0]["payload"]["message"]["toRecipients"]
    assert [r["emailAddress"]["address"] for r in recipients] == [f"typed-{sfx}@example.com"]


def test_the_emailed_activation_url_uses_the_canonical_external_origin(mail_configured):
    transport, calls = _recorder()
    _invite(transport=transport)
    body = calls[0]["payload"]["message"]["body"]["content"]
    assert f"{CANONICAL}/portal/login?invitation=" in body
    assert "inbound-host.invalid" not in body, "the inbound Host header leaked into the email"


def test_the_emailed_url_carries_the_fresh_token_that_activates_the_account(mail_configured):
    transport, calls = _recorder()
    out = _invite(transport=transport)
    body = calls[0]["payload"]["message"]["body"]["content"]
    url = body.split('href="')[1].split('"')[0]
    token = parse_qs(urlsplit(url).query)["invitation"][0]
    with engine.connect() as c:
        row = c.execute(select(portal_invitations)
                        .order_by(portal_invitations.c.id.desc())).mappings().first()
    assert row["token_hash"] == _hash(token), "the emailed token is not the invitation's token"
    assert out.person_id


def test_the_subject_is_the_agreed_wording(mail_configured):
    transport, calls = _recorder()
    _invite(transport=transport)
    assert calls[0]["payload"]["message"]["subject"] == "Your 360Plus client portal invitation"


def test_the_email_contains_no_internal_identifiers(mail_configured):
    transport, calls = _recorder()
    out = _invite(transport=transport)
    payload = repr(calls[0]["payload"])
    for leak in ("person_id", "household_id", "portal_account", "access_type", "grant"):
        assert leak not in payload, f"the email exposed {leak}"
    assert f">{out.person_id}<" not in payload


# --- the raw token stays where it was ----------------------------------------------------------

def test_the_raw_token_is_never_persisted_logged_audited_or_redirected(mail_configured):
    transport, calls = _recorder()
    captured_audit = []
    session: dict = {}
    with patch("app.routes.portal_admin.write_audit_event",
               side_effect=lambda **kw: captured_audit.append(kw)):
        out = _invite(transport=transport, session=session)
    body = calls[0]["payload"]["message"]["body"]["content"]
    url = body.split('href="')[1].split('"')[0]
    token = parse_qs(urlsplit(url).query)["invitation"][0]

    # 1. not in the database
    with engine.connect() as c:
        row = c.execute(select(portal_invitations)
                        .order_by(portal_invitations.c.id.desc())).mappings().first()
    assert token not in {str(v) for v in row.values()}
    # 2. not in any audit event
    assert captured_audit
    for event in captured_audit:
        assert token not in repr(event), "the token reached an audit event"
        assert url not in repr(event), "the activation URL reached an audit event"
    # 3. not in the redirect
    location = out.response.headers["location"]
    assert token not in location and "invitation" not in location
    # 4. only in the one-time handoff and the email
    payload = _handoff(session)
    assert payload and payload["url"] == url
    assert _handoff(session) is None, "the handoff was not one-time"


def test_the_json_invite_endpoint_still_returns_no_token():
    import inspect

    from app.routes import portal_admin
    src = inspect.getsource(portal_admin.portal_admin_invite)
    assert '"account_id": account_id, "status": "invited"' in src
    assert "raw_token" not in src


def test_the_delivery_service_never_returns_the_activation_url_in_its_detail(mail_configured):
    transport, _ = _recorder(status_code=500)
    result = ED.send_portal_invitation(
        recipient_email="x@example.com", display_name="X",
        activation_url=f"{CANONICAL}/portal/login?invitation=SUPER-SECRET-TOKEN",
        graph_post=transport)
    assert result.delivered is False
    assert "SUPER-SECRET-TOKEN" not in result.detail
    assert "SUPER-SECRET-TOKEN" not in repr(result.audit_metadata)


# --- success reporting ---------------------------------------------------------------------

def test_success_reports_delivery_without_exposing_the_token(mail_configured):
    transport, _ = _recorder()
    sfx = uuid.uuid4().hex[:8]
    session: dict = {}
    _invite(transport=transport, email=f"banner-{sfx}@example.com", session=session, sfx=sfx)
    payload = _handoff(session)
    assert payload["delivered"] is True
    assert payload["delivery_detail"] == f"Invitation emailed to banner-{sfx}@example.com."
    assert "invitation=" not in payload["delivery_detail"]


def test_a_successful_send_writes_the_email_sent_audit_event_with_safe_metadata(mail_configured):
    transport, _ = _recorder()
    sfx = uuid.uuid4().hex[:8]
    out = _invite(transport=transport, email=f"aud-{sfx}@example.com", sfx=sfx)
    with engine.connect() as c:
        rows = c.execute(select(audit_events.c.action, audit_events.c.metadata)
                         .where(audit_events.c.action == "portal.admin.invitation_email_sent")
                         .order_by(audit_events.c.id.desc())).mappings().all()
    assert rows, "no invitation_email_sent audit event"
    meta = rows[0]["metadata"] or {}
    assert meta.get("provider") == "microsoft_graph"
    assert meta.get("delivery_status") == "sent"
    assert meta.get("recipient_domain") == "example.com"
    assert f"aud-{sfx}@example.com" not in repr(meta), "the full address was duplicated into audit"
    assert out.person_id


# --- failure is reported honestly ------------------------------------------------------------

def test_a_graph_failure_preserves_the_invitation_and_the_handoff(mail_configured):
    transport, calls = _recorder(status_code=500)
    session: dict = {}
    out = _invite(transport=transport, session=session)
    assert calls, "no send was attempted"
    # the invitation still exists
    with engine.connect() as c:
        assert c.execute(select(portal_invitations.c.id)).fetchall()
    # and the fallback link is still handed to staff
    payload = _handoff(session)
    assert payload and payload["url"].startswith(f"{CANONICAL}/portal/login?invitation=")
    assert payload["delivered"] is False
    assert out.response.status_code == 303


def test_a_graph_failure_shows_a_normal_warning_not_a_raw_exception(mail_configured):
    transport, _ = _recorder(status_code=500)
    session: dict = {}
    _invite(transport=transport, session=session)
    detail = _handoff(session)["delivery_detail"]
    assert "status 500" in detail
    assert "Traceback" not in detail and "SHOULD-NEVER-BE-SHOWN" not in detail
    assert "graph body" not in detail.lower()


def test_a_transport_exception_is_reported_not_raised(mail_configured):
    def exploding(token, sender, payload):
        raise ConnectionError("ECONNREFUSED 10.0.0.5:443")

    session: dict = {}
    out = _invite(transport=exploding, session=session)
    assert out.response.status_code == 303, "an unhandled exception escaped the route"
    detail = _handoff(session)["delivery_detail"]
    assert "network error" in detail and "ECONNREFUSED" not in detail


def test_an_auth_failure_never_leaks_msal_detail(mail_configured, monkeypatch):
    def boom():
        raise RuntimeError("AADSTS7000215: Invalid client secret provided for tenant abc-123")

    monkeypatch.setattr(ED, "_acquire_app_token", boom)
    transport, calls = _recorder()
    result = ED.send_portal_invitation(recipient_email="x@example.com", display_name="X",
                                       activation_url=f"{CANONICAL}/portal/login?invitation=t",
                                       graph_post=transport)
    assert result.delivered is False and result.failure_class == "auth_failed"
    assert "AADSTS" not in result.detail and "abc-123" not in result.detail
    assert not calls, "a send was attempted without a token"


def test_a_failure_writes_the_failure_audit_event_with_safe_metadata(mail_configured):
    transport, _ = _recorder(status_code=503)
    _invite(transport=transport)
    with engine.connect() as c:
        rows = c.execute(select(audit_events.c.metadata)
                         .where(audit_events.c.action == "portal.admin.invitation_email_failed")
                         .order_by(audit_events.c.id.desc())).mappings().all()
    assert rows
    meta = rows[0]["metadata"] or {}
    assert meta.get("delivery_status") == "failed"
    assert meta.get("failure_class") == "graph_http_503"
    assert meta.get("provider") == "microsoft_graph"


# --- fail-closed configuration ----------------------------------------------------------------

def test_disabled_email_does_not_claim_that_anything_was_sent(monkeypatch):
    monkeypatch.delenv("PORTAL_EMAIL_ENABLED", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", CANONICAL)
    transport, calls = _recorder()
    session: dict = {}
    _invite(transport=transport, session=session)
    assert not calls, "a send was attempted while delivery is disabled"
    payload = _handoff(session)
    assert payload["delivered"] is False and payload["delivery_status"] == "disabled"
    assert "not configured" in payload["delivery_detail"]
    assert payload["url"].startswith(f"{CANONICAL}/portal/login?invitation=")


def test_enabled_but_unconfigured_email_fails_cleanly(monkeypatch):
    monkeypatch.setenv("PORTAL_EMAIL_ENABLED", "true")
    monkeypatch.delenv("PORTAL_EMAIL_SENDER", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", CANONICAL)
    transport, calls = _recorder()
    session: dict = {}
    _invite(transport=transport, session=session)
    assert not calls
    payload = _handoff(session)
    assert payload["delivered"] is False and payload["delivery_status"] == "not_configured"
    assert "PORTAL_EMAIL_SENDER" in payload["delivery_detail"]


def test_configuration_status_names_what_is_missing_without_values(monkeypatch):
    monkeypatch.setenv("PORTAL_EMAIL_ENABLED", "true")
    monkeypatch.setenv("PORTAL_EMAIL_SENDER", "s@example.com")
    monkeypatch.setenv("PORTAL_EMAIL_TENANT_ID", "t")
    monkeypatch.setenv("PORTAL_EMAIL_CLIENT_ID", "c")
    monkeypatch.setenv("PORTAL_EMAIL_CLIENT_SECRET", "super-secret-value")
    ready, reason = ED.configuration_status()
    assert ready is True and reason == ""
    monkeypatch.delenv("PORTAL_EMAIL_CLIENT_SECRET", raising=False)
    ready, reason = ED.configuration_status()
    assert ready is False and reason == "PORTAL_EMAIL_CLIENT_SECRET is not set"
    assert "super-secret-value" not in reason


def test_startup_warns_when_email_is_on_but_unsendable(monkeypatch):
    from app.config import _portal_email_warnings

    monkeypatch.setenv("PORTAL_EMAIL_ENABLED", "true")
    monkeypatch.delenv("PORTAL_EMAIL_SENDER", raising=False)
    warnings = _portal_email_warnings()
    assert len(warnings) == 1 and "PORTAL_EMAIL_SENDER" in warnings[0]
    monkeypatch.delenv("PORTAL_EMAIL_ENABLED", raising=False)
    assert _portal_email_warnings() == [], "off is a normal state and must be silent"


# --- content safety ----------------------------------------------------------------------------

def test_a_client_controlled_display_name_cannot_inject_html():
    hostile = '<img src=x onerror="alert(1)"> & "quoted"'
    html_body, text_body = ED.invitation_bodies(hostile, f"{CANONICAL}/portal/login?invitation=t")
    assert "<img" not in html_body and 'onerror="alert(1)"' not in html_body
    assert "&lt;img" in html_body and "&amp;" in html_body
    assert hostile in text_body, "the plain-text alternative should carry the literal name"


def test_the_activation_url_is_escaped_in_the_html_body():
    html_body, _ = ED.invitation_bodies(
        "Client", f'{CANONICAL}/portal/login?invitation=a"onmouseover="x')
    assert 'onmouseover="x' not in html_body
    assert "&quot;" in html_body


def test_both_an_html_and_a_plain_text_body_are_produced():
    html_body, text_body = ED.invitation_bodies("Michael", f"{CANONICAL}/portal/login?invitation=t")
    assert html_body.startswith("<p>") and "<a href=" in html_body
    assert "<" not in text_body and "360 Wealth Consulting" in text_body
    assert "You have been invited to access the 360Plus client portal." in text_body


def test_an_invalid_recipient_is_refused_before_any_send(mail_configured):
    transport, calls = _recorder()
    for bad in ("", "not-an-email", "a@b@c.com", "ok@example.com\r\nBcc: evil@example.com"):
        result = ED.send_portal_invitation(recipient_email=bad, display_name="X",
                                           activation_url=f"{CANONICAL}/x", graph_post=transport)
        assert result.delivered is False and result.failure_class == "invalid_recipient"
    assert not calls, "a send was attempted for an invalid recipient"


# --- nothing else moved --------------------------------------------------------------------------

def test_no_real_network_call_can_happen_in_these_tests():
    """The transport is injected everywhere; the real one is only the default."""
    import inspect
    src = inspect.getsource(ED.send_portal_invitation)
    assert "(graph_post or _post_to_graph)" in src
    assert "import requests" in inspect.getsource(ED._post_to_graph)


# --- the credential boundary: a DEDICATED app registration ---------------------------------------

def test_the_mail_app_uses_its_own_client_id_and_secret_not_the_main_integration(monkeypatch):
    """Application Mail.Send sends with no signed-in user; that privilege must never attach to the
    app registration handling the rest of the Microsoft integration."""
    captured = {}

    class _Client:
        def __init__(self, client_id, authority, client_credential):
            captured.update(client_id=client_id, authority=authority, secret=client_credential)

        def acquire_token_for_client(self, scopes):
            captured["scopes"] = scopes
            return {"access_token": "app-token"}

    monkeypatch.setenv("PORTAL_EMAIL_TENANT_ID", "mail-tenant")
    monkeypatch.setenv("PORTAL_EMAIL_CLIENT_ID", "mail-client")
    monkeypatch.setenv("PORTAL_EMAIL_CLIENT_SECRET", "mail-secret")
    # Values the mail path must IGNORE entirely.
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "main-tenant")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "main-client")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "main-secret")
    monkeypatch.setattr("msal.ConfidentialClientApplication", _Client)

    assert ED._acquire_app_token() == "app-token"
    assert captured["client_id"] == "mail-client", "the main integration's client id was used"
    assert captured["secret"] == "mail-secret", "the main integration's secret was used"
    assert "mail-tenant" in captured["authority"]
    for main in ("main-client", "main-secret", "main-tenant"):
        assert main not in repr(captured), f"{main} leaked into the mail client"


def test_the_app_token_requests_the_default_scope(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, **kw):
            pass

        def acquire_token_for_client(self, scopes):
            captured["scopes"] = scopes
            return {"access_token": "t"}

    monkeypatch.setattr("msal.ConfidentialClientApplication", _Client)
    ED._acquire_app_token()
    assert captured["scopes"] == ["https://graph.microsoft.com/.default"]


def test_microsoft_credentials_alone_cannot_enable_portal_email(monkeypatch):
    """Setting only the main integration's credentials must NOT switch delivery on."""
    monkeypatch.setenv("PORTAL_EMAIL_ENABLED", "true")
    monkeypatch.setenv("PORTAL_EMAIL_SENDER", "s@example.com")
    for name in ED.REQUIRED_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "t")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "c")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "s")
    ready, reason = ED.configuration_status()
    assert ready is False and reason.startswith("PORTAL_EMAIL_")


def test_the_module_never_reads_the_main_microsoft_credentials():
    """Source guard: no MICROSOFT_* env read, and no import of the delegated token machinery."""
    import re

    src = open("app/services/email_delivery.py", encoding="utf-8").read()
    code = re.sub(r'"""(?:.|\n)*?"""', "", src)            # drop docstrings; prose may name them
    code = re.sub(r"^\s*#.*$", "", code, flags=re.M)        # and comments
    for forbidden in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID"):
        assert forbidden not in code, f"the mail service reads {forbidden}"
    for forbidden in ("microsoft_identity", "build_msal_client", "get_microsoft_access_token",
                      "account_for_principal", "microsoft_accounts", "get_microsoft365_config"):
        assert forbidden not in code, f"the mail service imports delegated machinery: {forbidden}"


def test_required_credentials_are_the_dedicated_namespace_only():
    assert ED.REQUIRED_CREDENTIALS == ("PORTAL_EMAIL_TENANT_ID", "PORTAL_EMAIL_CLIENT_ID",
                                       "PORTAL_EMAIL_CLIENT_SECRET")
    assert not any(name.startswith("MICROSOFT_") for name in ED.REQUIRED_CREDENTIALS)


def test_sendmail_targets_the_configured_sender_and_there_is_no_me_fallback():
    import inspect

    assert ED.GRAPH_SENDMAIL_TEMPLATE == "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
    src = open("app/services/email_delivery.py", encoding="utf-8").read()
    code = src.split('"""', 2)[-1]                          # skip the module docstring
    assert "/me/sendMail" not in code, "a delegated /me fallback exists"
    assert "sender=sender" in inspect.getsource(ED._post_to_graph)


def test_the_delegated_token_cache_system_is_untouched():
    """The existing per-staff Microsoft account/token-cache code must not have moved."""
    from app.services import microsoft_identity as MI

    assert hasattr(MI, "build_msal_client") and hasattr(MI, "get_microsoft_access_token")
    assert hasattr(MI, "persist_token_cache") and hasattr(MI, "account_for_principal")
    src = open("app/services/email_delivery.py", encoding="utf-8").read()
    assert "persist_token_cache" not in src and "microsoft_accounts" not in src


def test_the_delegated_scopes_and_portal_oidc_are_untouched():
    from app.services.microsoft_identity import GRAPH_DELEGATED_SCOPES

    assert "Mail.Send" in GRAPH_DELEGATED_SCOPES          # pre-existing delegated send-as-self
    src = open("app/services/email_delivery.py", encoding="utf-8").read()
    assert "GRAPH_DELEGATED_SCOPES" not in src, "outbound mail altered the delegated scope set"
    assert "PORTAL_OIDC" not in src, "outbound mail touched portal sign-in configuration"


def test_the_route_does_not_own_graph_http_mechanics():
    import inspect

    from app.routes import portal_admin
    src = inspect.getsource(portal_admin)
    assert "graph.microsoft.com" not in src
    assert "email_delivery.send_portal_invitation" in src


def test_invitation_acceptance_and_hashing_are_unchanged(mail_configured):
    """The emailed token still activates the account through the existing path."""
    from app.portal.service import accept_invitation, sign_in_with_subject

    transport, calls = _recorder()
    _invite(transport=transport)
    body = calls[0]["payload"]["message"]["body"]["content"]
    url = body.split('href="')[1].split('"')[0]
    token = parse_qs(urlsplit(url).query)["invitation"][0]
    subject = f"microsoft:OID-{uuid.uuid4().hex[:10]}"
    account_id = accept_invitation(token, subject, True)
    assert sign_in_with_subject(subject, True) == account_id
    with pytest.raises(ValueError, match="MFA"):
        sign_in_with_subject(subject, False)
