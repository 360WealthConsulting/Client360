"""The client portal login page: an EMAIL form, and no external identity provider.

Clients authenticate by proving they hold the mailbox the firm invited — a six-digit code is emailed
to the address on the portal account and typed back. They are not tenant users and are no longer
asked to hold a Microsoft identity, so the page must offer no "Sign in with Microsoft" action and no
provider-specific language, and the routes it used no longer exist.

The generic-error contract is unchanged and still enforced here: /portal/auth/* and the activation
route collapse every cause into a fixed sentence, and this page must not undo that.

Staff/admin Microsoft authentication is a different surface entirely (/auth/login, /auth/callback)
and is untouched — see test_staff_auth_unchanged in tests/test_portal_email_auth.py.
"""
from __future__ import annotations

import re

import pytest

from app.routes.portal import PORTAL_LOGIN_ERRORS, portal_login
from tests._portal_util import fake_request, render

#: Anything that would put an external identity provider in front of a CLIENT. The routes these
#: named were removed with the client Microsoft path; nothing may reintroduce them in the copy.
MICROSOFT_AFFORDANCES = ("Sign in with Microsoft", "/portal/auth/start", "/portal/auth/callback",
                         "microsoft", "Microsoft", "OIDC", "MSAL", "Entra", "Azure")


def _login_html(**kwargs) -> str:
    return render(portal_login(fake_request("/portal/login"), **kwargs))


# --- the page is an email form, with no provider affordance -------------------------------

def test_login_renders_the_email_form():
    html = _login_html()
    assert 'action="/portal/login"' in html and 'name="email"' in html
    assert "Email me a code" in html
    assert 'method="post"' in html


def test_no_microsoft_affordance_appears_on_the_client_login_page():
    """The client Microsoft path is gone; nothing may put an action for it back on the page."""
    html = _login_html()
    for affordance in MICROSOFT_AFFORDANCES:
        assert affordance not in html, f"the client login page offers {affordance!r}"


def test_the_login_page_uses_no_developer_or_security_jargon():
    html = _login_html()
    for jargon in ("OAuth", "OIDC", "PKCE", "nonce", "identity provider", "auth_subject",
                   "multi-factor", "MFA", "token", "provider"):
        assert jargon not in html, f"client-facing copy contains {jargon!r}"


def test_an_invitation_is_forwarded_to_the_activation_route():
    """Old activation links in client inboxes still work; they are never rendered on the page."""
    response = portal_login(fake_request("/portal/login"), invitation="INV-TOKEN-123")
    assert response.status_code == 303
    assert response.headers["location"] == "/portal/activate?invitation=INV-TOKEN-123"


def test_the_invitation_is_url_encoded_on_the_way_to_activation():
    hostile = 'a&b=c d"><script>'
    response = portal_login(fake_request("/portal/login"), invitation=hostile)
    location = response.headers["location"]
    assert location.startswith("/portal/activate?invitation=")
    assert "&b=" not in location and "<script>" not in location and " " not in location


def test_the_sign_in_form_does_not_depend_on_the_portal_data_gates(portal_gates):
    """portal.enabled / portal.production_signed_off govern serving client data, not whether anyone
    can reach the sign-in form. With every gate off the email form still renders — and the gates
    remain in force for every authenticated request."""
    portal_gates(set())                                    # every portal gate OFF
    from app.portal.gate import production_ready
    assert production_ready() is False
    html = _login_html()
    assert 'name="email"' in html and "Email me a code" in html


def test_error_unavailable_renders_the_generic_temporary_message():
    html = _login_html(error="unavailable")
    assert "Secure sign-in is temporarily unavailable." in html
    assert "contact your advisory team" in html
    assert "Sign-in could not be completed" not in html


def test_error_failed_renders_the_generic_retry_message():
    html = _login_html(error="failed")
    assert "Sign-in could not be completed. Please try again." in html
    assert "temporarily unavailable" not in html


def test_only_the_codes_the_auth_routes_emit_are_supported():
    """The allow-list must match what the auth and activation routes redirect with."""
    assert set(PORTAL_LOGIN_ERRORS) == {"unavailable", "failed", "invitation"}


@pytest.mark.parametrize("unknown", [
    "unknown", "UNAVAILABLE", "failed ", "", "0", "true",
    "mfa_required", "bad_nonce", "invalid_signature",          # plausible internal-detail guesses
])
def test_an_unrecognised_error_code_is_ignored_entirely(unknown):
    """Unknown codes behave exactly like no error — and add nothing to the page."""
    baseline = _login_html()
    html = _login_html(error=unknown)
    assert "flash error" not in html, f"an unrecognised code ({unknown!r}) rendered an error box"
    assert html == baseline, "an unrecognised code changed the rendered page"


def test_no_error_code_renders_no_error_box():
    assert "flash error" not in _login_html()


def test_a_hostile_error_value_is_never_reflected():
    """The code is attacker-controllable on a PUBLIC page; it must never reach the document."""
    for hostile in ('<script>alert(1)</script>', '"><img src=x onerror=alert(1)>',
                    "failed'--", "unavailable<br>"):
        html = _login_html(error=hostile)
        assert hostile not in html
        assert "<script>" not in html and "onerror" not in html
        assert "flash error" not in html, "a hostile code was treated as a known error"


def _flash_message(html: str) -> str:
    """The rendered error box's text, or "" when there is none."""
    match = re.search(r'<div class="flash error" role="alert">(.*?)</div>', html, re.S)
    return match.group(1).strip() if match else ""


def test_the_error_messages_disclose_no_authentication_detail():
    """The uniform-error design: nothing in the message distinguishes WHY sign-in failed.

    Scoped to the error box itself — the page's own standing copy legitimately mentions invitations
    and multi-factor authentication, and that is not what must stay uniform."""
    for code in ("unavailable", "failed"):
        message = _flash_message(_login_html(error=code))
        assert message == PORTAL_LOGIN_ERRORS[code], "the rendered message is not the fixed sentence"
        for leak in ("nonce", "state", "signature", "issuer", "audience", "expired", "mfa",
                     "subject", "token", "claim", "pkce", "revoked", "invitation", "provider",
                     "aadsts", "traceback", "exception", "error="):
            assert leak not in message.lower(), f"the {code} message disclosed '{leak}'"


def test_both_error_messages_are_indistinguishable_about_cause():
    """Neither message names a subsystem, a check, or an account state."""
    messages = {c: _flash_message(_login_html(error=c)) for c in ("unavailable", "failed")}
    assert messages["unavailable"] != messages["failed"]      # availability vs attempt is fine...
    for message in messages.values():                         # ...but neither says why
        assert "microsoft" not in message.lower()
        assert message.count(".") <= 2 and len(message) < 200, "the message is too specific"


def test_the_email_form_still_appears_alongside_an_error():
    """A failed attempt must leave the client able to retry — with the email form, not a provider."""
    for code in ("unavailable", "failed", "invitation"):
        html = _login_html(error=code)
        assert 'name="email"' in html and "Email me a code" in html
        assert not any(a in html for a in MICROSOFT_AFFORDANCES)


def test_an_invitation_still_reaches_activation_even_with_an_error_code():
    """Retrying after a failure must not silently drop first-time activation."""
    response = portal_login(fake_request("/portal/login"), invitation="INV-TOKEN-123",
                            error="failed")
    assert response.status_code == 303
    assert response.headers["location"] == "/portal/activate?invitation=INV-TOKEN-123"
