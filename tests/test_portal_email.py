"""Outbound email transport (TaxDome cutover P0-1) — workflow + safety proofs.

Every test injects a FAKE transport (no live email service). Proves: each workflow produces the correct
non-sensitive email + portal deep link; invitation/reset send DIRECTLY and never persist their single-use
token (not to the ledger, not to logs/audit); a secure-message email never carries the conversation body;
provider failure is recorded; the email retry sweep redelivers transient failures idempotently; unconfigured
email or an invalid public URL is never reported as delivered; the in-app path is preserved.
"""
from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import Text, func, select

from app.db import audit_events, engine, metadata, portal_notifications
from app.services import notification_dispatch as dispatch
from app.services import notification_email as ne
from app.services import notifications as ledger
from app.services import portal_email
from tests._portal_util import seed_portal_account, seed_staff_user

_notifications = metadata.tables["notifications"]


class _FakeTransport(ne.EmailTransport):
    def __init__(self, *, fail=None):
        self.sent: list[ne.EmailMessage] = []
        self._fail = fail                    # None | 'transient' | 'permanent'

    def send(self, message):
        if self._fail == "transient":
            raise ne.TransientEmailError("simulated transient")
        if self._fail == "permanent":
            raise ne.EmailError("simulated permanent")
        self.sent.append(message)
        return f"<ref-{uuid.uuid4()}@client360>"


@pytest.fixture
def email_on(monkeypatch):
    """Configure a recording transport + a valid HTTPS public portal URL."""
    monkeypatch.setenv("EMAIL_FROM", "Client360 <noreply@360.test>")
    monkeypatch.setenv("PUBLIC_PORTAL_URL", "https://portal.360.test")
    transport = _FakeTransport()
    monkeypatch.setattr(ne, "build_email_transport", lambda: transport)
    return transport


def _attempts(notification_uid):
    rec = ledger.get_notification(notification_uid=notification_uid)
    return dispatch.delivery_attempts(rec.id)


def _token_rows(token):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(_notifications).where(
            _notifications.c.body.ilike(f"%{token}%") | _notifications.c.dedupe_key.ilike(f"%{token}%")))


# --- 1. invitation: direct send, token NEVER persisted -----------------------

def test_invitation_email_sends_and_never_persists_token(email_on):
    token = f"TKN-INV-{uuid.uuid4().hex}"
    r = portal_email.send_invitation_email(email="alice@client.test", display_name="Alice", token=token)
    assert r.outcome == "delivered" and r.delivered is True
    msg = email_on.sent[-1]
    assert msg.to == "alice@client.test" and "invited" in msg.subject.lower()
    assert f"https://portal.360.test/portal/activate?token={token}" in msg.text
    # SECURITY: the single-use token is in the email link only — NOT in the long-lived ledger.
    assert _token_rows(token) == 0


# --- 2. reset: direct send, token never in logs / audit / ledger -------------

def test_reset_email_does_not_persist_or_leak_token(email_on, caplog):
    token = f"TKN-RESET-{uuid.uuid4().hex}"
    with caplog.at_level(logging.DEBUG):
        r = portal_email.send_password_reset_email(email="bob@client.test", token=token)
    assert r.outcome == "delivered"
    assert token not in caplog.text                                   # not logged
    assert _token_rows(token) == 0                                    # not persisted in the ledger
    with engine.connect() as c:
        in_audit = c.scalar(select(func.count()).select_from(audit_events)
                            .where(audit_events.c.metadata.cast(Text).ilike(f"%{token}%")))
    assert in_audit == 0                                             # not in audit metadata
    assert f"token={token}" in email_on.sent[-1].text                # carried only in the email link


# --- 3. secure message: alert only, NO conversation content ------------------

def test_secure_message_email_has_no_message_content(email_on):
    r = portal_email.send_secure_message_email(email="carol@client.test", message_id=uuid.uuid4().int % 1_000_000)
    assert r.outcome == "delivered"
    text = email_on.sent[-1].text
    assert "new secure message" in text.lower()
    assert "https://portal.360.test/portal/messages" in text
    assert "not included" in text.lower() and "attachment" not in text.lower()


# --- 4. invoice: non-sensitive summary + link --------------------------------

def test_invoice_email_contains_nonsensitive_summary_and_link(email_on):
    iid = uuid.uuid4().int % 1_000_000
    r = portal_email.send_invoice_email(email="dan@client.test", invoice_id=iid,
                                        invoice_number="INV-20260101-ABC123", amount_label="$1,250.00")
    assert r.outcome == "delivered"
    text = email_on.sent[-1].text
    assert "INV-20260101-ABC123" in text and "$1,250.00" in text
    assert f"https://portal.360.test/portal/billing/invoices/{iid}" in text
    assert "line item" not in text.lower()


# --- 5. provider failure recorded as failure ---------------------------------

