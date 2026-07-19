"""Notification retry orchestration — decision layer (F5.7 / Epic 5, ADR-017).

Decides **whether** a still-``pending`` notification is eligible for another delivery
attempt and, relatively, **how long** to wait — derived **exclusively** from the immutable
F5.5 delivery-attempt history plus the notification's disposition. It is a pure, timeless
decision function.

It is **not** responsible for dispatch (F5.5), initial-dispatch selection (F5.6), provider
communication, notification creation (F5.4), or eligibility/consent (F5.3). It performs no
I/O beyond reading, holds no wall-clock, returns no absolute timestamp, schedules nothing,
and mutates nothing — no lifecycle state, no evidence/audit, and **no** retry counter or
summary field on notification rows (``completed_attempts`` is always *computed* from the
immutable attempt history).

A future scheduler is responsible for combining a :class:`RetryDecision`'s **relative**
``retry_delay`` with wall-clock time (``last_attempt_completed_at + retry_delay <= now?``).

Decision model (evaluated in order):
  1. terminal disposition (delivered/failed/suppressed/disabled/dead) -> TERMINAL_DISPOSITION
  2. pending, completed_attempts == 0                                 -> NOT_APPLICABLE_NO_ATTEMPTS
  3. pending, latest attempt does not recommend retry                -> NON_RETRYABLE_FAILURE
  4. pending, completed_attempts >= policy.max_attempts              -> EXHAUSTED
  5. pending, transient latest & completed_attempts < max_attempts   -> RETRYABLE_TRANSIENT
        retry_ordinal        = completed_attempts
        next_attempt_number  = completed_attempts + 1
        retry_delay          = policy.delay_for_retry(retry_ordinal)

Retryability derives from the normalized immutable ``retry_recommended`` field on the latest
attempt row (F5.5) — never inferred from provider outcome text.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import timedelta

from app.services import notification_dispatch as dispatch
from app.services import notifications as ledger

# --- closed retry-reason vocabulary ------------------------------------------

class RetryReason(enum.Enum):
    """The closed domain vocabulary for a retry decision (never free-form strings)."""

    TERMINAL_DISPOSITION = "terminal_disposition"
    NOT_APPLICABLE_NO_ATTEMPTS = "not_applicable_no_attempts"
    NON_RETRYABLE_FAILURE = "non_retryable_failure"
    RETRYABLE_TRANSIENT = "retryable_transient"
    EXHAUSTED = "exhausted"


# --- immutable retry policy --------------------------------------------------

@dataclass(frozen=True)
class RetryPolicy:
    """An **immutable**, versioned retry policy. Delay values live here — never in the
    decision engine. ``max_attempts`` is the maximum **total** provider delivery attempts,
    *including* the initial attempt, so a policy needs exactly ``max_attempts - 1`` retry
    delays (one per retry ordinal ``1 .. max_attempts-1``). Validated at construction.
    """

    policy_id: str = "default.v1"
    max_attempts: int = 4
    #: relative delays for retry ordinals 1..max_attempts-1 (retry_delays[0] is the first retry).
    retry_delays: tuple[timedelta, ...] = field(
        default=(timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))
    )

    def __post_init__(self):
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if len(self.retry_delays) != self.max_attempts - 1:
            raise ValueError(
                f"policy {self.policy_id!r} must define exactly max_attempts-1 "
                f"({self.max_attempts - 1}) retry delays, got {len(self.retry_delays)}"
            )
        if any(d < timedelta(0) for d in self.retry_delays):
            raise ValueError("retry delays must be non-negative")

    def delay_for_retry(self, retry_ordinal: int) -> timedelta:
        """Relative delay before the retry with the given ordinal (1 = first retry)."""
        if not (1 <= retry_ordinal <= self.max_attempts - 1):
            raise ValueError(
                f"retry_ordinal {retry_ordinal} out of range 1..{self.max_attempts - 1}"
            )
        return self.retry_delays[retry_ordinal - 1]


def default_retry_policy() -> RetryPolicy:
    """The versioned default policy (delay values are provisional but confined here)."""
    return RetryPolicy()


# --- immutable retry decision ------------------------------------------------

@dataclass(frozen=True)
class RetryDecision:
    """An **immutable**, content-free, **relative** retry decision. Carries no absolute
    timestamp and no wall-clock — a future scheduler combines ``retry_delay`` with time."""

    eligible: bool
    completed_attempts: int
    retry_ordinal: int | None        # None unless eligible
    next_attempt_number: int | None  # None unless eligible; == completed_attempts + 1
    max_attempts: int
    retry_delay: timedelta | None    # relative; None unless eligible
    reason: RetryReason              # closed enum
    policy_id: str

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible, "completed_attempts": self.completed_attempts,
            "retry_ordinal": self.retry_ordinal, "next_attempt_number": self.next_attempt_number,
            "max_attempts": self.max_attempts,
            "retry_delay_seconds": (self.retry_delay.total_seconds() if self.retry_delay is not None else None),
            "reason": self.reason.value, "policy_id": self.policy_id,
        }


# --- the decision function (read-only, timeless, deterministic) --------------

def evaluate_retry(*, notification_uid: str | None = None, notification_id: int | None = None,
                   policy: RetryPolicy | None = None, conn=None) -> RetryDecision:
    """Return the :class:`RetryDecision` for one notification, derived only from its
    disposition + immutable delivery-attempt history. Read-only; accepts no ``now``; returns
    no absolute timestamp; writes nothing.
    """
    policy = policy or default_retry_policy()
    rec = ledger.get_notification(notification_uid=notification_uid, notification_id=notification_id, conn=conn)
    if rec is None:
        raise ValueError("notification not found")
    attempts = dispatch.delivery_attempts(rec.id, conn=conn)
    completed = len(attempts)

    def _mk(eligible, reason, *, retry_ordinal=None, next_attempt_number=None, retry_delay=None):
        return RetryDecision(
            eligible=eligible, completed_attempts=completed, retry_ordinal=retry_ordinal,
            next_attempt_number=next_attempt_number, max_attempts=policy.max_attempts,
            retry_delay=retry_delay, reason=reason, policy_id=policy.policy_id,
        )

    # 1. terminal disposition (pending is the only non-terminal status).
    if rec.status != ledger.PENDING:
        return _mk(False, RetryReason.TERMINAL_DISPOSITION)
    # 2. zero attempts -> an initial-dispatch candidate owned by F5.6, not a retry.
    if completed == 0:
        return _mk(False, RetryReason.NOT_APPLICABLE_NO_ATTEMPTS)
    # 3. latest attempt must explicitly recommend retry (normalized F5.5 field).
    if not attempts[-1].get("retry_recommended"):
        return _mk(False, RetryReason.NON_RETRYABLE_FAILURE)
    # 4. attempt cap reached (max_attempts includes the initial attempt).
    if completed >= policy.max_attempts:
        return _mk(False, RetryReason.EXHAUSTED)
    # 5. eligible transient retry.
    retry_ordinal = completed
    return _mk(True, RetryReason.RETRYABLE_TRANSIENT, retry_ordinal=retry_ordinal,
               next_attempt_number=completed + 1, retry_delay=policy.delay_for_retry(retry_ordinal))


# --- content-free aggregation (pure; no DB sweep) ----------------------------

def summarize(decisions) -> dict:
    """Content-free counts over a list of :class:`RetryDecision` (no per-notification data)."""
    summary = {"inspected": 0, "retry_eligible": 0, "exhausted": 0, "not_retryable": 0}
    for d in decisions:
        summary["inspected"] += 1
        if d.eligible:
            summary["retry_eligible"] += 1
        elif d.reason is RetryReason.EXHAUSTED:
            summary["exhausted"] += 1
        else:
            summary["not_retryable"] += 1
    return summary
