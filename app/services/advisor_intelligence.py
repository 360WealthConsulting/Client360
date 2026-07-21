"""Advisor Intelligence (D.5A framework · D.5B signals · D.5C opportunities · D.5D recommendations).

D.5A shipped the deterministic signal INFRASTRUCTURE: the signal model, an
explainability model, a priority model, policy-gate placeholders, a signal
registry, and a thin composition layer
(``get_client_signals`` / ``get_household_signals`` / ``get_dashboard_signals``).

D.5B activated that framework with **factual, deterministic, operational** signals.
D.5C added **advisor opportunities** (factual reasons a client deserves attention).
D.5D adds **compliance-governed advisor recommendations** — advisor-facing, "X may
be appropriate based on ..." — each originating from a REGISTERED deterministic rule
and carrying immutable governance metadata (governing rule, version, compliance
owner, approval status) plus a policy gate. Recommendations are informational: gates
and approval status are DISPLAY-ONLY (no enforcement, blocking, execution, workflow,
or persistence). A recommendation is never a client communication, automated advice,
an automated decision, or a compliance/suitability determination. Every producer
(bottom of this module) composes an EXISTING authoritative, record-scoped read; none
recreates a domain's status/cadence logic. There is deliberately no regulated advice,
probabilistic scoring, policy interpretation, AI/LLM/ML, vector/embedding, or
historical/predictive logic here, and no writes, no persistence, and no new tables.

Governance (docs/ADVISOR_WORKSPACE_ARCHITECTURE.md §4, §7): Advisor Intelligence is
a **deterministic, propose-only** orchestration layer. It composes existing
authoritative services and must never become a portfolio, tax, insurance,
benefits, workflow, or compliance engine. Regulated signals remain ``[Policy-gated]``
and are NOT implemented here; they stay inert until the firm supplies rules and an
accountable compliance owner exists (GOV-2 / PD-4). Every operational signal in
this phase is ``PolicyGate.NONE`` with deterministic confidence.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum

from sqlalchemy import select

from app.db import engine, people
from app.security.authorization import accessible_person_ids, record_in_scope
from app.services.advisor_workspace import FIRM_TZ
from app.services.exception_engine import open_exceptions_for_people
from app.services.insurance import reviews_due_for_people
from app.services.portfolio import (
    accounts_due_for_review,
    accounts_missing_required_beneficiary,
    accounts_review_approaching,
)
from app.services.tasks import open_tasks_for_people
from app.services.timeline import recent_events

# A review this many days past due (or never reviewed) is treated as *materially*
# overdue for priority mapping; a task this many days past due likewise. These are
# deterministic thresholds over authoritative evidence, not scores or policy.
_MATERIAL_REVIEW_STALE_DAYS = 365
_MATERIAL_TASK_OVERDUE_DAYS = 30

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

    Every signal MUST carry this. ``confidence`` is **deterministic** (0.0 default;
    operational D.5B signals set 1.0) — there is no probabilistic/AI scoring here.
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
class RecommendationMeta:
    """Immutable governance metadata carried by an advisor RECOMMENDATION (Phase
    D.5D). A recommendation is advisor-facing and informational — never a client
    communication, automated advice, or a compliance/suitability determination.
    Every recommendation originates from a registered deterministic rule and
    declares that rule, its version, its compliance owner, and its approval status.
    All of this is DISPLAY-ONLY metadata: no gate is enforced, nothing is executed,
    nothing is persisted."""

    recommendation_type: str
    governing_rule: str
    rule_version: str
    compliance_owner: str
    approval_status: str
    created_from_rule: str  # the registry key of the rule that produced this

    def to_dict(self) -> dict:
        return {
            "recommendation_type": self.recommendation_type,
            "governing_rule": self.governing_rule,
            "rule_version": self.rule_version,
            "compliance_owner": self.compliance_owner,
            "approval_status": self.approval_status,
            "created_from_rule": self.created_from_rule,
        }


@dataclass(frozen=True)
class Signal:
    """A deterministic, propose-only, informational signal.

    A signal states a fact with its evidence and explainability; it is never a
    recommendation, never advice, and never mutates anything. ``created_at`` is
    caller-supplied (an ISO-8601 string) rather than generated, so the model
    stays deterministic. This is the shape the deterministic operational
    producers (D.5B) emit.
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
    # Present only on advisor recommendations (category "recommendation"); None for
    # operational signals and opportunities.
    recommendation: RecommendationMeta | None = None

    @property
    def group(self) -> str:
        """The top-level UI bucket a signal belongs to. Keeps the three families
        (operational signals, opportunities, recommendations) visually separate."""
        if self.category == "recommendation":
            return "Advisor Recommendations"
        if self.category == "opportunity":
            return "Advisor Opportunities"
        return "Operational Signals"

    def to_dict(self) -> dict:
        """JSON-safe serialization (enums → their values, nested models → dicts)."""
        return {
            "id": self.id,
            "category": self.category,
            "group": self.group,
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
            "recommendation": self.recommendation.to_dict() if self.recommendation else None,
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
    # Governance metadata — populated for recommendation rules (D.5D); None otherwise.
    governing_rule: str | None = None
    rule_version: str | None = None
    compliance_owner: str | None = None
    approval_status: str | None = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "category": self.category,
            "source_service": self.source_service,
            "default_priority": self.default_priority.value,
            "policy_gate": self.policy_gate.value,
            "description": self.description,
            "governing_rule": self.governing_rule,
            "rule_version": self.rule_version,
            "compliance_owner": self.compliance_owner,
            "approval_status": self.approval_status,
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
    governing_rule: str | None = None,
    rule_version: str | None = None,
    compliance_owner: str | None = None,
    approval_status: str | None = None,
) -> RegisteredSignal:
    """Register a deterministic rule's metadata. Idempotent-safe: a duplicate key
    raises, so two rules can never silently share an id. Recommendation rules
    (D.5D) additionally record their governing rule id, version, compliance owner,
    and approval status — all informational, display-only governance metadata."""
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
        governing_rule=governing_rule,
        rule_version=rule_version,
        compliance_owner=compliance_owner,
        approval_status=approval_status,
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