def test_transient_failure_is_retry_eligible_and_stays_pending(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "Client360 <noreply@360.test>")
    monkeypatch.setenv("PUBLIC_PORTAL_URL", "https://portal.360.test")
    monkeypatch.setattr(ne, "build_email_transport", lambda: _FakeTransport(fail="transient"))
    r = portal_email.send_secure_message_email(email="e@client.test", message_id=uuid.uuid4().int % 1_000_000)
    assert r.outcome == "provider_unavailable" and r.retry_recommended is True
    assert r.ledger_status == "pending"                              # transient → stays pending
    assert _attempts(r.notification_uid)[-1]["execution_result"] == "provider_unavailable"


def test_permanent_failure_direct_send_is_recorded_failed(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "Client360 <noreply@360.test>")
    monkeypatch.setenv("PUBLIC_PORTAL_URL", "https://portal.360.test")
    monkeypatch.setattr(ne, "build_email_transport", lambda: _FakeTransport(fail="permanent"))
    r = portal_email.send_invitation_email(email="f@client.test", display_name="F", token="T")
    assert r.outcome == "failed" and r.delivered is False and r.failure_class == "provider_error"


# --- 6. email retry sweep redelivers transient failures IDEMPOTENTLY ---------

def test_email_retry_sweep_redelivers_without_duplicates(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "Client360 <noreply@360.test>")
    monkeypatch.setenv("PUBLIC_PORTAL_URL", "https://portal.360.test")
    monkeypatch.setattr(ne, "build_email_transport", lambda: _FakeTransport(fail="transient"))
    r = portal_email.send_secure_message_email(email="retry@client.test",
                                               message_id=uuid.uuid4().int % 1_000_000)
    assert r.ledger_status == "pending"                              # left pending by transient failure
    ok = _FakeTransport()
    monkeypatch.setattr(ne, "build_email_transport", lambda: ok)     # transport recovers
    dispatch.dispatch_pending_notifications(channel="email")         # the retry sweep delivers it
    dispatch.dispatch_pending_notifications(channel="email")         # second pass: nothing left pending
    assert ledger.get_notification(notification_uid=r.notification_uid).status == "delivered"
    assert sum(1 for m in ok.sent if m.to == "retry@client.test") == 1   # exactly one email, no duplicate


# --- 7. unconfigured email never reports success -----------------------------

def test_unconfigured_email_is_not_delivered(monkeypatch):
    monkeypatch.delenv("EMAIL_SMTP_HOST", raising=False)
    monkeypatch.setattr(ne, "build_email_transport", lambda: None)   # explicitly unconfigured
    assert portal_email.email_configured() is False
    r = portal_email.send_invitation_email(email="h@client.test", display_name="H", token="T")
    assert r.delivered is False and r.failure_class == "provider_not_configured"


# --- 8. PUBLIC_PORTAL_URL fail-closed ----------------------------------------

def test_missing_public_url_fails_closed(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "Client360 <noreply@360.test>")
    monkeypatch.setattr(ne, "build_email_transport", lambda: _FakeTransport())
    monkeypatch.delenv("PUBLIC_PORTAL_URL", raising=False)           # transport ok, but no public URL
    assert portal_email.email_configured() is False                 # not deliverable
    r = portal_email.send_invitation_email(email="x@client.test", display_name="X", token="T")
    assert r.delivered is False                                      # never sent a relative/dev link


def test_production_requires_https_non_local_public_url(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "Client360 <noreply@360.test>")
    monkeypatch.setattr(ne, "build_email_transport", lambda: _FakeTransport())
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    for bad in ("http://portal.360.test", "https://localhost", "https://app.local", ""):
        monkeypatch.setenv("PUBLIC_PORTAL_URL", bad)
        assert portal_email.email_configured() is False, bad
    monkeypatch.setenv("PUBLIC_PORTAL_URL", "https://portal.360wealth.test")
    assert portal_email.email_configured() is True                  # valid HTTPS public URL


# --- 9. in-app notifications preserved (email is additive) -------------------

def test_in_app_billing_notification_still_created(email_on):
    from datetime import date

    from app.security.models import Principal
    from app.services.billing import service as b
    account_id, _principal, _pid, hid = seed_portal_account(seed_staff_user())
    staff = Principal(seed_staff_user(), "s@e.test", "S",
                      frozenset({"billing.read", "billing.write", "record.read_all", "record.write_all"}))
    inv = b.create_draft_invoice(staff, bill_to_type="household", bill_to_id=hid)
    b.add_line_item(staff, inv, description="Service", unit_amount_cents=5000)
    b.issue_invoice(staff, inv, due_date=date.today())
    with engine.connect() as c:
        in_app = c.scalar(select(func.count()).select_from(portal_notifications).where(
            portal_notifications.c.portal_account_id == account_id,
            portal_notifications.c.entity_type == "invoice"))
    assert in_app >= 1                                               # in-app preserved
    assert any("billing/invoices" in m.text for m in email_on.sent)  # + out-of-band email
