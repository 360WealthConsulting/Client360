from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class PortalIdentityResult:
    subject: str
    mfa_verified: bool
    email: Optional[str] = None

class PortalIdentityProvider(ABC):
    key: str
    @abstractmethod
    def verify_activation(self, assertion: str) -> PortalIdentityResult: ...

class PortalIdentityProviderRegistry:
    def __init__(self): self._providers = {}
    def register(self, provider): self._providers[provider.key] = provider
    def get(self, key):
        if key not in self._providers: raise ValueError(f"Portal identity provider '{key}' is not configured")
        return self._providers[key]

PORTAL_IDENTITY_PROVIDERS = PortalIdentityProviderRegistry()

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
