"""The portal login page must offer sign-in when the external IdP is actually registered.

The defect: ``app/templates/portal/login.html`` hard-coded "Portal identity provider configuration is
required for production sign-in." and ``portal_login()`` rendered it with ``context={}``. A fully
configured, registered, production-capable Microsoft provider therefore produced a login page that
told the client the opposite, with no way to reach ``/portal/auth/start`` — the authorization-code
flow existed and worked, but nothing linked to it.

Availability is decided by whether the PRODUCTION provider is registered, deliberately NOT by
``production_ready()``: that also requires ``portal.enabled`` and ``portal.production_signed_off``,
which govern whether client data may be served at all. Those gates still apply to every
authenticated request; they are simply not what decides whether a sign-in button is drawn.
"""
from __future__ import annotations

import re

import pytest

from app.portal.identity_local import LocalTestIdentityProvider
from app.portal.identity_microsoft import (
    MICROSOFT_PROVIDER_KEY,
    MicrosoftExternalIdentityProvider,
)
from app.portal.providers import PORTAL_IDENTITY_PROVIDERS
from app.routes.portal import PORTAL_LOGIN_ERRORS, portal_auth_start, portal_login
from tests._portal_util import fake_request, render

CONFIG_REQUIRED = "Portal identity provider configuration is required for production sign-in."
AUTH_START = "/portal/auth/start"


@pytest.fixture
def empty_provider_registry():
    """An empty registry for the duration of one test, restored afterwards."""
    saved = dict(PORTAL_IDENTITY_PROVIDERS._providers)
    PORTAL_IDENTITY_PROVIDERS._providers.clear()
    yield PORTAL_IDENTITY_PROVIDERS
    PORTAL_IDENTITY_PROVIDERS._providers.clear()
    PORTAL_IDENTITY_PROVIDERS._providers.update(saved)


def _microsoft_provider():
    return MicrosoftExternalIdentityProvider(
        issuer="https://contoso.ciamlogin.com/tenant",
        client_id="client-id-under-test",
        client_secret="not-a-real-secret",
    )


def _login_html(**kwargs) -> str:
    return render(portal_login(fake_request("/portal/login"), **kwargs))


# --- provider registered: the sign-in action appears --------------------------------

def test_login_offers_microsoft_sign_in_when_the_provider_is_registered(empty_provider_registry):
    """The defect, directly: a registered production provider must produce a usable sign-in action."""
    empty_provider_registry.register(_microsoft_provider())
    html = _login_html()
    assert f'href="{AUTH_START}"' in html, "the login page does not link to the sign-in flow"
    assert "Sign in with Microsoft" in html
    assert CONFIG_REQUIRED not in html, (
        "a fully configured provider still rendered the configuration-required message")


def test_the_sign_in_action_targets_the_real_authorization_code_flow(empty_provider_registry):
    """It must reach /portal/auth/start — the route that mints state/nonce/PKCE server-side."""
    empty_provider_registry.register(_microsoft_provider())
    html = _login_html()
    assert f'<a class="btn" href="{AUTH_START}">' in html
    # The redirect flow is the only way in: no local credential entry, no alternative post target.
    assert "password" not in html.lower(), "the login page offers credential entry"
    assert "verify_activation" not in html and "identity_assertion" not in html
    assert "/api/v1/portal/auth/invitations/accept" not in html, (
        "the page exposes the posted-assertion path, which has no state/nonce binding")


# --- provider absent: the existing fail-closed message is retained ---------------------

def test_login_keeps_the_configuration_message_when_no_provider_is_registered(
        empty_provider_registry):
    html = _login_html()
    assert CONFIG_REQUIRED in html
    assert AUTH_START not in html, "a sign-in action was offered with no provider behind it"


def test_the_local_test_provider_alone_never_produces_the_production_action(
        empty_provider_registry):
    """The synthetic provider is not production-capable and must not unlock real sign-in."""
    empty_provider_registry.register(LocalTestIdentityProvider())
    html = _login_html()
    assert CONFIG_REQUIRED in html
    assert AUTH_START not in html
    assert "Sign in with Microsoft" not in html


def test_a_non_production_capable_provider_on_the_microsoft_key_is_rejected(
        empty_provider_registry):
    """Availability requires the production key AND production_capable — not the key alone."""
    class _NotProductionCapable(LocalTestIdentityProvider):
        key = MICROSOFT_PROVIDER_KEY
        production_capable = False

    empty_provider_registry.register(_NotProductionCapable())
    assert CONFIG_REQUIRED in _login_html()


# --- the invitation parameter keeps its existing semantics ------------------------------

