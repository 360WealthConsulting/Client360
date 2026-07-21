"""Advisor Intelligence framework (Phase D.5A).

The framework that will eventually host Advisor Intelligence — and *only* the
framework. This phase ships deterministic signal INFRASTRUCTURE: a signal model,
an explainability model, a priority model, policy-gate placeholders, a signal
registry, and a thin composition layer
(``get_client_signals`` / ``get_household_signals`` / ``get_dashboard_signals``).

It generates NO signals. No rules are registered with executable bodies, the
composition accessors run no rules, and every accessor returns an empty
collection. There is deliberately no recommendation, AI/LLM/ML, vector/embedding,
historical/predictive, compliance, suitability, or business-rule logic here, and
no writes and no new tables.

Governance (docs/ADVISOR_WORKSPACE_ARCHITECTURE.md §4, §7): Advisor Intelligence is
a **deterministic, propose-only** orchestration layer. It composes existing
authoritative services and must never become a portfolio, tax, insurance,
benefits, workflow, or compliance engine. Regulated signals are ``[Policy-gated]``
and stay inert until the firm supplies rules and an accountable compliance owner
exists (GOV-2 / PD-4). This module is the seam those future, governed rules will
attach to; today it is empty.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.db import engine
from app.security.authorization import accessible_person_ids, record_in_scope

# --------------------------------------------------------------------------- #
# Priority model — ordering only, no scoring algorithm.
# --------------------------------------------------------------------------- #


class Priority(StrEnum):
    """Signal priority. Ordering is fixed and deterministic; there is no scoring
    algorithm in this phase — a future rule sets a value, it is never computed."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"

    @property
    def rank(self) -> int:
        """Higher = more urgent. Critical (4) … Informational (0)."""
        return _PRIORITY_RANK[self]

    @staticmethod
    def sort_key(signal: Signal) -> int:
        """Descending sort key (most urgent first): ``sorted(signals, key=..., reverse=True)``."""
        return signal.priority.rank


_PRIORITY_ORDER: tuple[Priority, ...] = (
    Priority.CRITICAL,
    Priority.HIGH,
    Priority.MEDIUM,
    Priority.LOW,
    Priority.INFORMATIONAL,
)
_PRIORITY_RANK: dict[Priority, int] = {
    p: len(_PRIORITY_ORDER) - 1 - i for i, p in enumerate(_PRIORITY_ORDER)
}


# --------------------------------------------------------------------------- #
# Policy gates — display-only placeholders. NOTHING is enforced here.
# --------------------------------------------------------------------------- #


class PolicyGate(StrEnum):
    """Placeholder policy gate for a (future) signal. Display metadata only: this
    phase enforces no regulatory logic. Turning any gate into actual enforcement
    is a governed, compliance-owned decision (GOV-2 / PD-4), not this framework's job."""

    NONE = "none"
    COMPLIANCE_REQUIRED = "compliance_required"
    LICENSE_REQUIRED = "license_required"
    SUITABILITY_REQUIRED = "suitability_required"


# --------------------------------------------------------------------------- #
# Explainability model — populated with placeholders in this phase.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Explainability:
    """Why a signal exists, which service produced it, and the evidence used.

    Every future signal MUST carry this. ``confidence`` is a **deterministic**
    placeholder (0.0) — there is no probabilistic/AI scoring in this framework.
    """

    why: str = ""
    source_service: str = ""
    evidence: tuple[str, ...] = ()
    confidence: float = 0.0
    policy_gate: PolicyGate = PolicyGate.NONE

    def to_dict(self) -> dict:
        return {
            "why": self.why,
            "source_service": self.source_service,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "policy_gate": self.policy_gate.value,
        }


# --------------------------------------------------------------------------- #
# Signal model — informational only. No recommendations, no generated advice.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceRecord:
    """The canonical record a signal is anchored to (never widens scope)."""

    entity_type: str  # "person" | "household"
    entity_id: int

    def to_dict(self) -> dict:
        return {"entity_type": self.entity_type, "entity_id": self.entity_id}


@dataclass(frozen=True)
class Signal:
    """A deterministic, propose-only, informational signal.

    A signal states a fact with its evidence and explainability; it is never a
    recommendation, never advice, and never mutates anything. ``created_at`` is
    caller-supplied (an ISO-8601 string) rather than generated, so the model
    stays deterministic. No signals are produced in this phase — this is the
    shape a governed D.5B rule will emit.
    """

    id: str
    category: str
    title: str
    summary: str
    source_service: str
    source_record: SourceRecord | None = None
    severity: str = "info"
    priority: Priority = Priority.INFORMATIONAL
    evidence: tuple[str, ...] = ()
    explainability: Explainability = field(default_factory=Explainability)
    policy_gate: PolicyGate = PolicyGate.NONE
    route: str | None = None
    status: str = "open"
    created_at: str | None = None

    def to_dict(self) -> dict:
        """JSON-safe serialization (enums → their values, nested models → dicts)."""
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "summary": self.summary,
            "source_service": self.source_service,
            "source_record": self.source_record.to_dict() if self.source_record else None,
            "severity": self.severity,
            "priority": self.priority.value,
            "evidence": list(self.evidence),
            "explainability": self.explainability.to_dict(),
            "policy_gate": self.policy_gate.value,
            "route": self.route,
            "status": self.status,
            "created_at": self.created_at,
        }


