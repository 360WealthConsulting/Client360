"""Notification dispatch worker (F5.6 / Epic 5, ADR-017).

The **activation** layer. A worker mechanism that locates pending notifications and hands
each, one at a time, to the existing **F5.5** dispatcher. It is a *thin* loop: it adds **no**
dispatch, provider, eligibility, or lifecycle logic — all execution semantics (provider call,
immutable delivery-attempt recording, atomic disposition transition, idempotency) live in
F5.5 and are reused unchanged.

Scope (F5.6): locate → claim → dispatch one at a time → record content-free metrics → exit.
It **does not**: create intents (F5.4), evaluate eligibility/consent (F5.3), retry/back off,
dead-letter, fail over, rate-limit, batch (beyond a bounded single cycle), schedule/register
cron or APScheduler jobs, add startup hooks, wrap multiple notifications in one transaction,
or mutate workflow/domain/business-event/evidence/audit state.

Claim abstraction: the worker acquires work only through :func:`claim_next_pending`, which
returns an immutable :class:`PendingNotificationClaim` value object (or ``None``) — the worker
operates on a *claim*, not a bare integer. Today the claim carries only the notification
references for the oldest ``pending`` notification not already attempted this cycle (the system
is single-instance). The value object exists to keep a **stable worker interface** for a future
scalable claim (see "Future claim implementations" below) that may extend it with lease/priority
fields, without changing the loop.

Cycle-local duplicate suppression: a transient provider failure leaves the notification
``pending`` (F5.5, Model A). To avoid re-dispatching the *same* notification repeatedly within
one cycle, the worker keeps an **in-memory** set of ids already attempted this cycle and
excludes them from the claim. This is **cycle-local only** — not retry scheduling, backoff,
permanent suppression, a lease, or durable state. A later cycle (fresh set) may encounter the
still-``pending`` notification again.

Concurrency: single-instance, matching the outbox dispatcher and F5.5's documented
assumption. F5.5 invokes the provider before its DB claim, so true multi-worker concurrency
could double-*send* (the DB is protected regardless by the unique attempt-sequence constraint
and the conditional pending-only transition). One active worker avoids duplicate external
sends. External delivery is therefore **at-least-once**.

Future claim implementations (documented, NOT implemented here): a durable lease column,
``SELECT ... FOR UPDATE SKIP LOCKED``, Postgres advisory locks, a distributed lock, or a
queue-backed claim would let multiple workers run; each would replace only
:func:`claim_next_pending`, leaving the worker loop untouched.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from sqlalchemy import select

from app.services import notification_dispatch as dispatch
from app.services import notifications as ledger

# PendingNotificationClaim now lives in the neutral contract module (owned by neither F5.6
# nor F5.9), keeping the dependency graph acyclic. Re-exported here so the existing import
# path ``from app.services.notification_worker import PendingNotificationClaim`` still resolves
# to the same class object.
from app.services.notification_claims import PendingNotificationClaim

# F5.9 ready-claim is the default claim (normal top-level import — no cycle, since F5.9 does
# not import this module).
from app.services.notification_ready import claim_next_ready

logger = logging.getLogger("client360.notifications.worker")

__all__ = [
    "PendingNotificationClaim", "DispatchCycleMetrics", "claim_next_pending",
    "run_dispatch_cycle",
]


# --- content-free cycle metrics ----------------------------------------------

@dataclass
class DispatchCycleMetrics:
    """Structured, **content-free** summary of one worker cycle. Counts/timings only —
    never recipient data, subject/body, contact details, provider payloads, or exception
    messages."""

    #: notifications claimed and processed this cycle (== dispatched + worker_errors).
    scanned: int = 0
    #: notifications handed to F5.5 that returned a result (did not raise at the worker level).
    dispatched: int = 0
    #: dispatched with a terminal ``delivered`` disposition.
    delivered: int = 0
    #: dispatched with a terminal ``failed`` disposition.
    failed: int = 0
    #: dispatched with a transient provider outcome (notification left ``pending``).
    transient_failures: int = 0
    #: dispatched but rejected by F5.5 (not pending / no provider) — expected ~0 in steady state.
    rejected: int = 0
    #: claims whose dispatch raised an unexpected error at the worker level (caught, not fatal).
    worker_errors: int = 0
    #: total cycle wall-clock in milliseconds.
    runtime_ms: float = 0.0
    #: cumulative time spent in claim_next_pending this cycle, in milliseconds.
    poll_latency_ms: float = 0.0
    #: True iff the cycle processed no notifications because none were pending (empty queue),
    #: i.e. scanned == 0 with no stop/limit termination.
    idle: bool = False
    #: True iff the cycle ended because a cooperative stop was requested.
    stopped: bool = False
    #: True iff the cycle ended because the configured cycle limit was reached.
    cycle_limit_reached: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# --- the claim abstraction (single-instance; oldest pending, cycle-exclusions) ---

def claim_next_pending(attempted_ids: set[int] | frozenset[int] = frozenset(), *, conn=None) -> PendingNotificationClaim | None:
    """Return a :class:`PendingNotificationClaim` for the next ``pending`` notification to
    dispatch, or ``None`` if none remain (excluding ids already attempted this cycle).

    Single-instance implementation: the oldest ``pending`` notification not in
    ``attempted_ids``. Read-only — it acquires **no** lease/lock and starts **no** write
    transaction, so it introduces no batch-rollback coupling. A future scalable claim
    (lease / ``FOR UPDATE SKIP LOCKED`` / advisory or distributed lock / queue) would replace
    only this function and populate the additional claim fields.
    """
    n = ledger._notifications_table()
    q = select(n.c.id, n.c.notification_uid, n.c.created_at).where(n.c.status == ledger.PENDING)
    if attempted_ids:
        q = q.where(n.c.id.notin_(attempted_ids))
    q = q.order_by(n.c.id).limit(1)

    def _do(c):
        row = c.execute(q).first()
        if row is None:
            return None
        return PendingNotificationClaim(notification_id=row[0], notification_uid=row[1], created_at=row[2])

    if conn is not None:
        return _do(conn)
    from app.db import engine
    with engine.connect() as c:
        return _do(c)


# --- the worker cycle --------------------------------------------------------

_OUTCOME_FIELD = {
    dispatch.DELIVERED: "delivered",
    dispatch.FAILED: "failed",
    dispatch.PROVIDER_UNAVAILABLE: "transient_failures",
    dispatch.REJECTED: "rejected",
}


def run_dispatch_cycle(*, registry=None, cycle_limit: int | None = None,
                       stop: Callable[[], bool] | None = None,
                       claim: Callable[..., int | None] | None = None,
                       dispatch_fn: Callable[..., object] | None = None) -> DispatchCycleMetrics:
    """Run a single worker cycle: claim → dispatch (via F5.5) → repeat, until the queue is
    drained, the cycle limit is reached, or a cooperative stop is requested.

    Worker mechanism only — invoked **explicitly** (no scheduler/cron/startup hook). Each
    notification is dispatched through F5.5 in its **own** transaction; the worker wraps no
    shared transaction. ``registry`` is passed to F5.5. ``cycle_limit`` bounds the number of
    notifications processed this cycle (``None`` = drain). ``stop`` is a cooperative predicate
    checked **between** notifications (never mid-dispatch). ``claim``/``dispatch_fn`` are test
    seams; ``claim`` defaults to the F5.9 :func:`claim_next_ready` (which returns only
    currently-due work), ``dispatch_fn`` to F5.5 ``dispatch_notification``.
    """
    claim = claim or claim_next_ready
    dispatch_fn = dispatch_fn or dispatch.dispatch_notification
    metrics = DispatchCycleMetrics()
    attempted: set[int] = set()  # cycle-local; in-memory; discarded when the cycle ends
    started = time.monotonic()

    while True:
        # cooperative stop is checked BEFORE claiming the next item, so an in-flight dispatch
        # always finishes and no further work is claimed once a stop is requested.
        if stop is not None and stop():
            metrics.stopped = True
            break
        if cycle_limit is not None and metrics.scanned >= cycle_limit:
            metrics.cycle_limit_reached = True
            break

        poll_start = time.monotonic()
        claimed = claim(attempted)
        metrics.poll_latency_ms += (time.monotonic() - poll_start) * 1000.0
        if claimed is None:
            break  # queue drained for this cycle

        nid = claimed.notification_id
        attempted.add(nid)          # mark attempted regardless of outcome (cycle-local)
        metrics.scanned += 1
        try:
            result = dispatch_fn(notification_id=nid, registry=registry)
            metrics.dispatched += 1
            field_name = _OUTCOME_FIELD.get(result.outcome)
            if field_name is not None:
                setattr(metrics, field_name, getattr(metrics, field_name) + 1)
        except Exception as exc:  # worker-level error: count, log content-free, keep going
            metrics.worker_errors += 1
            logger.warning("dispatch worker error", extra={
                "notification_id": nid, "error_class": type(exc).__name__,
            })

    metrics.runtime_ms = (time.monotonic() - started) * 1000.0
    metrics.idle = (metrics.scanned == 0 and not metrics.stopped and not metrics.cycle_limit_reached)
    return metrics