def test_an_invitation_is_forwarded_to_auth_start_unchanged(empty_provider_registry):
    """/portal/auth/start?invitation=<token> is the EXISTING mechanism: that route holds the token
    in the server-side session and never passes it to the IdP. The login page only carries it over."""
    empty_provider_registry.register(_microsoft_provider())
    html = _login_html(invitation="INV-TOKEN-123")
    assert f'href="{AUTH_START}?invitation=INV-TOKEN-123"' in html


def test_the_invitation_is_url_encoded_and_cannot_break_out_of_the_link(empty_provider_registry):
    """A token is attacker-supplied input on a PUBLIC page; it must not inject markup or parameters."""
    empty_provider_registry.register(_microsoft_provider())
    hostile = '" onmouseover="alert(1)" x="&next=//evil'
    html = _login_html(invitation=hostile)
    assert hostile not in html, "the raw token was written into the page unencoded"
    assert 'onmouseover="alert(1)"' not in html, "an event handler broke out of the href"
    assert "&next=//evil" not in html, "an unencoded parameter was smuggled into the link"
    # Every unsafe character percent-encoded inside the single href, nothing else emitted.
    assert ('<a class="btn" href="/portal/auth/start?invitation='
            '%22%20onmouseover%3D%22alert%281%29%22%20x%3D%22%26next%3D%2F%2Fevil">') in html


def test_no_invitation_yields_a_plain_auth_start_link(empty_provider_registry):
    empty_provider_registry.register(_microsoft_provider())
    assert f'href="{AUTH_START}"' in _login_html()


def test_an_invitation_does_not_reveal_a_sign_in_action_without_a_provider(
        empty_provider_registry):
    """An invitation link must not be a way around the fail-closed state."""
    html = _login_html(invitation="INV-TOKEN-123")
    assert CONFIG_REQUIRED in html
    assert AUTH_START not in html and "INV-TOKEN-123" not in html


# --- auth/start still fails closed on its own -------------------------------------------

def test_auth_start_still_fails_closed_without_the_production_provider(empty_provider_registry):
    """The page is a convenience; the route is the control. It must refuse independently."""
    response = portal_auth_start(fake_request("/portal/auth/start"))
    assert response.status_code == 303
    assert response.headers["location"] == "/portal/login?error=unavailable"


def test_auth_start_with_an_invitation_also_fails_closed_without_a_provider(
        empty_provider_registry):
    session: dict = {}
    request = fake_request("/portal/auth/start", session=session)
    response = portal_auth_start(request, invitation="INV-TOKEN-123")
    assert response.headers["location"] == "/portal/login?error=unavailable"
    assert session == {}, "state/nonce/verifier or the invitation were stored with no provider"


# --- availability is provider registration, NOT the data-exposure gates ------------------

def test_the_sign_in_action_does_not_depend_on_the_portal_data_gates(
        empty_provider_registry, portal_gates):
    """portal.enabled / portal.production_signed_off govern serving client data, not whether anyone
    can authenticate. With every gate off, a registered provider still yields a sign-in action —
    and the gates remain in force for every authenticated request."""
    empty_provider_registry.register(_microsoft_provider())
    portal_gates(set())                                    # every portal gate OFF
    from app.portal.gate import production_ready
    assert production_ready() is False
    html = _login_html()
    assert f'href="{AUTH_START}"' in html
    assert CONFIG_REQUIRED not in html


def test_login_page_never_leaks_provider_configuration(empty_provider_registry):
    """The page states availability only — never issuer, client id, secret, or tenant."""
    empty_provider_registry.register(_microsoft_provider())
    html = _login_html()
    for leak in ("not-a-real-secret", "client-id-under-test", "ciamlogin.com",
                 "PORTAL_OIDC", "client_secret"):
        assert leak not in html, f"login page disclosed {leak}"


# --- generic sign-in error feedback ------------------------------------------------------
#
# /portal/auth/start redirects failures to ?error=unavailable and /portal/auth/callback to
# ?error=failed. The page renders a fixed sentence for each and nothing else: the callback collapses
# bad state, bad nonce, bad signature, bad issuer/audience, expiry, unknown subject, a revoked
# account and a failed MFA check into ONE redirect so the browser cannot tell them apart, and the
# login page must not undo that.

def test_error_unavailable_renders_the_generic_temporary_message(empty_provider_registry):
    empty_provider_registry.register(_microsoft_provider())
    html = _login_html(error="unavailable")
    assert "Secure sign-in is temporarily unavailable." in html
    assert "contact your advisory team" in html
    assert "Sign-in could not be completed" not in html


