"""Outbound transactional email for Client360 — Microsoft Graph, application identity.

The portal invitation is the first message Client360 sends on its own behalf rather than on behalf
of a signed-in staff member, so it cannot use the delegated path
(``communications.mail_send`` → ``/me/sendMail`` with the staff user's own token). A client's
invitation must come from the firm's mailbox whether or not the staff member who pressed the button
has connected their Microsoft account. That means an APPLICATION token.

CREDENTIAL BOUNDARY — the reason this module has its own MSAL client.

Application ``Mail.Send`` is privileged: it sends with no signed-in user. It is therefore issued to
a SEPARATE, dedicated Entra app registration whose only Graph application permission is
``Mail.Send``, configured through its own ``PORTAL_EMAIL_*`` namespace. This module never reads
``MICROSOFT_CLIENT_ID``/``MICROSOFT_CLIENT_SECRET`` and never imports the delegated identity module,
so that privilege can never be attached to the app registration handling the rest of the Microsoft
integration. There is no fallback to the delegated client: missing dedicated credentials mean no
send, never a quieter send under someone else's identity.

    Entra + Exchange requirements (an administrator must do these; code cannot):
      * dedicated app registration, Graph APPLICATION permission ``Mail.Send`` ONLY, admin consent;
      * Exchange Online Application RBAC scoping that app to the ``PORTAL_EMAIL_SENDER`` mailbox.
    ``PORTAL_EMAIL_SENDER`` here selects which mailbox to send from — it is NOT a security
    boundary. Only Exchange can enforce that the app may send as that mailbox and no other.

Fail-closed: delivery is off unless ``PORTAL_EMAIL_ENABLED`` is true AND a sender mailbox is
configured AND the Microsoft credentials resolve. Every other outcome is reported honestly so the
caller can tell staff the truth rather than claiming a send that did not happen.

NOTHING here logs, audits, persists or returns the activation URL. It is a live credential: it is
formatted into the message body and otherwise never leaves this function's frame.
"""
from __future__ import annotations

import html
import os
import re
import uuid
from dataclasses import dataclass

GRAPH_SENDMAIL_TEMPLATE = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
#: App-only token request. `.default` asks for whatever application permissions are consented.
GRAPH_APP_SCOPE = ["https://graph.microsoft.com/.default"]

#: The DEDICATED mail credentials. MICROSOFT_* is deliberately absent: application Mail.Send must
#: not be attached to the main Client360 integration's app registration.
REQUIRED_CREDENTIALS = ("PORTAL_EMAIL_TENANT_ID", "PORTAL_EMAIL_CLIENT_ID",
                        "PORTAL_EMAIL_CLIENT_SECRET")

PROVIDER = "microsoft_graph"
SUBJECT = "Your 360Plus client portal invitation"
CODE_SUBJECT = "Your 360Plus verification code"
#: Stated in the invitation email so the client knows how long they have. Kept in step with
#: app.portal.service.INVITATION_TTL_HOURS by test_portal_email_auth.py — the two must not drift.
INVITATION_EXPIRY_DAYS = 7
FIRM_NAME = "360 Wealth Consulting"

# Honest outcomes. Mirrors the vocabulary of app/services/notification_providers.py.
SENT = "sent"
DISABLED = "disabled"
NOT_CONFIGURED = "not_configured"
FAILED = "failed"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class DeliveryResult:
    """The honest outcome of one send attempt.

    ``detail`` is staff-facing and safe: it never contains a Graph response body, a stack trace, a
    token, or the activation URL."""

    delivered: bool
    status: str
    provider: str = PROVIDER
    failure_class: str | None = None
    detail: str = ""
    recipient_domain: str = ""

    @property
    def audit_metadata(self) -> dict:
        """Safe metadata for an audit event — status and provider only, never content."""
        data = {"provider": self.provider, "delivery_status": self.status}
        if self.failure_class:
            data["failure_class"] = self.failure_class
        return data


def _env(name, default=""):
    return (os.getenv(name, default) or "").strip()


def is_enabled() -> bool:
    return _env("PORTAL_EMAIL_ENABLED").lower() in {"1", "true", "yes", "on"}


def sender_mailbox() -> str:
    return _env("PORTAL_EMAIL_SENDER")


def configuration_status() -> tuple[bool, str]:
    """``(ready, reason)``. Never raises, never echoes a secret value."""
    if not is_enabled():
        return False, "PORTAL_EMAIL_ENABLED is not set"
    if not sender_mailbox():
        return False, "PORTAL_EMAIL_SENDER is not set"
    for name in REQUIRED_CREDENTIALS:
        if not _env(name):
            return False, f"{name} is not set"
    return True, ""


def _valid_recipient(address: str | None) -> str | None:
    address = (address or "").strip()
    # CR/LF would allow header injection; the regex keeps it to one plain address.
    if not address or "\r" in address or "\n" in address or not _EMAIL_RE.match(address):
        return None
    return address


