from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class IdentityClaims:
    subject: str
    email: str
    display_name: str
    mfa_authenticated: bool

class IdentityProvider(Protocol):
    def authorization_url(self, *, state: str, redirect_uri: str) -> str: ...
    def exchange_code(self, *, code: str, redirect_uri: str) -> IdentityClaims: ...