# The producer seam. Deterministic operational producers (Phase D.5B) attach here
# as ``(SignalContext) -> Iterable[Signal]`` callables (see the bottom of this
# module). A producer only ever runs AFTER the caller-provided record scope has
# been resolved onto ``ctx.person_ids``, so it can never see a record outside the
# advisor's book. Regulated/policy signals do NOT attach here — this phase carries
# only factual operational awareness.
_PRODUCERS: list[Callable[[SignalContext], Iterable[Signal]]] = []


@dataclass(frozen=True)
class SignalContext:
    """The already-scoped context handed to a producer. ``person_ids`` is the
    resolved accessible-person scope (``None`` = firm-wide reader, a set = exactly
    the records this call may read); every producer reads strictly by this scope,
    so the framework never widens it. ``person_id`` / ``household_id`` name the
    single record when the call is a client/household view. ``now`` / ``today``
    fix the firm-timezone clock used by date-based producers (deterministic and
    injectable for tests)."""

    principal: object
    person_ids: frozenset[int] | None
    now: datetime
    today: object  # datetime.date
    person_id: int | None = None
    household_id: int | None = None


def _collect(ctx: SignalContext) -> tuple[Signal, ...]:
    """Run the producer seam over an already-scoped context, de-duplicating by
    signal id (the same underlying fact never yields two signals) and returning a
    deterministically ordered tuple. Scope is enforced once, at the accessor
    boundary, before any producer runs."""
    by_id: dict[str, Signal] = {}
    for produce in _PRODUCERS:
        for signal in produce(ctx):
            by_id.setdefault(signal.id, signal)
    return _order(by_id.values())


def _order(signals: Iterable[Signal]) -> tuple[Signal, ...]:
    """Most-urgent-first (priority rank), then by stable signal id — fully
    deterministic (no scoring, no time-dependent tiebreak)."""
    return tuple(sorted(signals, key=lambda s: (-s.priority.rank, s.id)))


#: Fixed display order for the three signal families. Business grouping lives here
#: (Python), not in the template (D.5E) — the shared renderer just iterates.
_GROUP_ORDER = ("Operational Signals", "Advisor Opportunities", "Advisor Recommendations")


def group_signals(signals: Iterable[Signal]) -> list[tuple[str, list[Signal]]]:
    """Group already-ordered signals into their display buckets in fixed order,
    dropping empty buckets. Within a bucket the incoming order is preserved. Exposed
    to the shared Advisor Intelligence template so grouping is decided in Python."""
    buckets: dict[str, list[Signal]] = {label: [] for label in _GROUP_ORDER}
    for signal in signals:
        buckets[signal.group].append(signal)
    return [(label, buckets[label]) for label in _GROUP_ORDER if buckets[label]]


def _firm_now(now: datetime | None) -> datetime:
    return now or datetime.now(FIRM_TZ)


