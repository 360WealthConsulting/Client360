from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class PortalIdentityResult:
    subject: str
    mfa_verified: bool
    email: Optional[str] = None

class PortalIdentityProvider(ABC):
    """A portal identity provider.

    ``verify_activation`` is the original one-shot contract: the caller hands over a single opaque
    assertion string and gets an identity back. It suits the deterministic LOCAL/test provider, which has
    no browser leg.

    A real OIDC provider cannot be expressed that way — an authorization-code flow needs a redirect, a
    server-held ``state``/``nonce``, and a code exchange bound to that browser session. Providers that
    support it set ``supports_redirect_flow = True`` and implement
    :meth:`authorization_url` / :meth:`exchange_code`; the portal auth routes use those instead of
    ``verify_activation``. Keeping both means the synthetic provider and its production guards are
    unchanged."""

    key: str
    #: True only for providers implementing the browser authorization-code flow below.
    supports_redirect_flow: bool = False
    #: True for providers acceptable as the PRODUCTION external IdP. The local/test provider is not.
    production_capable: bool = False

    @abstractmethod
    def verify_activation(self, assertion: str) -> PortalIdentityResult: ...

    def authorization_url(self, *, state: str, nonce: str, redirect_uri: str,
                          code_challenge: str) -> str:
        """The IdP URL to redirect the browser to. Redirect-flow providers override this."""
        raise NotImplementedError(f"{self.key} does not support the browser authorization-code flow")

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str,
                      expected_nonce: str) -> PortalIdentityResult:
        """Exchange the authorization code and return a verified identity. Raises ``ValueError`` on any
        validation failure — never a provider-specific exception, and never containing token material."""
        raise NotImplementedError(f"{self.key} does not support the browser authorization-code flow")

class ProviderRegistry:
    """Canonical registry for pluggable portal service providers.

    Providers are keyed by their ``.key`` attribute. ``label`` is used only to
    render a clear error when an unknown key is requested, so each registry can
    name its own domain (identity, signature, ...). Release 0.9.9 Phase 3
    consolidated the former per-domain registry classes onto this one type.
    """
    def __init__(self, label="Provider"):
        self._providers = {}
        self._label = label
    def register(self, provider): self._providers[provider.key] = provider
    def get(self, key):
        if key not in self._providers: raise ValueError(f"{self._label} '{key}' is not configured")
        return self._providers[key]

    def keys(self):
        """Registered provider keys. Read-only view for diagnostics and invariants."""
        return tuple(sorted(self._providers))

    def production_capable(self):
        """Registered providers acceptable as the PRODUCTION external IdP.

        The deterministic local/test provider is deliberately excluded, so production readiness can
        never be satisfied by the synthetic provider."""
        return tuple(sorted(k for k, p in self._providers.items()
                            if getattr(p, "production_capable", False)))

# Backwards-compatible alias for the former per-domain registry class.
PortalIdentityProviderRegistry = ProviderRegistry

PORTAL_IDENTITY_PROVIDERS = ProviderRegistry("Portal identity provider")

@dataclass(frozen=True)
class SignatureResult:
    external_id: str
    status: str
    metadata: dict

class SignatureProvider(ABC):
    key: str
    @abstractmethod
    def create_request(self, *, recipients, documents, callback_url, metadata) -> SignatureResult: ...
    @abstractmethod
    def get_status(self, external_id: str) -> SignatureResult: ...
    @abstractmethod
    def cancel(self, external_id: str) -> SignatureResult: ...

class NotificationProvider(ABC):
    channel: str
    @abstractmethod
    def deliver(self, *, recipient, title, body, metadata) -> dict: ...

class InAppNotificationProvider(NotificationProvider):
    channel = "in_app"
    def deliver(self, *, recipient, title, body, metadata):
        return {"delivered": True, "channel": self.channel}

class DisabledNotificationHook(NotificationProvider):
    def __init__(self, channel): self.channel = channel
    def deliver(self, **kwargs):
        return {"delivered": False, "channel": self.channel, "reason": "provider_not_configured"}

NOTIFICATION_PROVIDERS = {
    "in_app": InAppNotificationProvider(),
    "email": DisabledNotificationHook("email"),
    "sms": DisabledNotificationHook("sms"),
    "push": DisabledNotificationHook("push"),
}
