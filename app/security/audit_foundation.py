"""Security Audit & Policy Event Foundation (E2.5 / Backlog F2.5).

A canonical, provider-agnostic, transport-agnostic abstraction for security
auditing that **wraps and reuses** the two existing mechanisms and **preserves all
existing behavior**:

  * DB audit — ``audit.write_audit_event`` → ``audit_events`` (with
    ``redact_metadata``). Surfaced here as the ``db`` sink (delegates; behavior
    unchanged, agreement-tested).
  * Outbox security events — the F2.1–F2.4 ``emit_*`` helpers publish envelopes
    (``identity.*`` / ``authorization.*`` / ``object.*`` / ``field.*``). Surfaced
    here as the ``outbox`` sink using the same F1.4 envelope + F1.3 transport.

F2.5 formalizes a ``SecurityEvent`` model, a ``SecurityAuditService`` with
pluggable **sinks** (the provider abstraction), a security-event **taxonomy**, an
audit **context**, an audit **result**, and an audit **policy** — without changing
either existing mechanism. Sensitive values are scrubbed with the F2.4
field-security service before publication.

Out of scope (later features): SIEM integration, compliance reporting, retention,
immutable storage, analytics, alerting, intrusion detection, tenant isolation,
delegated administration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Protocol, runtime_checkable

from app.security.field_security import FieldSecurityContext, default_field_security_service

# --- security event taxonomy -------------------------------------------------

AUTHENTICATION = "authentication"
AUTHORIZATION = "authorization"
OBJECT = "object"
FIELD = "field"
SESSION = "session"
GENERIC = "security"
CATEGORIES = frozenset({AUTHENTICATION, AUTHORIZATION, OBJECT, FIELD, SESSION, GENERIC})

_ACTION_PREFIX_TO_CATEGORY = (
    ("identity.", AUTHENTICATION),
    ("authorization.", AUTHORIZATION),
    ("object.", OBJECT),
    ("field.", FIELD),
    ("session.", SESSION),
)


def category_for_action(action: str) -> str:
    """Map a canonical action (e.g. ``identity.authenticated``) to its category."""
    for prefix, category in _ACTION_PREFIX_TO_CATEGORY:
        if action.startswith(prefix):
            return category
    return GENERIC


class SecurityAuditError(Exception):
    """Base error for the security-audit foundation."""


class UnknownSinkError(SecurityAuditError, KeyError):
    """No audit sink registered under the given name."""


@dataclass(frozen=True)
class SecurityEvent:
    """A canonical security/policy event. ``attributes`` are references only and
    are scrubbed before publication — never secrets or sensitive values."""

    action: str
    category: str = ""
    actor_user_id: int | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    subject_ref: str | None = None
    outcome: str = "info"
    attributes: dict = field(default_factory=dict)
    template_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or not self.action.strip():
            raise SecurityAuditError("action must be a non-empty string")
        if not self.category:
            object.__setattr__(self, "category", category_for_action(self.action))
        if self.category not in CATEGORIES:
            raise SecurityAuditError(f"unknown category: {self.category!r}")
        if not isinstance(self.attributes, dict):
            raise SecurityAuditError("attributes must be a dict")

    def scrubbed_attributes(self) -> dict:
        """Attributes with sensitive-named values masked (reuses F2.4)."""
        return default_field_security_service().redact_mapping(
            FieldSecurityContext.system(), self.attributes
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SecurityEvent:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_envelope(self, *, event_id: str | None = None, occurred_at: str | None = None, correlation_id: str | None = None):
        """Serialize to a canonical F1.4 event envelope (scrubbed, reference-only)."""
        from app.platform import new_event  # lazy: avoid import cycles

        payload = {
            "outcome": self.outcome,
            "actor_user_id": self.actor_user_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            **self.scrubbed_attributes(),
        }
        metadata: dict[str, Any] = {"category": self.category}
        if self.template_id:
            metadata["template_id"] = self.template_id
        kwargs: dict[str, Any] = {
            "producer": f"security.{self.category}",
            "subject_ref": self.subject_ref,
            "metadata": metadata,
            "correlation_id": correlation_id,
        }
        if event_id is not None:
            kwargs["event_id"] = event_id
        if occurred_at is not None:
            kwargs["occurred_at"] = occurred_at
        return new_event(self.action, payload, **kwargs)


def for_workflow_template(action: str, template_id: str, **kwargs) -> SecurityEvent:
    """Build a SecurityEvent linked to a registered workflow template (F1.5).

    Validates the template exists in the default registry (integration point)."""
    from app.platform import default_registry

    default_registry().get(template_id)  # raises if unknown
    return SecurityEvent(action=action, template_id=template_id, **kwargs)


@dataclass(frozen=True)
class AuditContext:
    """Ambient context for an audit record (request/correlation/client)."""

    request_id: str = "security-audit"
    actor_user_id: int | None = None
    correlation_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None

    @classmethod
    def from_request(cls, request) -> AuditContext:
        state = getattr(request, "state", None)
        principal = getattr(state, "principal", None)
        client = getattr(request, "client", None)
        headers = getattr(request, "headers", None)
        return cls(
            request_id=getattr(state, "request_id", None) or "security-audit",
            actor_user_id=getattr(principal, "user_id", None),
            ip_address=getattr(client, "host", None) if client else None,
            user_agent=headers.get("user-agent") if headers is not None else None,
        )


@dataclass(frozen=True)
class AuditResult:
    """Outcome of recording a security event across sinks."""

    recorded: bool
    sinks: tuple[str, ...] = ()
    event_id: str | None = None
    audit_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# --- sinks (provider-agnostic audit interface) -------------------------------

@runtime_checkable
class AuditSink(Protocol):
    """A destination for security events. Future sinks (SIEM, immutable store)
    implement this and register via ``register_sink``."""

    sink_name: str

    def record(self, event: SecurityEvent, context: AuditContext, *, conn=None) -> dict: ...


class OutboxAuditSink:
    """Publishes a security event via the F1.3 transactional outbox using the F1.4
    envelope. Written in the caller's transaction when ``conn`` is provided."""

    sink_name = "outbox"

    def record(self, event: SecurityEvent, context: AuditContext, *, conn=None) -> dict:
        from app.platform import publish_event  # lazy

        envelope = event.to_envelope(correlation_id=context.correlation_id)
        if conn is not None:
            event_id = publish_event(conn, envelope)
        else:
            from app.db import engine

            with engine.begin() as connection:
                event_id = publish_event(connection, envelope)
        return {"sink": self.sink_name, "event_id": event_id}