def get_dashboard_signals(principal, *, now: datetime | None = None) -> tuple[Signal, ...]:
    """Book-scoped advisor-dashboard signals. Scope is resolved to the advisor's
    accessible book (``accessible_person_ids`` — ``None`` for a firm-wide reader)
    BEFORE any producer runs, so producers only ever read across that book."""
    stamp = _firm_now(now)
    with engine.connect() as conn:
        person_ids = accessible_person_ids(conn, principal)
    ctx = SignalContext(
        principal=principal,
        person_ids=None if person_ids is None else frozenset(person_ids),
        now=stamp,
        today=stamp.date(),
    )
    return _collect(ctx)


def get_client_signals(principal, person_id: int, *, now: datetime | None = None) -> tuple[Signal, ...]:
    """Signals for one client. Enforces person record-scope FIRST: an inaccessible
    person yields ``()`` and never reaches a producer, so producer logic can never
    read or expose another client's data."""
    if not record_in_scope(principal, "person", person_id):
        return ()
    stamp = _firm_now(now)
    ctx = SignalContext(
        principal=principal,
        person_ids=frozenset({person_id}),
        now=stamp,
        today=stamp.date(),
        person_id=person_id,
    )
    return _collect(ctx)


def _household_member_ids(household_id: int) -> frozenset[int]:
    with engine.connect() as conn:
        return frozenset(conn.scalars(
            select(people.c.id).where(people.c.household_id == household_id)))


def get_household_signals(principal, household_id: int, *, now: datetime | None = None) -> tuple[Signal, ...]:
    """Signals for one household. Enforces household record-scope FIRST: an
    inaccessible household yields ``()`` and never reaches a producer. Scope is the
    household's member person ids, so producers read only those records."""
    if not record_in_scope(principal, "household", household_id):
        return ()
    stamp = _firm_now(now)
    ctx = SignalContext(
        principal=principal,
        person_ids=_household_member_ids(household_id),
        now=stamp,
        today=stamp.date(),
        household_id=household_id,
    )
    return _collect(ctx)


# --------------------------------------------------------------------------- #
# Deterministic operational producers (Phase D.5B).
#
# Each producer composes an EXISTING authoritative, record-scoped read and emits
# factual, propose-only signals. No producer queries a domain table directly, no
# producer recomputes a domain's status/eligibility logic, and no producer emits
# a recommendation, a probabilistic score, or a policy conclusion. Every emitted
# signal is anchored to a source record within ``ctx.person_ids`` (the scope
# resolved at the accessor boundary), so it can never expose an inaccessible
# record. All are PolicyGate.NONE with deterministic confidence 1.0.
# --------------------------------------------------------------------------- #

# Exception severity -> priority, straight from the authoritative source label
# (exceptions severities are blocker/high/medium/low). "blocker" is the source's
# most-severe label, so it maps to Critical — Critical is never invented.
_SEVERITY_PRIORITY = {
    "blocker": Priority.CRITICAL,
    "high": Priority.HIGH,
    "medium": Priority.MEDIUM,
    "low": Priority.LOW,
}


def _signal_id(signal_type: str, record_type: str, record_id) -> str:
    """Stable, deterministic id: type:record_type:record_id. Globally unique per
    underlying record, so the same fact never produces a duplicate signal."""
    return f"{signal_type}:{record_type}:{record_id}"


def _evidence(**pairs) -> tuple[str, ...]:
    """Build a deterministic evidence tuple of ``"key=value"`` strings, in call
    order. Centralizes the evidence-string construction every rule shared."""
    return tuple(f"{key}={value}" for key, value in pairs.items())


def _emit(*, key: str, category: str, source_record: SourceRecord, title: str,
          summary: str, source_service: str, explain_source: str, why: str,
          evidence: tuple[str, ...], severity: str, route: str | None,
          priority: Priority = Priority.MEDIUM, policy_gate: PolicyGate = PolicyGate.NONE,
          status: str = "open", recommendation: RecommendationMeta | None = None) -> Signal:
    """The shared deterministic Signal builder — the single place a rule result
    becomes a ``Signal`` (the "Rule → Evidence → Signal" pipeline). It owns the
    deterministic id (derived from ``key`` + ``source_record``) and the
    ``Explainability`` object (evidence + deterministic confidence 1.0 + policy
    gate), so a producer supplies only its rule-specific fields. ``explain_source``
    is the detailed read path recorded in the explainability (kept separate from the
    short ``source_service`` shown on the signal). Output is byte-identical to the
    pre-D.5E inline construction."""
    return Signal(
        id=_signal_id(key, source_record.entity_type, source_record.entity_id),
        category=category,
        title=title,
        summary=summary,
        source_service=source_service,
        source_record=source_record,
        severity=severity,
        priority=priority,
        evidence=evidence,
        explainability=Explainability(
            why=why, source_service=explain_source, evidence=evidence,
            confidence=1.0, policy_gate=policy_gate),
        policy_gate=policy_gate,
        route=route,
        status=status,
        recommendation=recommendation,
    )