def _build_mail_client():
    """An MSAL confidential client for the DEDICATED mail app registration.

    Built here, from the PORTAL_EMAIL_* namespace only. It deliberately does not call
    ``microsoft_identity.build_msal_client``: that factory reads MICROSOFT_CLIENT_ID/SECRET, and
    reusing it would put application Mail.Send on the main integration's app registration."""
    import msal

    return msal.ConfidentialClientApplication(
        client_id=_env("PORTAL_EMAIL_CLIENT_ID"),
        authority=f"https://login.microsoftonline.com/{_env('PORTAL_EMAIL_TENANT_ID')}",
        client_credential=_env("PORTAL_EMAIL_CLIENT_SECRET"),
    )


def _acquire_app_token() -> str:
    """An APPLICATION Graph token for the dedicated mail app. No delegated fallback exists."""
    result = _build_mail_client().acquire_token_for_client(scopes=GRAPH_APP_SCOPE)
    token = (result or {}).get("access_token")
    if not token:
        # result["error_description"] can carry tenant/app detail — deliberately not surfaced.
        raise RuntimeError("no application access token")
    return token


def _post_to_graph(token: str, sender: str, payload: dict):
    """Real Graph transport. Injected in tests — no network call is ever made there."""
    import requests

    return requests.post(GRAPH_SENDMAIL_TEMPLATE.format(sender=sender), json=payload, timeout=30,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"})


def invitation_bodies(display_name: str, activation_url: str) -> tuple[str, str]:
    """``(html_body, text_body)``.

    ``display_name`` comes from a client record and is therefore untrusted for HTML purposes: it is
    escaped, so a name containing markup cannot inject anything into the message. The activation URL
    is server-built from the canonical origin plus a URL-encoded token."""
    greeting = display_name.strip() or "there"
    text = (
        f"Hello {greeting},\n\n"
        "You have been invited to access the 360Plus client portal.\n\n"
        "Use the secure link below to activate your portal access:\n\n"
        f"{activation_url}\n\n"
        f"This link expires in {INVITATION_EXPIRY_DAYS} days. When you open it we will email you a "
        "6-digit code to confirm it is you.\n\n"
        "For your security, this invitation link is intended only for you. If you were not "
        f"expecting this invitation, please contact {FIRM_NAME}.\n\n"
        f"{FIRM_NAME}\n"
    )
    safe_name = html.escape(greeting)
    safe_url = html.escape(activation_url, quote=True)
    html_body = (
        f"<p>Hello {safe_name},</p>"
        "<p>You have been invited to access the 360Plus client portal.</p>"
        "<p>Use the secure link below to activate your portal access:</p>"
        f'<p><a href="{safe_url}">{safe_url}</a></p>'
        f"<p>This link expires in {INVITATION_EXPIRY_DAYS} days. When you open it we will email you "
        "a 6-digit code to confirm it is you.</p>"
        "<p>For your security, this invitation link is intended only for you. If you were not "
        f"expecting this invitation, please contact {html.escape(FIRM_NAME)}.</p>"
        f"<p>{html.escape(FIRM_NAME)}</p>"
    )
    return html_body, text


def send_portal_invitation(*, recipient_email, display_name, activation_url,
                           request_id=None, graph_post=None) -> DeliveryResult:
    """Send one portal activation email. Never raises — the outcome is the return value.

    The caller has already created the invitation by this point, so an exception here would leave
    a real invitation behind an unhandled error. Every failure mode is therefore a
    :class:`DeliveryResult` the caller can show to staff alongside the fallback activation link."""
    request_id = request_id or f"portal-invite-mail-{uuid.uuid4()}"

    recipient = _valid_recipient(recipient_email)
    if not recipient:
        return DeliveryResult(delivered=False, status=FAILED, failure_class="invalid_recipient",
                              detail="The invitation email address is not a valid address.")
    domain = recipient.rsplit("@", 1)[-1]

    ready, reason = configuration_status()
    if not ready:
        status = DISABLED if not is_enabled() else NOT_CONFIGURED
        return DeliveryResult(
            delivered=False, status=status, failure_class="provider_not_configured",
            recipient_domain=domain,
            detail=f"Portal invitation email is not configured ({reason}).")

    if not (activation_url or "").strip():
        return DeliveryResult(delivered=False, status=FAILED, failure_class="no_activation_url",
                              recipient_domain=domain,
                              detail="No activation link was available to send.")

    html_body, text_body = invitation_bodies(display_name or "", activation_url)
    payload = {
        "message": {
            "subject": SUBJECT,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
        },
        "saveToSentItems": True,
    }
    # Graph takes one body; the plain-text alternative is kept for callers/providers that want it.
    payload["message"].setdefault("bodyPreview", text_body[:255])

    sender = sender_mailbox()
    try:
        token = _acquire_app_token()
    except Exception:                                     # noqa: BLE001 — never leak MSAL detail
        return DeliveryResult(delivered=False, status=FAILED, failure_class="auth_failed",
                              recipient_domain=domain,
                              detail="Client360 could not authenticate to Microsoft 365 to send "
                                     "the invitation.")
    try:
        response = (graph_post or _post_to_graph)(token, sender, payload)
    except Exception:                                     # noqa: BLE001 — network/transport
        return DeliveryResult(delivered=False, status=FAILED, failure_class="transport_error",
                              recipient_domain=domain,
                              detail="The invitation email could not be sent (network error).")

    code = getattr(response, "status_code", 0)
    if code in (200, 202):
        return DeliveryResult(delivered=True, status=SENT, recipient_domain=domain,
                              detail=f"Invitation emailed to {recipient}.")
    # The Graph body can echo the message (and therefore the activation URL) and tenant detail, so
    # only the status code is retained.
    return DeliveryResult(delivered=False, status=FAILED, failure_class=f"graph_http_{code}",
                          recipient_domain=domain,
                          detail="Microsoft 365 refused the invitation email "
                                 f"(status {code}).")


def verification_code_bodies(display_name: str, code: str) -> tuple[str, str]:
    """``(html_body, text_body)`` for a one-time sign-in code.

    ``display_name`` comes from a client record and is escaped for the HTML part. The code is
    generated server-side and is always six digits, but it is escaped too rather than trusted by
    construction. There is NO LINK: the code must be typed back into the browser session that asked
    for it, so forwarding the mail does not hand anyone a working credential."""
    greeting = (display_name or "").strip() or "there"
    text = (
        f"Hello {greeting},\n\n"
        f"Your 360Plus verification code is: {code}\n\n"
        "Enter it on the sign-in screen to continue. The code expires shortly and can only be used "
        "once.\n\n"
        f"If you did not request this code, you can ignore this email and contact {FIRM_NAME}.\n\n"
        f"{FIRM_NAME}\n"
    )
    safe_name, safe_code = html.escape(greeting), html.escape(code)
    html_body = (
        f"<p>Hello {safe_name},</p>"
        "<p>Your 360Plus verification code is:</p>"
        f'<p style="font-size:28px;font-weight:bold;letter-spacing:4px">{safe_code}</p>'
        "<p>Enter it on the sign-in screen to continue. The code expires shortly and can only be "
        "used once.</p>"
        "<p>If you did not request this code, you can ignore this email and contact "
        f"{html.escape(FIRM_NAME)}.</p>"
        f"<p>{html.escape(FIRM_NAME)}</p>"
    )
    return html_body, text


def send_portal_verification_code(*, recipient_email, display_name, code, purpose="login",
                                  graph_post=None) -> DeliveryResult:
    """Send one sign-in code. Never raises — the outcome is the return value.

    Same dedicated app-only registration as the invitation (``PORTAL_EMAIL_*``): an application
    ``Mail.Send`` identity, never a staff mailbox token and never a delegated sign-in.

    The code is formatted into the message and otherwise never leaves this frame. Nothing here — the
    :class:`DeliveryResult`, its ``detail``, its ``audit_metadata`` — carries the code, and the Graph
    response body is discarded for the same reason it is on the invitation path: it can echo the
    message that contains it."""
    recipient = _valid_recipient(recipient_email)
    if not recipient:
        return DeliveryResult(delivered=False, status=FAILED, failure_class="invalid_recipient",
                              detail="The account email address is not a valid address.")
    domain = recipient.rsplit("@", 1)[-1]

    ready, reason = configuration_status()
    if not ready:
        status = DISABLED if not is_enabled() else NOT_CONFIGURED
        return DeliveryResult(delivered=False, status=status,
                              failure_class="provider_not_configured", recipient_domain=domain,
                              detail=f"Verification email is not configured ({reason}).")
    if not (code or "").strip():
        return DeliveryResult(delivered=False, status=FAILED, failure_class="no_code",
                              recipient_domain=domain, detail="No verification code was available.")

    html_body, text_body = verification_code_bodies(display_name or "", code)
    payload = {"message": {"subject": CODE_SUBJECT,
                           "body": {"contentType": "HTML", "content": html_body},
                           "toRecipients": [{"emailAddress": {"address": recipient}}]},
               "saveToSentItems": False}
    # NOTE: no bodyPreview. On the invitation path it carries a link; here it would carry the code
    # into a field mail clients surface in list views and notifications.
    _ = text_body

    try:
        token = _acquire_app_token()
    except Exception:                                     # noqa: BLE001 — never leak MSAL detail
        return DeliveryResult(delivered=False, status=FAILED, failure_class="auth_failed",
                              recipient_domain=domain,
                              detail="Client360 could not authenticate to Microsoft 365 to send the "
                                     "verification code.")
    try:
        response = (graph_post or _post_to_graph)(token, sender_mailbox(), payload)
    except Exception:                                     # noqa: BLE001 — network/transport
        return DeliveryResult(delivered=False, status=FAILED, failure_class="transport_error",
                              recipient_domain=domain,
                              detail="The verification code could not be sent (network error).")

    status_code = getattr(response, "status_code", 0)
    if status_code in (200, 202):
        return DeliveryResult(delivered=True, status=SENT, recipient_domain=domain,
                              detail="Verification code sent.")
    return DeliveryResult(delivered=False, status=FAILED,
                          failure_class=f"graph_http_{status_code}", recipient_domain=domain,
                          detail=f"Microsoft 365 refused the verification email (status {status_code}).")
