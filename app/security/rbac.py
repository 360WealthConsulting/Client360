"""Authorization (RBAC) Foundation (E2.2 / Backlog F2.2).

A canonical, provider-agnostic authorization abstraction over the existing
capability-based RBAC (users → roles → capabilities). It **wraps and reuses** the
existing model (``Principal.capabilities`` / ``policy.capability_codes_query`` /
``dependencies.require_capability``) and **preserves all existing enforcement
behavior** — the default policy evaluates exactly the same capability-membership
check the app already uses.

Scope (F2.2): role/capability resolution, permission evaluation, an authorization
context, an authorization result, a policy abstraction, extension points, and
authorization events. **Out of scope** (later features): object ownership /
record-level scope (``has_record_scope`` / ``authorization.py``), field-level
security, row filtering, business approval workflows, and audit policy — those are
untouched.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy import func, or_, select

from app.db import capabilities as _capabilities
from app.db import engine
from app.db import role_capabilities as _role_capabilities
from app.db import roles as _roles
from app.db import user_roles as _user_roles
from app.db import users as _users
from app.security.models import Principal
from app.security.policy import capability_codes_query

DEFAULT_POLICY = "capability"


class AuthorizationError(Exception):
    """Base error for the authorization foundation."""


class AuthorizationDenied(AuthorizationError, PermissionError):
    """Raised by ``require`` when a permission is denied."""


class UnknownPolicyError(AuthorizationError, KeyError):
    """No authorization policy registered under the given name."""


@dataclass(frozen=True)
class Permission:
    """A required permission — a capability code."""

    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise AuthorizationError("permission code must be a non-empty string")

    def to_dict(self) -> dict:
        return {"code": self.code}


def as_permission(permission: Permission | str) -> Permission:
    return permission if isinstance(permission, Permission) else Permission(permission)


@dataclass(frozen=True)
class AuthorizationContext:
    """A principal's authorization view for a decision (capabilities + roles)."""

    user_id: int
    capabilities: frozenset[str] = field(default_factory=frozenset)
    roles: tuple[str, ...] = ()
    provider: str = DEFAULT_POLICY

    @classmethod
    def from_principal(cls, principal: Principal, *, roles: tuple[str, ...] = ()) -> AuthorizationContext:
        return cls(user_id=principal.user_id, capabilities=frozenset(principal.capabilities), roles=tuple(roles))

    def has(self, permission: Permission | str) -> bool:
        return as_permission(permission).code in self.capabilities

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "capabilities": sorted(self.capabilities),
            "roles": list(self.roles),
            "provider": self.provider,
        }


@dataclass(frozen=True)
class AuthorizationResult:
    """The outcome of a permission evaluation."""

    allowed: bool
    permission: str
    user_id: int | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed

    @classmethod
    def granted(cls, permission: str, user_id: int | None = None) -> AuthorizationResult:
        return cls(True, permission, user_id, "capability granted")

    @classmethod
    def denied(cls, permission: str, user_id: int | None = None, reason: str = "capability not granted") -> AuthorizationResult:
        return cls(False, permission, user_id, reason)

    def to_dict(self) -> dict:
        return asdict(self)


@runtime_checkable
class Policy(Protocol):
    """Provider-agnostic policy contract. Future policies (Active Directory /
    Entra ID groups, SAML/OIDC claims, delegated administration) implement this."""

    policy_name: str

    def evaluate(self, context: AuthorizationContext, permission: Permission) -> AuthorizationResult: ...


class CapabilityPolicy:
    """Default policy: permit iff the capability code is in the context.

    Identical semantics to ``Principal.can`` / ``require_capability`` — existing
    enforcement is preserved."""

    policy_name = DEFAULT_POLICY

    def evaluate(self, context: AuthorizationContext, permission: Permission) -> AuthorizationResult:
        if context.has(permission):
            return AuthorizationResult.granted(permission.code, context.user_id)
        return AuthorizationResult.denied(permission.code, context.user_id)


# --- policy registry (extension point) ---------------------------------------

_policies: dict[str, Policy] = {}


def register_policy(policy: Policy) -> None:
    _policies[policy.policy_name] = policy


