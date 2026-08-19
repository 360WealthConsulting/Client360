"""Portal email senders for the TaxDome-cutover workflows (P0-1).

Each function builds the NON-sensitive email content + an authenticated-portal deep link, records an F5.4
notification intent (``channel='email'``) via :func:`app.services.notifications.record_notification`, and
executes it through F5.5 :func:`app.services.notification_dispatch.dispatch_notification` — reusing the
existing ledger, provider registry, and append-only delivery-attempt infrastructure. It does NOT create a
parallel notification system and does NOT touch the in-app ``portal_notifications`` path.

Privacy boundary (critical): the secure-message email says only that a message is waiting and links the
client into the authenticated portal — it never contains the conversation body or attachment content.
Invoice emails carry only the invoice number, amount, and a portal link. Invitation/reset emails contain
their single-use token ONLY inside the portal link (as they must); those tokens are never written to logs
or audit metadata (this module writes no audit events, and the F5.2 providers never log title/body).
"""
from __future__ import annotations

from app.services import notification_dispatch as dispatch
from app.services import notification_email
from app.services import notifications as ledger

_RECIPIENT_TYPE = "portal_account"


def email_configured() -> bool:
    """Fully fail-closed gate the wiring uses before attempting delivery: a configured transport AND a
    valid public portal URL (see notification_email.email_delivery_ready). Referenced via the module so a
    monkeypatched transport is honored in tests."""
    return notification_email.email_delivery_ready()


def portal_link(path: str) -> str:
    path = path if path.startswith("/") else f"/{path}"
    return f"{notification_email.public_portal_url()}{path}"


def _send_direct(*, email, subject, body):
    """Send an email SYNCHRONOUSLY via the provider WITHOUT persisting it. Used for invitation/reset,
    whose links carry a single-use secret token — so the raw token never lands in the long-lived
    notification ledger (body/dedupe_key). Returns the F5.2 DeliveryResult; records no ledger row and no
    token anywhere at rest (the token exists only in-memory for this call and in the sent email)."""
    from app.services import notification_providers as providers
    provider = providers.default_registry().get("email")
    return provider.deliver_result(recipient=email, title=subject, body=body)


def _send(*, email, notification_type, subject, body, dedupe_key, conn=None):
    """Record a TOKEN-FREE email intent (idempotent by dedupe_key) and dispatch it once. For the
    non-sensitive alerts (secure-message / invoice) only — the persisted body/dedupe_key never contain a
    secret. Returns the F5.5 DispatchResult; a delivery problem is recorded as a delivery attempt."""
    rec = ledger.record_notification(
        notification_type=notification_type, recipient_ref=email, recipient_type=_RECIPIENT_TYPE,
        channel="email", title=subject, body=body, dedupe_key=dedupe_key, conn=conn)
    return dispatch.dispatch_notification(notification_uid=rec.notification_uid, conn=conn)


# --- 1. portal invitation -----------------------------------------------------

def send_invitation_email(*, email, display_name, token, expires_hours=72, conn=None):  # noqa: ARG001
    link = portal_link(f"/portal/activate?token={token}")
    subject = "You're invited to your Client360 secure portal"
    body = (
        f"Hello {display_name or 'there'},\n\n"
        "Your advisor has invited you to the Client360 secure client portal, where you can view and share "
        "documents, read secure messages, and see invoices.\n\n"
        f"Activate your access (link expires in {expires_hours} hours):\n{link}\n\n"
        "If you did not expect this invitation, you can ignore this email.")
    # Direct send: the single-use activation token is never persisted (not to the ledger, not to logs).
    return _send_direct(email=email, subject=subject, body=body)


# --- 2. password reset --------------------------------------------------------

def send_password_reset_email(*, email, token, expires_minutes=30, conn=None):  # noqa: ARG001
    link = portal_link(f"/portal/reset?token={token}")
    subject = "Reset your Client360 portal password"
    body = (
        "We received a request to reset your Client360 portal password.\n\n"
        f"Reset it here (link expires in {expires_minutes} minutes):\n{link}\n\n"
        "If you did not request this, you can safely ignore this email — your password will not change.")
    # Direct send: the single-use reset token is never persisted (not to the ledger, not to logs).
    return _send_direct(email=email, subject=subject, body=body)


# --- 3. secure-message notification (NO message content) ----------------------

def send_secure_message_email(*, email, message_id, conn=None):
    link = portal_link("/portal/messages")
    subject = "You have a new secure message in Client360"
    # Deliberately content-free: no conversation body, no attachment, no sender detail — just sign in.
    body = (
        "You have a new secure message from your advisor in the Client360 portal.\n\n"
        f"Sign in to read it securely:\n{link}\n\n"
        "For your security, the message itself is not included in this email.")
    return _send(email=email, notification_type="portal.secure_message", subject=subject, body=body,
                 dedupe_key=f"portal.secure_message:{message_id}", conn=conn)


# --- 4. invoice / payment notification (non-sensitive summary + link) ---------

def send_invoice_email(*, email, invoice_id, invoice_number, amount_label, event="invoice.issued", conn=None):
    link = portal_link(f"/portal/billing/invoices/{invoice_id}")
    if event == "payment.recorded":
        subject = "Payment received — Client360"
        body = (f"We've recorded a payment toward invoice {invoice_number}.\n\n"
                f"View the invoice and payment history:\n{link}")
    else:
        subject = "A new invoice is available in Client360"
        body = (f"Invoice {invoice_number} for {amount_label} is now available in your Client360 portal.\n\n"
                f"View and pay it here:\n{link}")
    return _send(email=email, notification_type="portal.billing", subject=subject, body=body,
                 dedupe_key=f"portal.billing:{event}:{invoice_id}:{email}", conn=conn)
