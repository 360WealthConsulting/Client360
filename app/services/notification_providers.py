"""Canonical notification channel provider registry (F5.2 / Epic 5, ADR-017).

The platform-level provider **contract** and **registry**. It reconciles the existing
portal notification providers (``app.portal.providers.NOTIFICATION_PROVIDERS``) by
**wrapping** them as the single underlying implementation — preserving the enabled
in-app provider and the disabled email/SMS/push hooks and their honest outcomes
**exactly** — and exposes them through one canonical platform registry with a
structured, deterministic delivery result suitable for later F5.5 dispatch.

Bounded hybrid (ADR-013/ADR-017 Option B): the portal providers are **not** replaced or
abandoned; this is the single canonical *platform* registration source. Compatibility is
total — ``app/portal/providers.py`` is unchanged, so portal, benefits, and exception-SLA
imports and behavior are preserved.

F5.2 establishes **contracts and honest outcomes only**. It performs **no** async
dispatch, event consumption, recipient-preference enforcement, retry orchestration,
audit/evidence emission, or API exposure; it enables **no** external provider, adds **no**
credentials/config, and never logs a notification's title or body.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- honest delivery outcomes (ADR-017 §10) ----------------------------------

DELIVERED = "delivered"
DISABLED = "disabled"
FAILED = "failed"
#: The complete set of honest outcomes a provider may report.
DELIVERY_OUTCOMES: frozenset[str] = frozenset({DELIVERED, DISABLED, FAILED})

# Failure classifications (human-safe; never contain notification content).
FAILURE_NOT_CONFIGURED = "provider_not_configured"
FAILURE_UNAVAILABLE = "provider_unavailable"
FAILURE_ERROR = "provider_error"

#: The canonical channels and their required initial state (in_app enabled; rest disabled).
REQUIRED_CHANNELS: tuple[str, ...] = ("in_app", "email", "sms", "push")


@dataclass(frozen=True)
class DeliveryResult:
    """Structured, deterministic result of a delivery attempt.

    Carries **no** notification content — ``description`` is a human-safe summary and never
    includes the title/body. ``outcome`` is one of :data:`DELIVERY_OUTCOMES`.
    """

    outcome: str
    channel: str
    delivered: bool
    provider_ref: str | None = None      # external provider reference when available
    failure_class: str | None = None     # provider_not_configured | provider_unavailable | provider_error
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome, "channel": self.channel, "delivered": self.delivered,
            "provider_ref": self.provider_ref, "failure_class": self.failure_class,
            "description": self.description,
        }


def _classify(channel: str, raw: dict) -> DeliveryResult:
    """Map an underlying provider's honest dict outcome into a structured result."""
    if bool(raw.get("delivered")):
        return DeliveryResult(outcome=DELIVERED, channel=channel, delivered=True,
                              provider_ref=raw.get("channel") or channel,
                              description=f"delivered via {channel}")
    reason = raw.get("reason") or FAILURE_NOT_CONFIGURED
    outcome = DISABLED if reason == FAILURE_NOT_CONFIGURED else FAILED
    return DeliveryResult(outcome=outcome, channel=channel, delivered=False,
                          failure_class=reason, description=f"{channel} not delivered ({reason})")


class ChannelProvider:
    """Canonical platform channel provider — an adapter over an underlying delivery hook.

    Exposes provider metadata (``identifier``, ``channel``, ``enabled``, :meth:`is_ready`)
    and a deterministic, exception-normalized :meth:`deliver_result`. A disabled provider
    returns an explicit ``disabled`` outcome **without** attempting delivery.
    """

    def __init__(self, *, identifier: str, channel: str, enabled: bool, delegate) -> None:
        self.identifier = identifier
        self.channel = channel
        self.enabled = enabled
        self._delegate = delegate  # underlying hook: .deliver(recipient,title,body,metadata)->dict

    def is_ready(self) -> bool:
        """Whether the provider can attempt delivery (disabled providers are never ready)."""
        return bool(self.enabled)

    def deliver_result(self, *, recipient, title, body=None, metadata=None) -> DeliveryResult:
        """Deterministic structured delivery. Disabled → explicit ``disabled`` (no attempt);
        an underlying exception is normalized to ``failed``. Never logs title/body."""
        if not self.is_ready():
            return DeliveryResult(outcome=DISABLED, channel=self.channel, delivered=False,
                                  failure_class=FAILURE_NOT_CONFIGURED,
                                  description=f"{self.channel} provider not configured")
        try:
            raw = self._delegate.deliver(recipient=recipient, title=title, body=body, metadata=metadata or {})
        except Exception:
            return DeliveryResult(outcome=FAILED, channel=self.channel, delivered=False,
                                  failure_class=FAILURE_ERROR,
                                  description=f"{self.channel} provider raised an error")
        return _classify(self.channel, raw)


class NotificationProviderRegistry:
    """Canonical registry of channel providers (one provider per channel)."""

    def __init__(self) -> None:
        self._providers: dict[str, ChannelProvider] = {}

    def register(self, provider: ChannelProvider) -> None:
        if provider.channel in self._providers:
            raise ValueError(f"Notification channel '{provider.channel}' is already registered")
        self._providers[provider.channel] = provider

    def get(self, channel: str) -> ChannelProvider:
        if channel not in self._providers:
            raise ValueError(f"Unsupported notification channel '{channel}'")
        return self._providers[channel]

    def channels(self) -> frozenset[str]:
        return frozenset(self._providers)

    def states(self) -> dict:
        """Provider-state metadata per channel (enabled/ready/identifier) — no content."""
        return {ch: {"enabled": p.enabled, "ready": p.is_ready(), "identifier": p.identifier}
                for ch, p in self._providers.items()}

    def __contains__(self, channel: object) -> bool:
        return channel in self._providers

    def __len__(self) -> int:
        return len(self._providers)


def build_default_registry() -> NotificationProviderRegistry:
    """The single canonical platform registry, reconciling (wrapping) the portal providers.

    Wraps ``app.portal.providers.NOTIFICATION_PROVIDERS`` as the underlying implementation:
    ``in_app`` enabled, ``email``/``sms``/``push`` disabled (initial state). No provider is
    enabled or configured here; disabled channels remain disabled.
    """
    from app.portal.providers import NOTIFICATION_PROVIDERS as portal_providers

    registry = NotificationProviderRegistry()
    for channel in REQUIRED_CHANNELS:
        registry.register(ChannelProvider(
            identifier=channel, channel=channel,
            enabled=(channel == "in_app"),
            delegate=portal_providers.get(channel),
        ))
    return registry


def default_registry() -> NotificationProviderRegistry:
    """Return the canonical registry (built fresh; F5.2 keeps no shared mutable state)."""
    return build_default_registry()