def get_policy(name: str = DEFAULT_POLICY) -> Policy:
    try:
        return _policies[name]
    except KeyError as exc:
        raise UnknownPolicyError(name) from exc


def list_policies() -> list[str]:
    return sorted(_policies)


register_policy(CapabilityPolicy())


# --- authorization service ---------------------------------------------------

class AuthorizationService:
    """Evaluates permissions against a policy. Default policy preserves the
    existing capability-based enforcement semantics."""

    def __init__(self, policy: Policy | None = None) -> None:
        self._policy = policy or get_policy(DEFAULT_POLICY)

    @property
    def policy_name(self) -> str:
        return self._policy.policy_name

    def authorize(self, context: AuthorizationContext, permission: Permission | str) -> AuthorizationResult:
        return self._policy.evaluate(context, as_permission(permission))

    def is_permitted(self, context: AuthorizationContext, permission: Permission | str) -> bool:
        return bool(self.authorize(context, permission))

    def require(self, context: AuthorizationContext, permission: Permission | str) -> AuthorizationResult:
        result = self.authorize(context, permission)
        if not result.allowed:
            raise AuthorizationDenied(result.permission)
        return result


_default_service: AuthorizationService | None = None


def default_authorization_service() -> AuthorizationService:
    global _default_service
    if _default_service is None:
        _default_service = AuthorizationService()
    return _default_service


# --- role & capability resolution (reuses existing query) --------------------

def resolve_capabilities(user_id: int, *, conn=None) -> frozenset[str]:
    """A user's effective capability codes (reuses policy.capability_codes_query)."""
    query = capability_codes_query(
        user_id, users=_users, user_roles=_user_roles, roles=_roles,
        role_capabilities=_role_capabilities, capabilities=_capabilities,
    )
    if conn is not None:
        return frozenset(conn.execute(query).scalars())
    with engine.connect() as connection:
        return frozenset(connection.execute(query).scalars())


def resolve_roles(user_id: int, *, conn=None) -> tuple[str, ...]:
    """A user's active role codes (effective-date window applied)."""
    today = func.current_date()
    query = (
        select(_roles.c.code)
        .select_from(_user_roles.join(_roles, _roles.c.id == _user_roles.c.role_id))
        .where(
            _user_roles.c.user_id == user_id,
            _roles.c.active.is_(True),
            _user_roles.c.effective_date <= today,
            or_(_user_roles.c.inactive_date.is_(None), _user_roles.c.inactive_date >= today),
        )
    )
    if conn is not None:
        return tuple(sorted(conn.execute(query).scalars()))
    with engine.connect() as connection:
        return tuple(sorted(connection.execute(query).scalars()))


def authorization_context(principal: Principal, *, include_roles: bool = False, conn=None) -> AuthorizationContext:
    """Build an AuthorizationContext from an existing Principal (capabilities are
    already resolved on the Principal). Optionally enrich with role codes."""
    roles = resolve_roles(principal.user_id, conn=conn) if include_roles else ()
    return AuthorizationContext.from_principal(principal, roles=roles)


# --- authorization events (F1.3 outbox + F1.4 envelope) ----------------------

AUTHZ_GRANTED = "authorization.granted"
AUTHZ_DENIED = "authorization.denied"


def emit_authorization_event(conn, result: AuthorizationResult, *, correlation_id: str | None = None, metadata: dict | None = None) -> str:
    """Publish an authorization decision via the transactional outbox.

    Reference-only payload (user_id, permission code, allowed) — never PII.
    Written in the caller's transaction. Extension point: not wired into the
    existing enforcement path (which is unchanged)."""
    from app.platform import new_event, publish_event  # lazy: avoid import cycles

    event_type = AUTHZ_GRANTED if result.allowed else AUTHZ_DENIED
    envelope = new_event(
        event_type,
        {"user_id": result.user_id, "permission": result.permission, "allowed": result.allowed},
        producer="security.authorization",
        subject_ref=f"user:{result.user_id}" if result.user_id is not None else None,
        metadata=metadata or {},
        correlation_id=correlation_id,
    )
    return publish_event(conn, envelope)
