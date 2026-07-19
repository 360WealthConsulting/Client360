"""F5.2 / Epic 5 — Canonical notification channel provider registry tests (ADR-017).

Covers the canonical registry, one-provider-per-channel, duplicate/unknown handling,
enabled in-app + disabled email/SMS/push honest outcomes, exception normalization,
no external attempt when disabled, no credentials/config, no content leakage, and total
compatibility with the preserved portal providers (which are wrapped, not replaced).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.notification_providers import (
    DELIVERED,
    DELIVERY_OUTCOMES,
    DISABLED,
    FAILED,
    FAILURE_ERROR,
    FAILURE_NOT_CONFIGURED,
    REQUIRED_CHANNELS,
    ChannelProvider,
    DeliveryResult,
    NotificationProviderRegistry,
    build_default_registry,
    default_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Spy:
    """A delegate that records calls (to prove disabled providers never attempt delivery)."""
    def __init__(self, result=None, raises=False):
        self.calls = 0
        self._result = result or {"delivered": True, "channel": "spy"}
        self._raises = raises

    def deliver(self, **kwargs):
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._result


# --- registry construction / membership --------------------------------------

def test_canonical_registry_construction_one_per_channel():
    reg = build_default_registry()
    assert isinstance(reg, NotificationProviderRegistry)
    assert reg.channels() == set(REQUIRED_CHANNELS) == {"in_app", "email", "sms", "push"}
    assert len(reg) == 4
    # provider-state metadata: in_app enabled/ready; others disabled/not-ready
    states = reg.states()
    assert states["in_app"] == {"enabled": True, "ready": True, "identifier": "in_app"}
    for ch in ("email", "sms", "push"):
        assert states[ch] == {"enabled": False, "ready": False, "identifier": ch}


def test_duplicate_registration_rejected():
    reg = NotificationProviderRegistry()
    reg.register(ChannelProvider(identifier="in_app", channel="in_app", enabled=True, delegate=_Spy()))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(ChannelProvider(identifier="in_app", channel="in_app", enabled=True, delegate=_Spy()))


def test_unknown_channel_handling():
    reg = build_default_registry()
    assert "telepathy" not in reg
    with pytest.raises(ValueError, match="Unsupported notification channel"):
        reg.get("telepathy")


# --- enabled in-app / disabled channels + honest outcomes --------------------

def test_in_app_enabled_and_delivers():
    p = build_default_registry().get("in_app")
    assert p.is_ready() is True and p.enabled is True
    r = p.deliver_result(recipient="user:1", title="t", body="b")
    assert r.outcome == DELIVERED and r.delivered is True and r.channel == "in_app"
    assert r.provider_ref == "in_app"


@pytest.mark.parametrize("channel", ["email", "sms", "push"])
def test_disabled_channels_report_disabled_without_attempt(channel):
    reg = build_default_registry()
    p = reg.get(channel)
    assert p.is_ready() is False and p.enabled is False
    r = p.deliver_result(recipient="user:1", title="t", body="b")
    assert r.outcome == DISABLED and r.delivered is False
    assert r.failure_class == FAILURE_NOT_CONFIGURED


def test_disabled_provider_makes_no_external_attempt():
    spy = _Spy()
    p = ChannelProvider(identifier="email", channel="email", enabled=False, delegate=spy)
    p.deliver_result(recipient="x", title="t", body="b")
    assert spy.calls == 0  # disabled -> never calls the underlying delegate


def test_honest_outcomes_are_explicit():
    assert DELIVERY_OUTCOMES == {DELIVERED, DISABLED, FAILED}
    # a disabled provider never claims delivery
    r = build_default_registry().get("sms").deliver_result(recipient="x", title="t", body="b")
    assert r.delivered is False and r.outcome != DELIVERED


# --- exception normalization -------------------------------------------------

def test_provider_exception_normalized_to_failed():
    p = ChannelProvider(identifier="email", channel="email", enabled=True, delegate=_Spy(raises=True))
    r = p.deliver_result(recipient="x", title="t", body="b")
    assert r.outcome == FAILED and r.delivered is False and r.failure_class == FAILURE_ERROR


# --- no content leakage / no credentials -------------------------------------

def test_no_notification_content_in_result():
    p = ChannelProvider(identifier="email", channel="email", enabled=False, delegate=_Spy())
    r = p.deliver_result(recipient="x", title="SECRET-TITLE", body="SECRET-BODY")
    blob = str(r.to_dict())
    assert "SECRET-TITLE" not in blob and "SECRET-BODY" not in blob


def test_no_credentials_or_config_or_dispatch_in_source():
    source = (REPO_ROOT / "app" / "services" / "notification_providers.py").read_text()
    for forbidden in ("os.environ", "getenv", "api_key", "password", "secret_key", "token=",
                      "requests.", "httpx", "urllib", "smtplib", "APIRouter", "subscribe(",
                      "dispatch_pending", "record_evidence", "write_audit_event"):
        assert forbidden not in source
    assert (REPO_ROOT / "docs" / "NOTIFICATION_PROVIDERS.md").is_file()


# --- compatibility: portal providers wrapped, not replaced -------------------

def test_portal_providers_unchanged_and_wrapped():
    from app.portal.providers import NOTIFICATION_PROVIDERS
    # legacy behavior byte-for-byte (still asserted by test_provider_abstraction)
    assert NOTIFICATION_PROVIDERS["in_app"].deliver(recipient="x", title="t", body="b", metadata={}) == \
        {"delivered": True, "channel": "in_app"}
    # the canonical registry wraps the SAME underlying portal provider instances
    reg = default_registry()
    for ch in ("in_app", "email", "sms", "push"):
        assert reg.get(ch)._delegate is NOTIFICATION_PROVIDERS[ch]


def test_result_type_is_structured():
    r = build_default_registry().get("in_app").deliver_result(recipient="x", title="t", body="b")
    assert isinstance(r, DeliveryResult)
    assert set(r.to_dict()) == {"outcome", "channel", "delivered", "provider_ref", "failure_class", "description"}