def _review_overdue_producer(ctx: SignalContext) -> list[Signal]:
    """Client review overdue — from the authoritative wealth review-due read
    (``portfolio.accounts_due_for_review``). The due basis is NOT recomputed here."""
    signals: list[Signal] = []
    for acct in accounts_due_for_review(
        ctx.person_ids, stale_days=_MATERIAL_REVIEW_STALE_DAYS, today=ctx.today, limit=200
    ):
        last = acct.get("last_review_date")
        # Never-reviewed accounts are materially overdue; otherwise ordinary.
        priority = Priority.HIGH if last is None else Priority.MEDIUM
        acct_label = acct.get("account_name") or acct.get("account_number") or f"account {acct['id']}"
        basis = "never reviewed" if last is None else f"last reviewed {last}"
        signals.append(_emit(
            key="client_review_overdue", category="review",
            source_record=SourceRecord("account", acct["id"]),
            title=f"Account review overdue — {acct_label}",
            summary=f"Account review is overdue ({basis}).",
            source_service="portfolio",
            explain_source="portfolio.accounts_due_for_review",
            why="Account last_review_date is null or older than the review-due "
                "threshold, per portfolio.accounts_due_for_review.",
            evidence=_evidence(
                account_id=acct["id"], account=acct_label,
                person_id=acct.get("person_id"), last_review_date=last,
                stale_days_threshold=_MATERIAL_REVIEW_STALE_DAYS),
            severity="review_overdue", priority=priority,
            route=_person_route(acct.get("person_id"))))
    return signals


def _open_exception_producer(ctx: SignalContext) -> list[Signal]:
    """Open client exception — from the authoritative Exception Engine read
    (``exception_engine.open_exceptions_for_people``). Severity is preserved from
    the source; no exception logic is recreated."""
    signals: list[Signal] = []
    for exc in open_exceptions_for_people(ctx.person_ids, limit=200):
        severity = (exc.get("severity") or "").lower()
        priority = _SEVERITY_PRIORITY.get(severity, Priority.INFORMATIONAL)
        title = exc.get("title") or f"exception {exc['id']}"
        signals.append(_emit(
            key="open_client_exception", category="exception",
            source_record=SourceRecord("exception", exc["id"]),
            title=f"Open exception — {title}",
            summary="Exception remains open.",
            source_service="exception_engine",
            explain_source="exception_engine.open_exceptions_for_people",
            why="Exception status is not resolved/cancelled, per "
                "exception_engine.open_exceptions_for_people.",
            evidence=_evidence(
                exception_id=exc["id"], domain=exc.get("domain"),
                category=exc.get("category"), severity=exc.get("severity"),
                status=exc.get("status"), opened_at=exc.get("opened_at")),
            severity=severity or "info", priority=priority,
            route=_person_route(exc.get("person_id")) or "/exceptions",
            status=exc.get("status") or "open"))
    return signals


def _overdue_task_producer(ctx: SignalContext) -> list[Signal]:
    """Overdue open task — from the authoritative task read
    (``tasks.open_tasks_for_people``). "Overdue" is a factual due_date < today
    comparison; task status comes from the stored field (not recomputed)."""
    signals: list[Signal] = []
    for task in open_tasks_for_people(ctx.person_ids, limit=200):
        due = task.get("due_date")
        if due is None or due >= ctx.today:
            continue  # only overdue tasks
        days_overdue = (ctx.today - due).days
        priority = Priority.HIGH if days_overdue > _MATERIAL_TASK_OVERDUE_DAYS else Priority.MEDIUM
        title = task.get("title") or f"task {task['id']}"
        signals.append(_emit(
            key="overdue_open_task", category="task",
            source_record=SourceRecord("task", task["id"]),
            title=f"Task overdue — {title}",
            summary=f"Task is overdue by {days_overdue} day(s).",
            source_service="tasks",
            explain_source="tasks.open_tasks_for_people",
            why="Task due_date is before today and status is open, per "
                "tasks.open_tasks_for_people.",
            evidence=_evidence(
                task_id=task["id"], title=title, due_date=due,
                status=task.get("status"), days_overdue=days_overdue),
            severity="task_overdue", priority=priority,
            route=(f"{_person_route(task.get('person_id'))}?tab=tasks"
                   if task.get("person_id") else "/tasks"),
            status=task.get("status") or "open"))
    return signals


