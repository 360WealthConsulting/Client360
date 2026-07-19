"""Ready notification claim (F5.9 / Epic 5, ADR-017).

Returns the next notification currently **ready** for the F5.6 worker to process
(:class:`PendingNotificationClaim`) or ``None``. It closes the F5.8 operational-safety gap:
with :func:`claim_next_ready` as F5.6's default claim, the worker only receives notifications
that are actually due, so a recurring activation cannot re-attempt a transiently-failed
(still-``pending``) notification before its retry delay has elapsed.

Design — three cohesive collaborators (never one large function):
- :class:`CandidateRepository` — owns candidate retrieval, latest-attempt-timestamp retrieval,
  and short-lived read-connection management; returns candidates in a **deterministic** order
  (id ascending today, but that key is not part of the contract); supports attempted-ID
  exclusion and bounded retrieval. Read-only.
- :class:`ReadinessEvaluator` — evaluates one candidate at a time: consults **F5.7** for the
  retry decision, distinguishes initial-dispatch (zero attempts) from retry, applies the
  inclusive retry-timing rule, and **fails closed** on invalid/missing timing. No writes.
- :func:`claim_next_ready` — orchestrates the two: pulls a bounded, deterministically-ordered
  candidate page, asks the evaluator for readiness, returns the first ready candidate (or
  ``None``), and emits content-free diagnostics once per call. One claim per call.

F5.9 owns the wall-clock retry arithmetic F5.7 deferred (injected aware-UTC ``now`` seam;
resolved once at the boundary; naive ``now`` rejected). It is **read-only**: no status/attempt/
counter writes, no lease/lock, no transaction spanning dispatch, no lifecycle states. Ordering
and exclusion mirror F5.6's existing semantics. Single-instance (no locks/leases); multi-worker
support is a later explicit feature. No migration (reuses F5.1/F5.5/F5.7). F5.9 does **not**
import the F5.6 worker module — the shared claim contract lives in the neutral
``notification_claims`` module — so the dependency graph stays acyclic.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.services import notification_dispatch as dispatch
from app.services import notifications as ledger
from app.services.notification_claims import PendingNotificationClaim
from app.services.notification_retry import RetryReason, evaluate_retry

logger = logging.getLogger("client360.notifications.ready")

DEFAULT_SCAN_LIMIT = 100


# --- content-free diagnostics ------------------------------------------------

@dataclass
class ClaimDiagnostics:
    """Content-free counts/flags for one ``claim_next_ready`` call. Never recipient,
    destination, title/body, payload, provider response, or notification-bearing error text."""

    candidates_inspected: int = 0
    zero_attempt_ready: int = 0
    retry_ready: int = 0
    retry_not_due: int = 0
    retry_ineligible: int = 0
    missing_attempt_timestamp: int = 0
    evaluation_errors: int = 0
    scan_bound_reached: bool = False
    claim_returned: bool = False
    no_ready_claim: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# --- readiness verdict (internal) --------------------------------------------

# category constants map 1:1 to a ClaimDiagnostics counter field.
_READY_ZERO_ATTEMPT = "zero_attempt_ready"
_READY_RETRY = "retry_ready"
_RETRY_NOT_DUE = "retry_not_due"
_RETRY_INELIGIBLE = "retry_ineligible"
_MISSING_TIMESTAMP = "missing_attempt_timestamp"
_EVALUATION_ERROR = "evaluation_errors"


@dataclass(frozen=True)
class ReadinessVerdict:
    ready: bool
    category: str


def _is_aware(dt) -> bool:
    return isinstance(dt, datetime) and dt.tzinfo is not None and dt.utcoffset() is not None


# --- candidate repository (persistence; hides the connection) ----------------

class CandidateRepository:
    """Owns candidate retrieval + latest-attempt-timestamp retrieval over one short-lived
    read connection. Read-only; deterministic ordering; bounded; attempted-ID exclusion."""

    def __init__(self, conn):
        self._conn = conn

    def pending_candidates(self, *, exclude, limit: int) -> list[PendingNotificationClaim]:
        """A deterministically-ordered, bounded page of pending candidates, excluding
        already-attempted ids. (Order is id-ascending in this implementation; callers rely
        only on determinism, not the specific key.)"""
        n = ledger._notifications_table()
        q = select(n.c.id, n.c.notification_uid, n.c.created_at).where(n.c.status == ledger.PENDING)
        if exclude:
            q = q.where(n.c.id.notin_(exclude))
        q = q.order_by(n.c.id).limit(limit)
        return [
            PendingNotificationClaim(notification_id=r[0], notification_uid=r[1], created_at=r[2])
            for r in self._conn.execute(q).all()
        ]

    def latest_completed_at(self, notification_id: int):
        """The latest immutable attempt's ``execution_completed_at`` (or ``None`` if there is
        no attempt or no completed-at value)."""
        attempts = dispatch.delivery_attempts(notification_id, conn=self._conn)
        if not attempts:
            return None
        return attempts[-1].get("execution_completed_at")


# --- readiness evaluator (consults F5.7; no writes) --------------------------

class ReadinessEvaluator:
    """Determines whether one candidate is currently ready. Consults F5.7 for the retry
    decision, applies the inclusive retry-timing rule, and fails closed on invalid timing."""

    def __init__(self, repository: CandidateRepository, *, evaluate=evaluate_retry):
        self._repo = repository
        self._evaluate = evaluate

    def is_ready(self, candidate: PendingNotificationClaim, *, now: datetime) -> ReadinessVerdict:
        nid = candidate.notification_id
        try:
            decision = self._evaluate(notification_id=nid, conn=self._repo._conn)
        except Exception as exc:  # candidate-specific F5.7 failure -> fail closed, keep scanning
            logger.warning("ready: retry evaluation failed", extra={
                "notification_id": nid, "error_class": type(exc).__name__})
            return ReadinessVerdict(False, _EVALUATION_ERROR)

        reason = decision.reason
        # initial-dispatch work: pending with no attempts is ready immediately (not a retry).
        if reason is RetryReason.NOT_APPLICABLE_NO_ATTEMPTS:
            return ReadinessVerdict(True, _READY_ZERO_ATTEMPT)
        # retryable transient: ready only when the delay has inclusively elapsed.
        if reason is RetryReason.RETRYABLE_TRANSIENT:
            try:
                completed_at = self._repo.latest_completed_at(nid)
            except Exception as exc:  # candidate-specific read failure -> fail closed
                logger.warning("ready: attempt-timestamp read failed", extra={
                    "notification_id": nid, "error_class": type(exc).__name__})
                return ReadinessVerdict(False, _EVALUATION_ERROR)
            if not _is_aware(completed_at) or decision.retry_delay is None:
                return ReadinessVerdict(False, _MISSING_TIMESTAMP)  # fail closed
            due = completed_at + decision.retry_delay
            return ReadinessVerdict(due <= now, _READY_RETRY if due <= now else _RETRY_NOT_DUE)
        # terminal / exhausted / non-retryable / anything else -> ineligible (no mutation).
        return ReadinessVerdict(False, _RETRY_INELIGIBLE)


# --- clock resolution (boundary only) ----------------------------------------

def _resolve_now(now) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if not _is_aware(now):
        raise ValueError("now must be a timezone-aware datetime")
    return now


# --- the orchestrator --------------------------------------------------------

def claim_next_ready(attempted_ids: set[int] | frozenset[int] = frozenset(), *,
                     now: datetime | None = None, scan_limit: int = DEFAULT_SCAN_LIMIT,
                     observe: Callable[[ClaimDiagnostics], None] | None = None,
                     ) -> PendingNotificationClaim | None:
    """Return the next ready :class:`PendingNotificationClaim`, or ``None`` if none is ready
    within the bounded scan. Read-only, single claim per call. See module docstring."""
    if not isinstance(scan_limit, int) or isinstance(scan_limit, bool) or scan_limit < 1:
        raise ValueError(f"scan_limit must be a positive integer, got {scan_limit!r}")
    now = _resolve_now(now)
    diag = ClaimDiagnostics()

    from app.db import engine
    with engine.connect() as conn:  # one short-lived read connection for this claim call
        repo = CandidateRepository(conn)
        evaluator = ReadinessEvaluator(repo)
        # repository-level retrieval failures propagate (not swallowed) to F5.6's claim path.
        candidates = repo.pending_candidates(exclude=attempted_ids, limit=scan_limit)
        claim = _select_ready(candidates, evaluator, now=now, scan_limit=scan_limit, diag=diag)
    _emit(observe, diag)
    return claim


def _select_ready(candidates, evaluator, *, now, scan_limit, diag: ClaimDiagnostics):
    """Scan a bounded, deterministically-ordered candidate list and return the first ready
    one (or ``None``), tallying content-free diagnostics. A not-ready candidate never blocks a
    later ready one; ``scan_bound_reached`` is True only when the full bound was consumed
    without a ready candidate. One claim per call."""
    for candidate in candidates:
        diag.candidates_inspected += 1
        verdict = evaluator.is_ready(candidate, now=now)
        setattr(diag, verdict.category, getattr(diag, verdict.category) + 1)
        if verdict.ready:
            diag.claim_returned = True
            return candidate
    diag.scan_bound_reached = len(candidates) == scan_limit  # bound fully consumed vs exhausted
    diag.no_ready_claim = True
    return None


def _emit(observe, diagnostics: ClaimDiagnostics) -> None:
    if observe is not None:
        observe(diagnostics)
