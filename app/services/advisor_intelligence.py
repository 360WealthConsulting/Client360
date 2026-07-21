"""Advisor Intelligence (D.5A framework + D.5B operational signals + D.5C opportunities).

D.5A shipped the deterministic signal INFRASTRUCTURE: the signal model, an
explainability model, a priority model, policy-gate placeholders, a signal
registry, and a thin composition layer
(``get_client_signals`` / ``get_household_signals`` / ``get_dashboard_signals``).

D.5B activated that framework with **factual, deterministic, operational** signals
(client review overdue, open client exception, overdue open task, upcoming client
meeting). D.5C adds **advisor opportunities** — factual, evidence-backed reasons a
client deserves attention (portfolio review approaching, insurance review due,
missing required beneficiary). An opportunity is NOT advice, a recommendation,
suitability, or a required action. Every producer (bottom of this module) composes
an EXISTING authoritative, record-scoped read; none recreates a domain's status/
cadence logic. There is deliberately no recommendation, regulated advice,
probabilistic scoring, policy interpretation, AI/LLM/ML, vector/embedding,
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
        evidence = (
            f"account_id={acct['id']}",
            f"account={acct_label}",
            f"person_id={acct.get('person_id')}",
            f"last_review_date={last}",
            f"stale_days_threshold={_MATERIAL_REVIEW_STALE_DAYS}",
        )
        signals.append(Signal(
            id=_signal_id("client_review_overdue", "account", acct["id"]),
            category="review",
            title=f"Account review overdue — {acct_label}",
            summary=f"Account review is overdue ({basis}).",
            source_service="portfolio",
            source_record=SourceRecord("account", acct["id"]),
            severity="review_overdue",
            priority=priority,
            evidence=evidence,
            explainability=Explainability(
                why="Account last_review_date is null or older than the review-due "
                    "threshold, per portfolio.accounts_due_for_review.",
                source_service="portfolio.accounts_due_for_review",
                evidence=evidence, confidence=1.0, policy_gate=PolicyGate.NONE),
            policy_gate=PolicyGate.NONE,
            route=_person_route(acct.get("person_id")),
            status="open",
        ))
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
        evidence = (
            f"exception_id={exc['id']}",
            f"domain={exc.get('domain')}",
            f"category={exc.get('category')}",
            f"severity={exc.get('severity')}",
            f"status={exc.get('status')}",
            f"opened_at={exc.get('opened_at')}",
        )
        signals.append(Signal(
            id=_signal_id("open_client_exception", "exception", exc["id"]),
            category="exception",
            title=f"Open exception — {title}",
            summary="Exception remains open.",
            source_service="exception_engine",
            source_record=SourceRecord("exception", exc["id"]),
            severity=severity or "info",
            priority=priority,
            evidence=evidence,
            explainability=Explainability(
                why="Exception status is not resolved/cancelled, per "
                    "exception_engine.open_exceptions_for_people.",
                source_service="exception_engine.open_exceptions_for_people",
                evidence=evidence, confidence=1.0, policy_gate=PolicyGate.NONE),
            policy_gate=PolicyGate.NONE,
            route=_person_route(exc.get("person_id")) or "/exceptions",
            status=exc.get("status") or "open",
        ))
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
        evidence = (
            f"task_id={task['id']}",
            f"title={title}",
            f"due_date={due}",
            f"status={task.get('status')}",
            f"days_overdue={days_overdue}",
        )
        signals.append(Signal(
            id=_signal_id("overdue_open_task", "task", task["id"]),
            category="task",
            title=f"Task overdue — {title}",
            summary=f"Task is overdue by {days_overdue} day(s).",
            source_service="tasks",
            source_record=SourceRecord("task", task["id"]),
            severity="task_overdue",
            priority=priority,
            evidence=evidence,
            explainability=Explainability(
                why="Task due_date is before today and status is open, per "
                    "tasks.open_tasks_for_people.",
                source_service="tasks.open_tasks_for_people",
                evidence=evidence, confidence=1.0, policy_gate=PolicyGate.NONE),
            policy_gate=PolicyGate.NONE,
            route=(f"{_person_route(task.get('person_id'))}?tab=tasks"
                   if task.get("person_id") else "/tasks"),
            status=task.get("status") or "open",
        ))
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
        evidence = (
            f"event_id={ev['id']}",
            f"event_time={when}",
            f"person_id={person_id}",
            "event_type=calendar_event",
        )
        signals.append(Signal(
            id=_signal_id("upcoming_client_meeting", "timeline_event", ev["id"]),
            category="meeting",
            title=ev.get("title") or "Upcoming client meeting",
            summary="Meeting is scheduled within the preparation window.",
            source_service="timeline",
            source_record=SourceRecord("timeline_event", ev["id"]),
            severity="info",
            priority=Priority.MEDIUM,
            evidence=evidence,
            explainability=Explainability(
                why="A calendar_event for this client falls within today through the "
                    "next business day, per timeline.recent_events.",
                source_service="timeline.recent_events",
                evidence=evidence, confidence=1.0, policy_gate=PolicyGate.NONE),
            policy_gate=PolicyGate.NONE,
            route=f"/workspace/meetings/{person_id}?event={ev['id']}",
            status="open",
        ))
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
        evidence = (
            f"account_id={acct['id']}",
            f"account={label}",
            f"person_id={acct.get('person_id')}",
            f"last_review_date={acct.get('last_review_date')}",
        )
        signals.append(Signal(
            id=_signal_id("portfolio_review_opportunity", "account", acct["id"]),
            category="opportunity",
            title=f"Annual portfolio review is due soon — {label}",
            summary="Annual portfolio review is approaching.",
            source_service="portfolio",
            source_record=SourceRecord("account", acct["id"]),
            severity="opportunity",
            priority=Priority.MEDIUM,
            evidence=evidence,
            explainability=Explainability(
                why="Account last_review_date is within the approaching window of its "
                    "annual cadence (not yet overdue), per portfolio.accounts_review_approaching.",
                source_service="portfolio.accounts_review_approaching",
                evidence=evidence, confidence=1.0, policy_gate=PolicyGate.NONE),
            policy_gate=PolicyGate.NONE,
            route=_person_route(acct.get("person_id")),
            status="open",
        ))
    return signals


def _insurance_review_opportunity_producer(ctx: SignalContext) -> list[Signal]:
    """Insurance review due — from the authoritative insurance servicing-review
    cadence (``insurance.reviews_due_for_people``). No coverage/replacement/
    suitability analysis."""
    signals: list[Signal] = []
    for rev in reviews_due_for_people(ctx.person_ids, limit=200):
        evidence = (
            f"insurance_review_id={rev['id']}",
            f"review_type={rev.get('review_type')}",
            f"status={rev.get('status')}",
            f"due_date={rev.get('due_date')}",
            f"person_id={rev.get('person_id')}",
        )
        signals.append(Signal(
            id=_signal_id("insurance_review_opportunity", "insurance_review", rev["id"]),
            category="opportunity",
            title="Annual insurance review is due",
            summary="Insurance servicing review is due.",
            source_service="insurance",
            source_record=SourceRecord("insurance_review", rev["id"]),
            severity="opportunity",
            priority=Priority.MEDIUM,
            evidence=evidence,
            explainability=Explainability(
                why="An insurance servicing review is open with a due date within the "
                    "window, per insurance.reviews_due_for_people.",
                source_service="insurance.reviews_due_for_people",
                evidence=evidence, confidence=1.0, policy_gate=PolicyGate.NONE),
            policy_gate=PolicyGate.NONE,
            route=_person_route(rev.get("person_id")),
            status=rev.get("status") or "open",
        ))
    return signals


def _beneficiary_review_opportunity_producer(ctx: SignalContext) -> list[Signal]:
    """Beneficiary review — from the authoritative wealth predicate for a missing
    *required* designation (``portfolio.accounts_missing_required_beneficiary``:
    IRA account with no active beneficiary). Missing beneficiaries are never
    inferred beyond that explicit predicate."""
    signals: list[Signal] = []
    for acct in accounts_missing_required_beneficiary(ctx.person_ids, limit=200):
        label = acct.get("account_name") or acct.get("account_number") or f"account {acct['id']}"
        evidence = (
            f"account_id={acct['id']}",
            f"account={label}",
            f"person_id={acct.get('person_id')}",
            f"registration_type={acct.get('registration_type')}",
            "active_beneficiaries=0",
        )
        signals.append(Signal(
            id=_signal_id("beneficiary_review_opportunity", "account", acct["id"]),
            category="opportunity",
            title=f"Beneficiary information is missing — {label}",
            summary="Beneficiary information is missing on a retirement account.",
            source_service="portfolio",
            source_record=SourceRecord("account", acct["id"]),
            severity="opportunity",
            priority=Priority.MEDIUM,
            evidence=evidence,
            explainability=Explainability(
                why="An IRA account has no active beneficiary designation, per "
                    "portfolio.accounts_missing_required_beneficiary.",
                source_service="portfolio.accounts_missing_required_beneficiary",
                evidence=evidence, confidence=1.0, policy_gate=PolicyGate.NONE),
            policy_gate=PolicyGate.NONE,
            route=_person_route(acct.get("person_id")),
            status="open",
        ))
    return signals


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

#: Everything registered/attached at import — operational signals (D.5B) plus
#: advisor opportunities (D.5C).
_ALL_SIGNALS = _OPERATIONAL_SIGNALS + _OPPORTUNITY_SIGNALS


def register_operational_signals() -> None:
    """Register the approved signals' metadata and attach their producers to the
    seam. Idempotent: skips any already-registered key so repeated imports/
    registration are safe."""
    for meta, producer in _ALL_SIGNALS:
        if meta["key"] not in _REGISTRY:
            register_signal(**meta)
        if producer not in _PRODUCERS:
            _PRODUCERS.append(producer)


register_operational_signals()