def _next_business_day(day):
    """The next business day after ``day`` (skips Sat/Sun). Deterministic."""
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5:  # 5=Sat, 6=Sun
        nxt += timedelta(days=1)
    return nxt


def _upcoming_meeting_producer(ctx: SignalContext) -> list[Signal]:
    """Upcoming client meeting requiring preparation — from the authoritative
    calendar/timeline read (``timeline.recent_events``), limited to a near-term
    window: today through the end of the next business day, in the firm timezone
    (the same read/tz the Daily Dashboard uses). No scheduler/reminder created."""
    day_start = datetime.combine(ctx.today, time.min, tzinfo=FIRM_TZ)
    window_end = datetime.combine(
        _next_business_day(ctx.today) + timedelta(days=1), time.min, tzinfo=FIRM_TZ)
    signals: list[Signal] = []
    for ev in recent_events(
        ctx.person_ids, event_types=("calendar_event",),
        start=day_start, end=window_end, limit=200,
    ):
        person_id = ev.get("person_id")
        if not person_id:
            continue  # a person-anchored meeting only
        when = ev.get("event_time")
        signals.append(_emit(
            key="upcoming_client_meeting", category="meeting",
            source_record=SourceRecord("timeline_event", ev["id"]),
            title=ev.get("title") or "Upcoming client meeting",
            summary="Meeting is scheduled within the preparation window.",
            source_service="timeline",
            explain_source="timeline.recent_events",
            why="A calendar_event for this client falls within today through the "
                "next business day, per timeline.recent_events.",
            evidence=_evidence(
                event_id=ev["id"], event_time=when, person_id=person_id,
                event_type="calendar_event"),
            severity="info", priority=Priority.MEDIUM,
            route=f"/workspace/meetings/{person_id}?event={ev['id']}"))
    return signals


def _person_route(person_id) -> str | None:
    return f"/people/{person_id}" if person_id else None


# --------------------------------------------------------------------------- #
# Deterministic advisor OPPORTUNITY producers (Phase D.5C).
#
# An opportunity is a factual, evidence-backed reason a client deserves advisor
# attention — NOT advice, a recommendation, suitability, compliance, or a required
# action. Each composes an existing authoritative cadence read; none performs
# coverage/tax/allocation/suitability analysis or infers anything. All are
# category "opportunity", PolicyGate.NONE, deterministic confidence 1.0, Medium
# priority (proactive attention — severity is never invented for an opportunity).
# --------------------------------------------------------------------------- #


def _portfolio_review_opportunity_producer(ctx: SignalContext) -> list[Signal]:
    """Portfolio review approaching — from the authoritative wealth cadence read
    (``portfolio.accounts_review_approaching``, disjoint from the D.5B overdue
    signal). Review math is NOT recreated here."""
    signals: list[Signal] = []
    for acct in accounts_review_approaching(ctx.person_ids, today=ctx.today, limit=200):
        label = acct.get("account_name") or acct.get("account_number") or f"account {acct['id']}"
        signals.append(_emit(
            key="portfolio_review_opportunity", category="opportunity",
            source_record=SourceRecord("account", acct["id"]),
            title=f"Annual portfolio review is due soon — {label}",
            summary="Annual portfolio review is approaching.",
            source_service="portfolio",
            explain_source="portfolio.accounts_review_approaching",
            why="Account last_review_date is within the approaching window of its "
                "annual cadence (not yet overdue), per portfolio.accounts_review_approaching.",
            evidence=_evidence(
                account_id=acct["id"], account=label,
                person_id=acct.get("person_id"), last_review_date=acct.get("last_review_date")),
            severity="opportunity", route=_person_route(acct.get("person_id"))))
    return signals


def _insurance_review_opportunity_producer(ctx: SignalContext) -> list[Signal]:
    """Insurance review due — from the authoritative insurance servicing-review
    cadence (``insurance.reviews_due_for_people``). No coverage/replacement/
    suitability analysis."""
    signals: list[Signal] = []
    for rev in reviews_due_for_people(ctx.person_ids, limit=200):
        signals.append(_emit(
            key="insurance_review_opportunity", category="opportunity",
            source_record=SourceRecord("insurance_review", rev["id"]),
            title="Annual insurance review is due",
            summary="Insurance servicing review is due.",
            source_service="insurance",
            explain_source="insurance.reviews_due_for_people",
            why="An insurance servicing review is open with a due date within the "
                "window, per insurance.reviews_due_for_people.",
            evidence=_evidence(
                insurance_review_id=rev["id"], review_type=rev.get("review_type"),
                status=rev.get("status"), due_date=rev.get("due_date"),
                person_id=rev.get("person_id")),
            severity="opportunity", route=_person_route(rev.get("person_id")),
            status=rev.get("status") or "open"))
    return signals


