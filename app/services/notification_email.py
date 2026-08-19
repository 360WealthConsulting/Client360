"""Outbound email transport for the canonical notification pipeline (TaxDome cutover P0-1).

This is the concrete ``email`` delegate behind the existing F5.2 ``ChannelProvider`` contract
(:mod:`app.services.notification_providers`). It does NOT introduce a parallel notification system:
email notifications are recorded as F5.4 ledger intents and executed by F5.5
:func:`app.services.notification_dispatch.dispatch_notification`, which records the append-only
delivery attempt and transitions the intent. This module only turns a ``(recipient, subject, body)``
into an actual SMTP send.

Transport choice — SMTP, not Microsoft Graph:
  The existing Microsoft integration is DELEGATED and deliberately READ-ONLY
  (``app.services.microsoft_identity.GRAPH_READ_SCOPES`` = ``Mail.Read`` only, "H10 scope reduction").
  Sending via Graph would require adding ``Mail.Send``, re-consenting the delegated OAuth, reversing that
  scope reduction, and sending from a personal delegated mailbox — not a clean reuse. SMTP is
  vendor-neutral, sends from a proper transactional ``From`` address, needs no Graph scope change, and
  works with the firm's existing Microsoft 365 / Exchange Online SMTP relay. It is implemented behind the
  generic provider interface, so the rest of Client360 stays transport-independent (a Graph transport
  could be added later as another ``EmailTransport`` without touching callers).

Fail-closed: with no transport configured, the ``email`` channel stays DISABLED — delivery is never
reported as successful. Configuration/credentials are read from the environment only; nothing is logged.
"""
from __future__ import annotations

import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage as _MimeMessage

# Configuration is read from the environment only — never hard-coded, never logged.
_HOST = "EMAIL_SMTP_HOST"
_PORT = "EMAIL_SMTP_PORT"
_USER = "EMAIL_SMTP_USER"
_PASSWORD = "EMAIL_SMTP_PASSWORD"        # noqa: S105 — env var NAME, not a secret value
_FROM = "EMAIL_FROM"
_STARTTLS = "EMAIL_SMTP_STARTTLS"
_TIMEOUT = "EMAIL_SMTP_TIMEOUT_SECONDS"


class EmailError(RuntimeError):
    """Permanent email failure (bad config, auth rejected, permanent SMTP 5xx). Terminal."""


class TransientEmailError(EmailError):
    """Transient email failure (connect/timeout/4xx/network). Retry-eligible — the dispatch layer keeps
    the notification ``pending`` and records the attempt with ``retry_recommended``."""


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    from_addr: str


class EmailTransport:
    """Transport interface. ``send`` returns a provider message reference or raises
    :class:`TransientEmailError` (retryable) / :class:`EmailError` (permanent). Never logs content."""

    def send(self, message: EmailMessage) -> str:      # pragma: no cover - interface
        raise NotImplementedError


class SmtpEmailTransport(EmailTransport):
    """STARTTLS SMTP transport (works with M365/Exchange Online and any standard relay)."""

    def __init__(self, *, host: str, port: int, username: str | None, password: str | None,
                 starttls: bool = True, timeout: float = 30.0) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._starttls = starttls
        self._timeout = timeout

    def send(self, message: EmailMessage) -> str:
        mime = _MimeMessage()
        mime["From"] = message.from_addr
        mime["To"] = message.to
        mime["Subject"] = message.subject
        ref = f"<{uuid.uuid4()}@client360>"
        mime["Message-ID"] = ref
        mime.set_content(message.text)                 # plain text only — no HTML, no content in headers
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                if self._starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if self._username:
                    smtp.login(self._username, self._password or "")
                smtp.send_message(mime)
            return ref
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, smtplib.SMTPHeloError,
                TimeoutError, ConnectionError, OSError) as exc:
            raise TransientEmailError(f"smtp transient failure: {type(exc).__name__}") from exc
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused,
                smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError, smtplib.SMTPException) as exc:
            raise EmailError(f"smtp permanent failure: {type(exc).__name__}") from exc


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def email_from_address() -> str | None:
    return (os.getenv(_FROM) or "").strip() or None