def test_error_failed_renders_the_generic_retry_message(empty_provider_registry):
    empty_provider_registry.register(_microsoft_provider())
    html = _login_html(error="failed")
    assert "Sign-in could not be completed. Please try again." in html
    assert "temporarily unavailable" not in html


def test_only_the_two_codes_the_auth_routes_emit_are_supported():
    """The allow-list must match what /portal/auth/start and /portal/auth/callback redirect with."""
    assert set(PORTAL_LOGIN_ERRORS) == {"unavailable", "failed"}


@pytest.mark.parametrize("unknown", [
    "unknown", "UNAVAILABLE", "failed ", "", "0", "true",
    "mfa_required", "bad_nonce", "invalid_signature",          # plausible internal-detail guesses
])
def test_an_unrecognised_error_code_is_ignored_entirely(empty_provider_registry, unknown):
    """Unknown codes behave exactly like no error — and add nothing to the page."""
    empty_provider_registry.register(_microsoft_provider())
    baseline = _login_html()
    html = _login_html(error=unknown)
    assert "flash error" not in html, f"an unrecognised code ({unknown!r}) rendered an error box"
    assert html == baseline, "an unrecognised code changed the rendered page"


def test_no_error_code_renders_no_error_box(empty_provider_registry):
    empty_provider_registry.register(_microsoft_provider())
    assert "flash error" not in _login_html()


def test_a_hostile_error_value_is_never_reflected(empty_provider_registry):
    """The code is attacker-controllable on a PUBLIC page; it must never reach the document."""
    empty_provider_registry.register(_microsoft_provider())
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


def test_the_error_messages_disclose_no_authentication_detail(empty_provider_registry):
    """The uniform-error design: nothing in the message distinguishes WHY sign-in failed.

    Scoped to the error box itself — the page's own standing copy legitimately mentions invitations
    and multi-factor authentication, and that is not what must stay uniform."""
    empty_provider_registry.register(_microsoft_provider())
    for code in ("unavailable", "failed"):
        message = _flash_message(_login_html(error=code))
        assert message == PORTAL_LOGIN_ERRORS[code], "the rendered message is not the fixed sentence"
        for leak in ("nonce", "state", "signature", "issuer", "audience", "expired", "mfa",
                     "subject", "token", "claim", "pkce", "revoked", "invitation", "provider",
                     "aadsts", "traceback", "exception", "error="):
            assert leak not in message.lower(), f"the {code} message disclosed '{leak}'"


def test_both_error_messages_are_indistinguishable_about_cause(empty_provider_registry):
    """Neither message names a subsystem, a check, or an account state."""
    empty_provider_registry.register(_microsoft_provider())
    messages = {c: _flash_message(_login_html(error=c)) for c in ("unavailable", "failed")}
    assert messages["unavailable"] != messages["failed"]      # availability vs attempt is fine...
    for message in messages.values():                         # ...but neither says why
        assert "microsoft" not in message.lower()
        assert message.count(".") <= 2 and len(message) < 200, "the message is too specific"


def test_error_messages_never_expose_provider_configuration(empty_provider_registry):
    empty_provider_registry.register(_microsoft_provider())
    for code in ("unavailable", "failed"):
        html = _login_html(error=code)
        for leak in ("not-a-real-secret", "client-id-under-test", "ciamlogin.com",
                     "PORTAL_OIDC", "client_secret", "microsoft:"):
            assert leak not in html, f"the {code} page disclosed {leak}"


def test_the_sign_in_action_still_appears_alongside_an_error(empty_provider_registry):
    """A failed attempt must leave the client able to retry."""
    empty_provider_registry.register(_microsoft_provider())
    for code in ("unavailable", "failed"):
        html = _login_html(error=code)
        assert f'href="{AUTH_START}"' in html and "Sign in with Microsoft" in html
        assert CONFIG_REQUIRED not in html


def test_an_error_does_not_change_the_provider_unavailable_message(empty_provider_registry):
    """With no provider registered the fail-closed message stands, error code or not."""
    for code in ("unavailable", "failed", "unknown"):
        html = _login_html(error=code)
        assert CONFIG_REQUIRED in html
        assert AUTH_START not in html


def test_an_error_preserves_the_invitation_on_the_retry_link(empty_provider_registry):
    """Retrying after a failure must not silently drop first-time activation."""
    empty_provider_registry.register(_microsoft_provider())
    html = _login_html(invitation="INV-TOKEN-123", error="failed")
    assert f'href="{AUTH_START}?invitation=INV-TOKEN-123"' in html
    assert "Sign-in could not be completed. Please try again." in html