def _beneficiary_review_opportunity_producer(ctx: SignalContext) -> list[Signal]:
    """Beneficiary review — from the authoritative wealth predicate for a missing
    *required* designation (``portfolio.accounts_missing_required_beneficiary``:
    IRA account with no active beneficiary). Missing beneficiaries are never
    inferred beyond that explicit predicate."""
    signals: list[Signal] = []
    for acct in accounts_missing_required_beneficiary(ctx.person_ids, limit=200):
        label = acct.get("account_name") or acct.get("account_number") or f"account {acct['id']}"
        signals.append(_emit(
            key="beneficiary_review_opportunity", category="opportunity",
            source_record=SourceRecord("account", acct["id"]),
            title=f"Beneficiary information is missing — {label}",
            summary="Beneficiary information is missing on a retirement account.",
            source_service="portfolio",
            explain_source="portfolio.accounts_missing_required_beneficiary",
            why="An IRA account has no active beneficiary designation, per "
                "portfolio.accounts_missing_required_beneficiary.",
            evidence=_evidence(
                account_id=acct["id"], account=label,
                person_id=acct.get("person_id"),
                registration_type=acct.get("registration_type"),
                active_beneficiaries=0),
            severity="opportunity", route=_person_route(acct.get("person_id"))))
    return signals


# --------------------------------------------------------------------------- #
# Compliance-governed advisor RECOMMENDATION producers (Phase D.5D).
#
# A recommendation is advisor-facing and informational — "X may be appropriate
# based on ...". It is NOT a client communication, automated advice, an automated
# decision, workflow execution, or a compliance/suitability determination. Each
# originates from a REGISTERED deterministic rule and carries immutable governance
# metadata (governing rule, version, compliance owner, approval status) plus a
# policy gate. Gates and approval status are DISPLAY-ONLY — nothing is enforced,
# blocked, executed, or persisted. Only recommendation types backed by an existing
# deterministic rule are implemented; tax-planning and retirement-plan
# recommendations are deferred (no authoritative cadence rule exists).
# --------------------------------------------------------------------------- #

#: The role accountable for a policy-gated rule. No individual is assigned yet — the
#: gate is display-only until a compliance owner exists (V1_RISK_REGISTER GOV-2 /
#: PRODUCT_DECISIONS PD-4). Named honestly rather than fabricated.
_COMPLIANCE_OWNER_ROLE = "compliance_reviewer"
_COMPLIANCE_OWNER_UNASSIGNED = f"{_COMPLIANCE_OWNER_ROLE} (unassigned — GOV-2/PD-4)"
_OPERATIONS_OWNER = "advisor_operations"

# Approval status values (informational only).
_APPROVED = "approved"
_PENDING = "pending_compliance_review"


def _recommendation(*, key, rec_type, governing_rule, rule_version, compliance_owner,
                    approval_status, policy_gate, source_record, title, summary, why,
                    source_service, evidence, route) -> Signal:
    """Build one advisor recommendation Signal (through the shared ``_emit``
    builder) plus its immutable governance metadata. A recommendation records the
    same read name in the explainability as the short source service."""
    return _emit(
        key=key, category="recommendation", source_record=source_record,
        title=title, summary=summary, source_service=source_service,
        explain_source=source_service, why=why, evidence=evidence,
        severity="recommendation", priority=Priority.MEDIUM, policy_gate=policy_gate,
        route=route, status="open",
        recommendation=RecommendationMeta(
            recommendation_type=rec_type, governing_rule=governing_rule,
            rule_version=rule_version, compliance_owner=compliance_owner,
            approval_status=approval_status, created_from_rule=key))


