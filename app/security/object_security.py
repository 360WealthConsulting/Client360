"""Object-Level Security Foundation (E2.3 / Backlog F2.3).

A canonical, provider-agnostic abstraction for object-level authorization that
**wraps and reuses** the existing record-scope enforcement and **preserves all
existing behavior**.

How the existing implementation maps to this backlog feature:
  * ``policy.has_record_scope`` — the low-level DB check (bypass capabilities
    ``record.read_all`` / ``record.write_all`` + a ``record_assignments`` row).
  * ``authorization.record_in_scope`` — the canonical per-record access helper
    (delegates to ``has_record_scope``; manages the connection; honors ``write``).
    This is the de-facto object-security service today; F2.3 formalizes it behind
    ``ObjectSecurityService`` without changing it.
  * ``record_assignments`` — the ownership/assignment model (user/team ↔ entity),
    surfaced here as ``resolve_assignments`` / ``resolve_owners``.

Scope (F2.3): object-security service, context, access evaluation, ownership &
assignment resolution, result model, policy abstraction, extension points, and
object-security events. **Out of scope** (later features): field-level security,
tenant isolation, workflow approval rules, business authorization logic, audit
policy, delegated administration. No new enforcement behavior is introduced — the
default policy delegates to ``record_in_scope``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy import select

from app.db import engine, record_assignments
from app.security.authorization import _active, record_in_scope
from app.security.models import Principal

DEFAULT_POLICY = "record-scope"


class ObjectSecurityError(Exception):
    """Base error for the object-security foundation."""


class ObjectAccessDenied(ObjectSecurityError, PermissionError):
    """Raised by ``require`` when object access is denied."""


class UnknownObjectPolicyError(ObjectSecurityError, KeyError):
    """No object-security policy registered under the given name."""


@dataclass(frozen=True)
class ObjectRef:
    """A reference to a secured object (entity type + id)."""

    entity_type: str
    entity_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.entity_type, str) or not self.entity_type.strip():
            raise ObjectSecurityError("entity_type must be a non-empty string")
        if not isinstance(self.entity_id, int):
            raise ObjectSecurityError("entity_id must be an int")

    @property
    def ref(self) -> str:
        return f"{self.entity_type}:{self.entity_id}"

    def to_dict(self) -> dict:
        return {"entity_type": self.entity_type, "entity_id": self.entity_id}


@dataclass(frozen=True)
class ObjectSecurityContext:
    """The subject and (optional) DB connection for an object-access decision."""

    principal: Principal
    connection: object | None = None
    provider: str = DEFAULT_POLICY

    @classmethod
    def for_principal(cls, principal: Principal, *, connection: object | None = None) -> ObjectSecurityContext:
        return cls(principal=principal, connection=connection)

    def to_dict(self) -> dict:
        return {
            "user_id": self.principal.user_id,
            "provider": self.provider,
            "has_connection": self.connection is not None,
        }


@dataclass(frozen=True)
class ObjectAccessResult:
    """The outcome of an object-access evaluation."""

    allowed: bool
    entity_type: str
    entity_id: int
    write: bool
    user_id: int | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed

    @property
    def ref(self) -> str:
        return f"{self.entity_type}:{self.entity_id}"

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "write": self.write,
            "user_id": self.user_id,
            "reason": self.reason,
        }


@runtime_checkable
class ObjectSecurityPolicy(Protocol):
    """Provider-agnostic policy contract. Future policies (tenant isolation,
    Active Directory groups, delegated administration) implement this."""

    policy_name: str

    def evaluate(self, context: ObjectSecurityContext, obj: ObjectRef, write: bool) -> ObjectAccessResult: ...


class RecordScopePolicy:
    """Default policy: delegates to the existing ``record_in_scope`` — enforcement
    is preserved exactly (bypass capabilities + record assignments)."""

    policy_name = DEFAULT_POLICY

    def evaluate(self, context: ObjectSecurityContext, obj: ObjectRef, write: bool) -> ObjectAccessResult:
        allowed = record_in_scope(
            context.principal, obj.entity_type, obj.entity_id,
            write=write, connection=context.connection,
        )
        reason = "record scope granted" if allowed else "record scope denied"
        return ObjectAccessResult(
            allowed, obj.entity_type, obj.entity_id, write, context.principal.user_id, reason
        )


# --- policy registry (extension point) ---------------------------------------

_policies: dict[str, ObjectSecurityPolicy] = {}


def register_object_policy(policy: ObjectSecurityPolicy) -> None:
    _policies[policy.policy_name] = policy


def get_object_policy(name: str = DEFAULT_POLICY) -> ObjectSecurityPolicy:
    try:
        return _policies[name]
    except KeyError as exc:
        raise UnknownObjectPolicyError(name) from exc


def list_object_policies() -> list[str]:
    return sorted(_policies)


register_object_policy(RecordScopePolicy())


# --- object-security service -------------------------------------------------

class ObjectSecurityService:
    """Evaluates object access against a policy. Default policy delegates to the
    existing record-scope enforcement (no behavior change)."""

    def __init__(self, policy: ObjectSecurityPolicy | None = None) -> None:
        self._policy = policy or get_object_policy(DEFAULT_POLICY)

    @property
    def policy_name(self) -> str:
        return self._policy.policy_name

    def evaluate(self, context: ObjectSecurityContext, obj: ObjectRef, *, write: bool = False) -> ObjectAccessResult:
        return self._policy.evaluate(context, obj, write)

    def can_access(self, context: ObjectSecurityContext, obj: ObjectRef, *, write: bool = False) -> bool:
        return bool(self.evaluate(context, obj, write=write))

    def require(self, context: ObjectSecurityContext, obj: ObjectRef, *, write: bool = False) -> ObjectAccessResult:
        result = self.evaluate(context, obj, write=write)
        if not result.allowed:
            raise ObjectAccessDenied(obj.ref)
        return result


_default_service: ObjectSecurityService | None = None


def default_object_security_service() -> ObjectSecurityService:
    global _default_service
    if _default_service is None:
        _default_service = ObjectSecurityService()
    return _default_service


# --- ownership & assignment resolution (reuses record_assignments) -----------

def resolve_assignments(entity_type: str, entity_id: int, *, conn=None) -> list[dict]:
    """Active assignment rows (user/team ↔ entity) for an object."""
    query = select(
        record_assignments.c.user_id,
        record_assignments.c.team_id,
        record_assignments.c.assignment_type,
    ).where(
        record_assignments.c.entity_type == entity_type,
        record_assignments.c.entity_id == entity_id,
        _active(record_assignments),
    )
    if conn is not None:
        return [dict(r._mapping) for r in conn.execute(query)]
    with engine.connect() as connection:
        return [dict(r._mapping) for r in connection.execute(query)]


def resolve_owners(entity_type: str, entity_id: int, *, conn=None) -> frozenset[int]:
    """User ids with an active user-assignment on an object."""
    return frozenset(
        row["user_id"]
        for row in resolve_assignments(entity_type, entity_id, conn=conn)
        if row["user_id"] is not None
    )


# --- object-security events (F1.3 outbox + F1.4 envelope) --------------------

OBJECT_ACCESS_GRANTED = "object.access_granted"
OBJECT_ACCESS_DENIED = "object.access_denied"


def emit_object_access_event(conn, result: ObjectAccessResult, *, correlation_id: str | None = None, metadata: dict | None = None) -> str:
    """Publish an object-access decision via the transactional outbox.

    Reference-only payload (user_id, entity_type, entity_id, write, allowed) —
    never PII. Written in the caller's transaction. Extension point: not wired into
    the existing enforcement path (unchanged)."""
    from app.platform import new_event, publish_event  # lazy: avoid import cycles

    event_type = OBJECT_ACCESS_GRANTED if result.allowed else OBJECT_ACCESS_DENIED
    envelope = new_event(
        event_type,
        {
            "user_id": result.user_id,
            "entity_type": result.entity_type,
            "entity_id": result.entity_id,
            "write": result.write,
            "allowed": result.allowed,
        },
        producer="security.object",
        subject_ref=result.ref,
        metadata=metadata or {},
        correlation_id=correlation_id,
    )
    return publish_event(conn, envelope)
