"""Authentication Foundation (E2.1 / Backlog F2.1).

A thin, provider-agnostic authentication layer that establishes the canonical
identity abstraction future Identity & Security features build on. It **wraps and
reuses** the existing session authentication (``app.security.service`` /
``Principal`` / ``AuthenticationMiddleware``) — it does not reimplement or replace
it, and it makes **no authorization decisions** (RBAC/capabilities and
object/field permissions are out of scope for F2.1).

Design (ADR-013 reconciliation):
  * ``AuthenticatedIdentity`` — a provider-agnostic view of *who* the principal is
    (user_id, email, display_name, provider). Deliberately carries **no
    capabilities** so authentication stays separate from authorization.
  * ``AuthenticationContext`` — per-request authentication state.
  * ``AuthenticationProvider`` — a Protocol for pluggable providers (session today;
    future Microsoft identity / Active Directory / SSO / MFA plug in here).
  * ``SessionAuthenticationService`` — the default provider; delegates session
    validation to ``resolve_principal`` (no duplication).
  * ``current_context(request)`` — projects the middleware-populated
    ``request.state.principal`` into a stable context (read-only; no middleware
    change).
  * Authentication events via the F1.3 outbox + F1.4 envelope (extension point).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from app.security.models import Principal
from app.security.service import resolve_principal

DEFAULT_PROVIDER = "session"

# Canonical authentication event types (emitted via the outbox/envelope).
AUTH_AUTHENTICATED = "identity.authenticated"
AUTH_FAILED = "identity.authentication_failed"
AUTH_SESSION_REVOKED = "identity.session_revoked"


class AuthenticationError(Exception):
    """Base error for the authentication foundation."""


class UnknownProviderError(AuthenticationError, KeyError):
    """No authentication provider registered under the given name."""


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Provider-agnostic identity of an authenticated principal (no authorization)."""

    user_id: int
    email: str
    display_name: str
    provider: str = DEFAULT_PROVIDER
    authenticated_at: str | None = None

    @classmethod
    def from_principal(
        cls, principal: Principal, *, provider: str = DEFAULT_PROVIDER,
        authenticated_at: str | None = None,
    ) -> AuthenticatedIdentity:
        # Projects the existing Principal to an identity — capabilities are
        # intentionally NOT carried (authentication, not authorization).
        return cls(
            user_id=principal.user_id,
            email=principal.email,
            display_name=principal.display_name,
            provider=provider,
            authenticated_at=authenticated_at,
        )

    @property
    def subject_ref(self) -> str:
        """A reference for events/logs that never leaks PII (e.g. email)."""
        return f"user:{self.user_id}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AuthenticatedIdentity:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class AuthenticationContext:
    """Per-request authentication state (who, via which provider, is a session present)."""

    identity: AuthenticatedIdentity | None = None
    provider: str = DEFAULT_PROVIDER
    session_present: bool = False
    request_id: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.identity is not None

    @classmethod
    def anonymous(
        cls, *, provider: str = DEFAULT_PROVIDER, session_present: bool = False,
        request_id: str | None = None,
    ) -> AuthenticationContext:
        return cls(identity=None, provider=provider, session_present=session_present, request_id=request_id)

    @classmethod
    def for_identity(
        cls, identity: AuthenticatedIdentity, *, request_id: str | None = None
    ) -> AuthenticationContext:
        return cls(identity=identity, provider=identity.provider, session_present=True, request_id=request_id)

    def to_dict(self) -> dict:
        return {
            "authenticated": self.is_authenticated,
            "provider": self.provider,
            "session_present": self.session_present,
            "request_id": self.request_id,
            "identity": self.identity.to_dict() if self.identity else None,
        }


@runtime_checkable
class AuthenticationProvider(Protocol):
    """Provider-agnostic contract. Future providers (Microsoft / AD / SSO / MFA)
    implement this and register via ``register_provider``."""

    provider_name: str

    def resolve_identity(self, token: str) -> AuthenticatedIdentity | None: ...


class SessionAuthenticationService:
    """Default provider: session-based authentication, reusing the existing
    ``resolve_principal`` session validation (no reimplementation)."""

    provider_name = DEFAULT_PROVIDER

    def resolve_identity(self, token: str) -> AuthenticatedIdentity | None:
        principal = resolve_principal(token)
        if principal is None:
            return None
        return AuthenticatedIdentity.from_principal(
            principal, provider=self.provider_name, authenticated_at=datetime.now(UTC).isoformat()
        )

    def validate_session(self, token: str) -> bool:
        return resolve_principal(token) is not None


# --- provider registry (extension point) -------------------------------------

_providers: dict[str, AuthenticationProvider] = {}


def register_provider(provider: AuthenticationProvider) -> None:
    _providers[provider.provider_name] = provider


def get_provider(name: str = DEFAULT_PROVIDER) -> AuthenticationProvider:
    try:
        return _providers[name]
    except KeyError as exc:
        raise UnknownProviderError(name) from exc


def list_providers() -> list[str]:
    return sorted(_providers)


register_provider(SessionAuthenticationService())


# --- authentication lifecycle API --------------------------------------------

def resolve_identity(token: str, *, provider: str = DEFAULT_PROVIDER) -> AuthenticatedIdentity | None:
    """Identity resolution / session validation via the named provider."""
    return get_provider(provider).resolve_identity(token)


def authenticate_token(
    token: str | None, *, provider: str = DEFAULT_PROVIDER, request_id: str | None = None
) -> AuthenticationContext:
    """Resolve a credential/token into an AuthenticationContext (lifecycle entry)."""
    identity = resolve_identity(token, provider=provider) if token else None
    if identity is None:
        return AuthenticationContext.anonymous(
            provider=provider, session_present=bool(token), request_id=request_id
        )
    return AuthenticationContext.for_identity(identity, request_id=request_id)


def current_context(request) -> AuthenticationContext:
    """Project the middleware-populated ``request.state`` into a stable context.

    Reads what ``AuthenticationMiddleware`` already set (``request.state.principal``);
    performs no authorization and does not alter middleware behavior. Future
    features should call this instead of reaching into request internals.
    """
    state = getattr(request, "state", None)
    request_id = getattr(state, "request_id", None)
    principal = getattr(state, "principal", None)
    try:
        session_present = bool(getattr(request, "session", {}).get("session_token"))
    except Exception:
        session_present = False
    if principal is None:
        return AuthenticationContext.anonymous(request_id=request_id, session_present=session_present)
    identity = AuthenticatedIdentity.from_principal(principal)
    return AuthenticationContext.for_identity(identity, request_id=request_id)


# --- authentication events (F1.3 outbox + F1.4 envelope) ---------------------

def emit_authentication_event(
    conn, event_type: str, *, identity: AuthenticatedIdentity | None = None,
    subject_ref: str | None = None, metadata: dict | None = None, correlation_id: str | None = None,
) -> str:
    """Publish an authentication event via the transactional outbox.

    Reference-only payload (user_id + provider) — never PII such as email
    (Constitution §9). Written in the caller's transaction (atomic).
    """
    from app.platform import new_event, publish_event  # lazy: avoid import cycles

    payload: dict = {}
    if identity is not None:
        payload["user_id"] = identity.user_id
        payload["provider"] = identity.provider
        subject_ref = subject_ref or identity.subject_ref
    envelope = new_event(
        event_type, payload, producer="security.authentication",
        subject_ref=subject_ref, metadata=metadata or {}, correlation_id=correlation_id,
    )
    return publish_event(conn, envelope)
