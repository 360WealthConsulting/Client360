"""Field-Level Security Foundation (E2.4 / Backlog F2.4).

A canonical, provider-agnostic, transport-agnostic abstraction for field
visibility and redaction that **wraps and reuses** the existing implementation and
**preserves all existing behavior**.

How the existing implementation maps to this backlog feature:
  * ``redaction.SENSITIVE`` — the existing sensitive-field-name classification
    (regex: token|secret|password|tax|ssn|content|body). **Reused as-is** — this
    feature does NOT invent a new classification system.
  * ``redaction.redact_metadata`` — the existing masking primitive (mask sensitive
    keys with ``[REDACTED]``), used by ``audit.write_audit_event``. The default
    policy here reproduces its behavior exactly (agreement-tested); ``redaction.py``
    and its callers are unchanged.

Scope (F2.4): field-security service, context, field descriptor, visibility &
redaction evaluation, result model, policy abstraction, deterministic masking &
omission, extension points, and field-security events. **Out of scope** (later
features): tenant isolation, business approval workflows, audit policy, DLP,
delegated administration, compliance certification, and domain suitability/licensing.

Security posture:
  * **Fail closed** — if a policy cannot evaluate, the field is ``DENIED``.
  * **No value leakage** — results and events carry the field *name* and decision
    only, never the field value. Masking is a constant token (deterministic).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.security.models import Principal
from app.security.redaction import SENSITIVE

DEFAULT_POLICY = "sensitive-name"

# Deterministic mask token — matches redaction.redact_metadata's "[REDACTED]".
MASK_TOKEN = "[REDACTED]"

# Field visibility decisions.
VISIBLE = "visible"
MASKED = "masked"
OMITTED = "omitted"
DENIED = "denied"
VISIBILITIES = frozenset({VISIBLE, MASKED, OMITTED, DENIED})

# Sentinel returned by apply() when a field must be dropped from output.
_OMIT = object()


class FieldSecurityError(Exception):
    """Base error for the field-security foundation."""


class UnknownFieldPolicyError(FieldSecurityError, KeyError):
    """No field-security policy registered under the given name."""


def is_sensitive(field_name: str) -> bool:
    """Reuses the existing sensitive-name classification (redaction.SENSITIVE)."""
    return bool(SENSITIVE.search(field_name or ""))


@dataclass(frozen=True)
class FieldDescriptor:
    """A reference to a field being evaluated (by name)."""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise FieldSecurityError("field name must be a non-empty string")

    @property
    def sensitive(self) -> bool:
        return is_sensitive(self.name)

    def to_dict(self) -> dict:
        return {"name": self.name, "sensitive": self.sensitive}


@dataclass(frozen=True)
class FieldSecurityContext:
    """The subject for a field-security decision (principal optional — the default
    name-based policy does not depend on it)."""

    principal: Principal | None = None
    provider: str = DEFAULT_POLICY

    @classmethod
    def for_principal(cls, principal: Principal) -> FieldSecurityContext:
        return cls(principal=principal)

    @classmethod
    def system(cls) -> FieldSecurityContext:
        return cls(principal=None)

    @property
    def user_id(self) -> int | None:
        return self.principal.user_id if self.principal is not None else None

    def to_dict(self) -> dict:
        return {"user_id": self.user_id, "provider": self.provider}


@dataclass(frozen=True)
class FieldAccessResult:
    """A field-visibility decision. Carries the field NAME and decision only —
    never the field value (no value leakage)."""

    field: str
    visibility: str
    user_id: int | None = None
    reason: str = ""

    @property
    def visible(self) -> bool:
        return self.visibility == VISIBLE

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "visibility": self.visibility,
            "user_id": self.user_id,
            "reason": self.reason,
        }


@runtime_checkable
class FieldSecurityPolicy(Protocol):
    """Provider-agnostic policy contract. Decisions are made from the field name +
    context (NOT the value), so no value is ever exposed to a policy."""

    policy_name: str

    def evaluate(self, context: FieldSecurityContext, descriptor: FieldDescriptor) -> FieldAccessResult: ...


class SensitiveNameRedactionPolicy:
    """Default policy: mask fields whose name is sensitive; show the rest. This
    reproduces ``redaction.redact_metadata`` semantics."""

    policy_name = DEFAULT_POLICY

    def evaluate(self, context: FieldSecurityContext, descriptor: FieldDescriptor) -> FieldAccessResult:
        if descriptor.sensitive:
            return FieldAccessResult(descriptor.name, MASKED, context.user_id, "sensitive field name")
        return FieldAccessResult(descriptor.name, VISIBLE, context.user_id, "not sensitive")


# --- policy registry (extension point) ---------------------------------------

_policies: dict[str, FieldSecurityPolicy] = {}


def register_field_policy(policy: FieldSecurityPolicy) -> None:
    _policies[policy.policy_name] = policy


def get_field_policy(name: str = DEFAULT_POLICY) -> FieldSecurityPolicy:
    try:
        return _policies[name]
    except KeyError as exc:
        raise UnknownFieldPolicyError(name) from exc


def list_field_policies() -> list[str]:
    return sorted(_policies)


register_field_policy(SensitiveNameRedactionPolicy())


# --- field-security service --------------------------------------------------

class FieldSecurityService:
    """Evaluates field visibility and applies deterministic masking/omission.
    Fails closed (DENIED) if a policy raises."""

    def __init__(self, policy: FieldSecurityPolicy | None = None) -> None:
        self._policy = policy or get_field_policy(DEFAULT_POLICY)

    @property
    def policy_name(self) -> str:
        return self._policy.policy_name

    def evaluate(self, context: FieldSecurityContext, field_name: str) -> FieldAccessResult:
        try:
            descriptor = FieldDescriptor(field_name)
            result = self._policy.evaluate(context, descriptor)
            if result.visibility not in VISIBILITIES:  # defensive: unknown -> fail closed
                raise FieldSecurityError(f"policy returned unknown visibility {result.visibility!r}")
            return result
        except Exception:
            # Fail closed — never expose a field we could not evaluate safely.
            return FieldAccessResult(field_name, DENIED, context.user_id, "policy error (fail-closed)")

    def visibility(self, context: FieldSecurityContext, field_name: str) -> str:
        return self.evaluate(context, field_name).visibility

    def apply(self, context: FieldSecurityContext, field_name: str, value: Any) -> tuple[str, Any]:
        """Return ``(visibility, output)`` where output is the value, the mask
        token, or the ``_OMIT`` sentinel (caller drops the field)."""
        vis = self.visibility(context, field_name)
        if vis == VISIBLE:
            return vis, value
        if vis == MASKED:
            return vis, MASK_TOKEN
        return vis, _OMIT  # OMITTED / DENIED -> drop

    def redact_mapping(self, context: FieldSecurityContext, mapping: dict | None) -> dict:
        """Canonical redaction of a mapping. With the default policy this is
        identical to ``redaction.redact_metadata`` (agreement-tested)."""
        redacted: dict = {}
        for key, item in (mapping or {}).items():
            _vis, output = self.apply(context, key, item)
            if output is _OMIT:
                continue
            redacted[key] = output
        return redacted


_default_service: FieldSecurityService | None = None


def default_field_security_service() -> FieldSecurityService:
    global _default_service
    if _default_service is None:
        _default_service = FieldSecurityService()
    return _default_service


# --- field-security events (F1.3 outbox + F1.4 envelope) ---------------------

FIELD_MASKED = "field.masked"
FIELD_OMITTED = "field.omitted"
FIELD_DENIED = "field.denied"
_EVENT_BY_VISIBILITY = {MASKED: FIELD_MASKED, OMITTED: FIELD_OMITTED, DENIED: FIELD_DENIED}


def emit_field_security_event(conn, result: FieldAccessResult, *, correlation_id: str | None = None, metadata: dict | None = None) -> str | None:
    """Publish a field-security decision via the transactional outbox.

    Emits only for non-visible decisions (masked/omitted/denied). The payload is
    **reference-only** — field NAME + visibility + user_id — and never contains the
    field value (Constitution §9). Returns None for a visible field."""
    from app.platform import new_event, publish_event  # lazy: avoid import cycles

    event_type = _EVENT_BY_VISIBILITY.get(result.visibility)
    if event_type is None:  # visible -> not a security event
        return None
    envelope = new_event(
        event_type,
        {"user_id": result.user_id, "field": result.field, "visibility": result.visibility},
        producer="security.field",
        subject_ref=f"user:{result.user_id}" if result.user_id is not None else None,
        metadata=metadata or {},
        correlation_id=correlation_id,
    )
    return publish_event(conn, envelope)