# --------------------------------------------------------------------------- #
# Signal registry — metadata only. Rules are NOT executed in this phase.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegisteredSignal:
    """A deterministic rule's registration record — its identity and metadata.

    The registry records that a rule *exists*; it holds no executable body and
    the composition layer never runs it in this phase. Executable, governed rule
    bodies arrive in D.5B and will attach to the producer seam below.
    """

    key: str
    category: str
    source_service: str
    default_priority: Priority = Priority.INFORMATIONAL
    policy_gate: PolicyGate = PolicyGate.NONE
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "category": self.category,
            "source_service": self.source_service,
            "default_priority": self.default_priority.value,
            "policy_gate": self.policy_gate.value,
            "description": self.description,
        }


_REGISTRY: dict[str, RegisteredSignal] = {}


def register_signal(
    key: str,
    *,
    category: str,
    source_service: str,
    default_priority: Priority = Priority.INFORMATIONAL,
    policy_gate: PolicyGate = PolicyGate.NONE,
    description: str = "",
) -> RegisteredSignal:
    """Register a deterministic signal rule's metadata. Idempotent-safe: a
    duplicate key raises, so two rules can never silently share an id. The rule
    is NOT executed by registering it (this phase runs no rules)."""
    if not key:
        raise ValueError("signal key is required")
    if key in _REGISTRY:
        raise ValueError(f"signal already registered: {key!r}")
    registered = RegisteredSignal(
        key=key,
        category=category,
        source_service=source_service,
        default_priority=default_priority,
        policy_gate=policy_gate,
        description=description,
    )
    _REGISTRY[key] = registered
    return registered


def list_registered_signals() -> tuple[RegisteredSignal, ...]:
    """All registered signal rules, ordered by key (deterministic)."""
    return tuple(_REGISTRY[k] for k in sorted(_REGISTRY))


def clear_registry() -> None:
    """Reset the registry. The framework has no persistence; this exists so tests
    can isolate registrations."""
    _REGISTRY.clear()


# --------------------------------------------------------------------------- #
# Composition layer — record-scoped, returns () in this phase.
# --------------------------------------------------------------------------- #

# The producer seam. In D.5B, governed deterministic rules attach here as
# ``(SignalContext) -> Iterable[Signal]`` callables. It is EMPTY in this phase,
# so every accessor deterministically produces no signals. Crucially, a producer
# only ever runs AFTER the caller-provided record scope has been resolved, so a
# future rule can never see a record outside the advisor's book.
_PRODUCERS: list[Callable[[SignalContext], Iterable[Signal]]] = []


@dataclass(frozen=True)
class SignalContext:
    """The already-scoped context handed to a (future) producer. ``person_ids`` is
    the accessible-person scope (``None`` = firm-wide reader); ``person_id`` /
    ``household_id`` narrow to a single record when set. A producer must confine
    its reads to this scope — the framework never widens it."""

    principal: object
    person_ids: frozenset[int] | None
    person_id: int | None = None
    household_id: int | None = None


def _collect(ctx: SignalContext) -> tuple[Signal, ...]:
    """Run the registered producer seam over an already-scoped context.

    Empty in this phase (no rules attached) → always ``()``. This is the single
    future dispatch point; keeping it here means scope is enforced once, at the
    accessor boundary, before any rule can run.
    """
    signals: list[Signal] = []
    for produce in _PRODUCERS:
        signals.extend(produce(ctx))
    return _order(signals)


def _order(signals: Sequence[Signal]) -> tuple[Signal, ...]:
    """Most-urgent-first, stable within a priority."""
    return tuple(sorted(signals, key=Priority.sort_key, reverse=True))


def get_dashboard_signals(principal) -> tuple[Signal, ...]:
    """Book-scoped advisor-dashboard signals. Returns ``()`` — no rules are
    registered to run yet (Phase D.5A). Scope is still resolved so that, when
    governed rules arrive, they can only ever run across the advisor's accessible
    book (``accessible_person_ids``)."""
    with engine.connect() as conn:
        person_ids = accessible_person_ids(conn, principal)
    ctx = SignalContext(
        principal=principal,
        person_ids=None if person_ids is None else frozenset(person_ids),
    )
    return _collect(ctx)


def get_client_signals(principal, person_id: int) -> tuple[Signal, ...]:
    """Signals for one client. Enforces person record-scope FIRST: an inaccessible
    person yields ``()`` and never reaches a producer, so it can never expose
    another client's data. An accessible client also yields ``()`` in this phase
    (no rules registered)."""
    if not record_in_scope(principal, "person", person_id):
        return ()
    ctx = SignalContext(
        principal=principal,
        person_ids=frozenset({person_id}),
        person_id=person_id,
    )
    return _collect(ctx)


def get_household_signals(principal, household_id: int) -> tuple[Signal, ...]:
    """Signals for one household. Enforces household record-scope FIRST: an
    inaccessible household yields ``()`` and never reaches a producer. An
    accessible household also yields ``()`` in this phase (no rules registered)."""
    if not record_in_scope(principal, "household", household_id):
        return ()
    ctx = SignalContext(
        principal=principal,
        person_ids=None,
        household_id=household_id,
    )
    return _collect(ctx)