class DbAuditSink:
    """Delegates to the existing ``audit.write_audit_event`` → ``audit_events``
    (with ``redact_metadata``). Behavior is unchanged (agreement-tested)."""

    sink_name = "db"

    def record(self, event: SecurityEvent, context: AuditContext, *, conn=None) -> dict:
        from app.security.audit import write_audit_event  # lazy

        audit_id = write_audit_event(
            action=event.action,
            entity_type=event.entity_type or event.category,
            request_id=context.request_id,
            actor_user_id=event.actor_user_id if event.actor_user_id is not None else context.actor_user_id,
            entity_id=event.entity_id,
            outcome=event.outcome,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata=event.attributes,  # write_audit_event applies redact_metadata
        )
        return {"sink": self.sink_name, "audit_id": audit_id}


# --- sink registry (extension point) -----------------------------------------

_sinks: dict[str, AuditSink] = {}


def register_sink(sink: AuditSink) -> None:
    _sinks[sink.sink_name] = sink


def get_sink(name: str) -> AuditSink:
    try:
        return _sinks[name]
    except KeyError as exc:
        raise UnknownSinkError(name) from exc


def list_sinks() -> list[str]:
    return sorted(_sinks)


register_sink(OutboxAuditSink())
register_sink(DbAuditSink())


# --- audit policy abstraction ------------------------------------------------

@runtime_checkable
class AuditPolicy(Protocol):
    policy_name: str

    def should_record(self, event: SecurityEvent) -> bool: ...


class RecordAllPolicy:
    """Default policy: record every event."""

    policy_name = "record-all"

    def should_record(self, event: SecurityEvent) -> bool:
        return True


# --- security audit service --------------------------------------------------

class SecurityAuditService:
    """Records a security event to its configured sinks, gated by a policy.
    Deterministic and reference-only (attributes scrubbed before publication)."""

    def __init__(self, sinks: list[AuditSink] | None = None, policy: AuditPolicy | None = None) -> None:
        self._sinks = list(sinks) if sinks is not None else [get_sink("outbox")]
        self._policy = policy or RecordAllPolicy()

    @property
    def sink_names(self) -> list[str]:
        return [s.sink_name for s in self._sinks]

    def record(self, event: SecurityEvent, context: AuditContext | None = None, *, conn=None) -> AuditResult:
        context = context or AuditContext()
        if not self._policy.should_record(event):
            return AuditResult(recorded=False)
        recorded: list[str] = []
        event_id: str | None = None
        audit_id: int | None = None
        for sink in self._sinks:
            outcome = sink.record(event, context, conn=conn)
            recorded.append(outcome["sink"])
            event_id = outcome.get("event_id", event_id)
            audit_id = outcome.get("audit_id", audit_id)
        return AuditResult(recorded=True, sinks=tuple(recorded), event_id=event_id, audit_id=audit_id)


_default_service: SecurityAuditService | None = None


def default_security_audit_service() -> SecurityAuditService:
    """Process-wide service using the outbox sink (event-driven security audit)."""
    global _default_service
    if _default_service is None:
        _default_service = SecurityAuditService()
    return _default_service