def _annual_portfolio_review_recommendation_producer(ctx: SignalContext) -> list[Signal]:
    """Annual portfolio review may be appropriate — governed by the portfolio
    review-cadence rule (``portfolio.accounts_review_approaching``)."""
    out: list[Signal] = []
    for acct in accounts_review_approaching(ctx.person_ids, today=ctx.today, limit=200):
        label = acct.get("account_name") or acct.get("account_number") or f"account {acct['id']}"
        evidence = _evidence(
            account_id=acct["id"], person_id=acct.get("person_id"),
            last_review_date=acct.get("last_review_date"),
            governing_rule="RULE-PORTFOLIO-REVIEW-CADENCE", rule_version="1.0.0")
        out.append(_recommendation(
            key="annual_portfolio_review_recommendation", rec_type="annual_portfolio_review",
            governing_rule="RULE-PORTFOLIO-REVIEW-CADENCE", rule_version="1.0.0",
            compliance_owner=_OPERATIONS_OWNER, approval_status=_APPROVED,
            policy_gate=PolicyGate.NONE, source_record=SourceRecord("account", acct["id"]),
            title=f"Annual portfolio review — {label}",
            summary="Annual portfolio review may be appropriate based on the client's review cadence.",
            why="Account last_review_date is within the annual review cadence window per "
                "RULE-PORTFOLIO-REVIEW-CADENCE (portfolio.accounts_review_approaching).",
            source_service="portfolio", evidence=evidence,
            route=_person_route(acct.get("person_id"))))
    return out


def _insurance_review_recommendation_producer(ctx: SignalContext) -> list[Signal]:
    """Annual insurance review may be appropriate — governed by the insurance
    review-cadence rule (``insurance.reviews_due_for_people``). License-gated
    (display only)."""
    out: list[Signal] = []
    for rev in reviews_due_for_people(ctx.person_ids, limit=200):
        evidence = _evidence(
            insurance_review_id=rev["id"], person_id=rev.get("person_id"),
            due_date=rev.get("due_date"),
            governing_rule="RULE-INSURANCE-REVIEW-CADENCE", rule_version="1.0.0")
        out.append(_recommendation(
            key="insurance_review_recommendation", rec_type="insurance_review",
            governing_rule="RULE-INSURANCE-REVIEW-CADENCE", rule_version="1.0.0",
            compliance_owner=_COMPLIANCE_OWNER_UNASSIGNED, approval_status=_PENDING,
            policy_gate=PolicyGate.LICENSE_REQUIRED,
            source_record=SourceRecord("insurance_review", rev["id"]),
            title="Annual insurance review",
            summary="Annual insurance review may be appropriate based on the client's review cadence.",
            why="An insurance servicing review is open and due within the cadence window per "
                "RULE-INSURANCE-REVIEW-CADENCE (insurance.reviews_due_for_people).",
            source_service="insurance", evidence=evidence,
            route=_person_route(rev.get("person_id"))))
    return out


def _beneficiary_review_recommendation_producer(ctx: SignalContext) -> list[Signal]:
    """Beneficiary review may be appropriate — governed by the required-beneficiary
    rule (``portfolio.accounts_missing_required_beneficiary``). Compliance-gated
    (display only)."""
    out: list[Signal] = []
    for acct in accounts_missing_required_beneficiary(ctx.person_ids, limit=200):
        label = acct.get("account_name") or acct.get("account_number") or f"account {acct['id']}"
        evidence = _evidence(
            account_id=acct["id"], person_id=acct.get("person_id"),
            registration_type=acct.get("registration_type"), active_beneficiaries=0,
            governing_rule="RULE-BENEFICIARY-DESIGNATION-PRESENT", rule_version="1.0.0")
        out.append(_recommendation(
            key="beneficiary_review_recommendation", rec_type="beneficiary_review",
            governing_rule="RULE-BENEFICIARY-DESIGNATION-PRESENT", rule_version="1.0.0",
            compliance_owner=_COMPLIANCE_OWNER_UNASSIGNED, approval_status=_PENDING,
            policy_gate=PolicyGate.COMPLIANCE_REQUIRED,
            source_record=SourceRecord("account", acct["id"]),
            title=f"Beneficiary review — {label}",
            summary="Beneficiary review may be appropriate because required beneficiary "
                    "information is absent.",
            why="An IRA account has no active beneficiary designation per "
                "RULE-BENEFICIARY-DESIGNATION-PRESENT "
                "(portfolio.accounts_missing_required_beneficiary).",
            source_service="portfolio", evidence=evidence,
            route=_person_route(acct.get("person_id"))))
    return out