# --- public portal base URL (fail-closed for deep links) ---------------------

_LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "0.0.0.0", "::1"})


def _is_production() -> bool:
    return (os.getenv("CLIENT360_ENVIRONMENT", "development") or "").strip().lower() == "production"


def public_portal_url() -> str:
    """The configured absolute base URL for portal deep links (no trailing slash)."""
    return (os.getenv("PUBLIC_PORTAL_URL") or "").strip().rstrip("/")


def public_portal_url_ok() -> bool:
    """True only for an explicitly-configured, valid absolute portal URL. In PRODUCTION this REQUIRES
    ``https://`` to a non-local host — production email can never send links pointing at localhost / a
    dev host / a missing base. In non-production a valid http(s) absolute URL is accepted (local dev)."""
    from urllib.parse import urlparse

    url = public_portal_url()
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        return False
    if _is_production():
        return parsed.scheme == "https" and host not in _LOCAL_HOSTS and not host.endswith(".local")
    return parsed.scheme in {"http", "https"}


def email_delivery_ready() -> bool:
    """Fully fail-closed readiness for outbound email: a configured transport AND a valid public portal
    URL. Every send path (direct + dispatched) is gated on this, so email is never sent when unconfigured
    or when it would carry a localhost/dev/relative link."""
    return build_email_transport() is not None and public_portal_url_ok()


def build_email_transport() -> EmailTransport | None:
    """Return a configured transport, or None when email is not configured (fail-closed).

    Configured == an SMTP host AND a From address are set. Credentials are optional (some relays allow
    unauthenticated internal submission). Reads env only; never logs values.
    """
    host = (os.getenv(_HOST) or "").strip()
    if not host or not email_from_address():
        return None
    port = 587
    try:
        port = int(os.getenv(_PORT, "587"))
    except (TypeError, ValueError):
        port = 587
    timeout = 30.0
    try:
        timeout = float(os.getenv(_TIMEOUT, "30"))
    except (TypeError, ValueError):
        timeout = 30.0
    starttls = _truthy(os.getenv(_STARTTLS, "true")) or os.getenv(_STARTTLS) is None
    return SmtpEmailTransport(
        host=host, port=port,
        username=(os.getenv(_USER) or "").strip() or None,
        password=os.getenv(_PASSWORD) or None,       # value read from env only; never logged/committed
        starttls=starttls, timeout=timeout)


class EmailNotificationProvider:
    """The ``email`` delegate for :class:`app.services.notification_providers.ChannelProvider`.

    Contract (matches ``app.portal.providers.NotificationProvider``): ``deliver(recipient, title, body,
    metadata) -> dict`` with ``delivered: bool`` and, on failure, a ``reason`` in
    {``provider_not_configured``, ``provider_unavailable``, ``provider_error``}. Never logs title/body.
    """

    channel = "email"

    def __init__(self, transport: EmailTransport | None, *, from_addr: str | None = None) -> None:
        self._transport = transport
        self._from = from_addr or email_from_address()

    def deliver(self, *, recipient, title, body=None, metadata=None) -> dict:   # noqa: ARG002
        if self._transport is None or not self._from:
            # Fail-closed: unconfigured email is never reported as delivered.
            return {"delivered": False, "channel": self.channel, "reason": "provider_not_configured"}
        if not recipient:
            return {"delivered": False, "channel": self.channel, "reason": "provider_error"}
        message = EmailMessage(to=str(recipient), subject=str(title or ""),
                               text=str(body or ""), from_addr=self._from)
        try:
            ref = self._transport.send(message)
        except TransientEmailError:
            return {"delivered": False, "channel": self.channel, "reason": "provider_unavailable"}
        except EmailError:
            return {"delivered": False, "channel": self.channel, "reason": "provider_error"}
        return {"delivered": True, "channel": self.channel, "ref": ref}