#: The approved Phase D.5D governed recommendation producers. Each registers its
#: governing rule id, version, compliance owner, and approval status.
_RECOMMENDATION_SIGNALS = (
    (dict(key="annual_portfolio_review_recommendation", category="recommendation",
          source_service="portfolio", default_priority=Priority.MEDIUM,
          policy_gate=PolicyGate.NONE, governing_rule="RULE-PORTFOLIO-REVIEW-CADENCE",
          rule_version="1.0.0", compliance_owner=_OPERATIONS_OWNER, approval_status=_APPROVED,
          description="Annual portfolio review may be appropriate per the review-cadence rule."),
     _annual_portfolio_review_recommendation_producer),
    (dict(key="insurance_review_recommendation", category="recommendation",
          source_service="insurance", default_priority=Priority.MEDIUM,
          policy_gate=PolicyGate.LICENSE_REQUIRED, governing_rule="RULE-INSURANCE-REVIEW-CADENCE",
          rule_version="1.0.0", compliance_owner=_COMPLIANCE_OWNER_UNASSIGNED, approval_status=_PENDING,
          description="Annual insurance review may be appropriate per the review-cadence rule."),
     _insurance_review_recommendation_producer),
    (dict(key="beneficiary_review_recommendation", category="recommendation",
          source_service="portfolio", default_priority=Priority.MEDIUM,
          policy_gate=PolicyGate.COMPLIANCE_REQUIRED, governing_rule="RULE-BENEFICIARY-DESIGNATION-PRESENT",
          rule_version="1.0.0", compliance_owner=_COMPLIANCE_OWNER_UNASSIGNED, approval_status=_PENDING,
          description="Beneficiary review may be appropriate per the required-designation rule."),
     _beneficiary_review_recommendation_producer),
)


#: The approved Phase D.5B operational producers, in registry order. Each entry is
#: (registry metadata, producer callable). Registered/attached at import.
_OPERATIONAL_SIGNALS = (
    (dict(key="client_review_overdue", category="review", source_service="portfolio",
          default_priority=Priority.MEDIUM, policy_gate=PolicyGate.NONE,
          description="Account review is overdue per portfolio.accounts_due_for_review."),
     _review_overdue_producer),
    (dict(key="open_client_exception", category="exception", source_service="exception_engine",
          default_priority=Priority.MEDIUM, policy_gate=PolicyGate.NONE,
          description="Client exception remains open per the Exception Engine."),
     _open_exception_producer),
    (dict(key="overdue_open_task", category="task", source_service="tasks",
          default_priority=Priority.MEDIUM, policy_gate=PolicyGate.NONE,
          description="Open task is past its due date per tasks.open_tasks_for_people."),
     _overdue_task_producer),
    (dict(key="upcoming_client_meeting", category="meeting", source_service="timeline",
          default_priority=Priority.MEDIUM, policy_gate=PolicyGate.NONE,
          description="Client calendar meeting falls within the preparation window."),
     _upcoming_meeting_producer),
)

#: The approved Phase D.5C advisor-opportunity producers (category "opportunity").
#: Tax-planning and retirement-plan opportunities are intentionally NOT here: no
#: authoritative review-cadence read exists for them (only active-count summaries),
#: so implementing them would require inventing review logic — out of scope.
_OPPORTUNITY_SIGNALS = (
    (dict(key="portfolio_review_opportunity", category="opportunity", source_service="portfolio",
          default_priority=Priority.MEDIUM, policy_gate=PolicyGate.NONE,
          description="Annual portfolio review is approaching per portfolio.accounts_review_approaching."),
     _portfolio_review_opportunity_producer),
    (dict(key="insurance_review_opportunity", category="opportunity", source_service="insurance",
          default_priority=Priority.MEDIUM, policy_gate=PolicyGate.NONE,
          description="Insurance servicing review is due per insurance.reviews_due_for_people."),
     _insurance_review_opportunity_producer),
    (dict(key="beneficiary_review_opportunity", category="opportunity", source_service="portfolio",
          default_priority=Priority.MEDIUM, policy_gate=PolicyGate.NONE,
          description="IRA account has no active beneficiary per portfolio.accounts_missing_required_beneficiary."),
     _beneficiary_review_opportunity_producer),
)

#: The single unified rule set — one model for all three families (operational
#: signals D.5B, opportunities D.5C, governed recommendations D.5D). Each entry is
#: ``(registry metadata, deterministic producer)``; operational/opportunity rules
#: simply omit the governance fields. Registered/attached at import.
_RULES = _OPERATIONAL_SIGNALS + _OPPORTUNITY_SIGNALS + _RECOMMENDATION_SIGNALS


def register_operational_signals() -> None:
    """Register every rule's metadata and attach its producer to the shared seam.
    Idempotent: skips any already-registered key so repeated imports/registration
    are safe. One loop over the unified ``_RULES`` — no per-category branching."""
    for meta, producer in _RULES:
        if meta["key"] not in _REGISTRY:
            register_signal(**meta)
        if producer not in _PRODUCERS:
            _PRODUCERS.append(producer)


register_operational_signals()
