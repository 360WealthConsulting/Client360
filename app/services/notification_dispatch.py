"""Notification dispatch & delivery attempts (F5.5 / Epic 5, ADR-017).

The canonical **execution** layer. It consumes eligible **pending** notification intents
(created by F5.4) and performs provider dispatch through the **F5.2** provider registry,
recording an **immutable, append-only** delivery-attempt for each try. It is responsible
**only** for dispatch execution and delivery-attempt recording.

It does **not**: create notification intents (F5.4), evaluate preferences/consent or re-run
the F5.3 decision, modify historical intent decisions, promote suppressed/disabled intents,
resurrect events, mutate workflow/domain/business-event/evidence state, emit notification
audit/evidence (F5.6), expose routes/admin (F5.7), or **execute/schedule retries** — it
records retry *metadata* only.

Core rule (ADR-017, Model A): the notification ledger is an **intent/disposition ledger**;
notification ``status`` records only durable **communication dispositions**. F5.5 performs
execution only. Dispatch success or failure never completes a workflow, satisfies an
obligation, or changes domain/evidence/business-event state.

Dispatch policy: only intents whose ledger status is ``pending`` are dispatched.
``suppressed``, ``disabled``, ``delivered``, ``failed`` and ``dead`` are never dispatched.

Status transitions (Model A — the only ones introduced): ``pending → delivered | failed``.
A **transient** provider outcome (provider unavailable, timeout, 429/503, DNS/network, ...)
is recorded **only** as a delivery attempt (with ``retry_recommended``); the notification
**stays ``pending``** — no transient provider condition becomes a notification status, and
no retry is scheduled. Retry timing/scheduling is a future feature outside F5.5.

Concurrency: dispatch is designed to run single-instance (like the outbox dispatcher). The
append-only ``uq_notif_attempt_seq (notification_id, attempt_seq)`` constraint and the
conditional ``WHERE status = 'pending'`` update are the backstops that make a repeated
or racing dispatch idempotent (no duplicate attempt row, no double terminal transition).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Table, func, select

from app.services import notification_providers as providers
from app.services import notifications as ledger

# --- dispatch outcomes -------------------------------------------------------

DELIVERED = "delivered"
FAILED = "failed"
PROVIDER_UNAVAILABLE = "provider_unavailable"
REJECTED = "rejected"  # intent not dispatchable (status != pending, or no provider)
DISPATCH_OUTCOMES: frozenset[str] = frozenset({DELIVERED, FAILED, PROVIDER_UNAVAILABLE, REJECTED})

#: The intent must be in this ledger status to be dispatched.
DISPATCHABLE_STATUS = ledger.PENDING


@dataclass(frozen=True)
class DispatchResult:
    """Structured, content-free result of one dispatch attempt (or rejection)."""

    outcome: str                     # delivered | failed | provider_unavailable | rejected
    notification_uid: str | None
    channel: str | None = None
    provider: str | None = None
    ledger_status: str | None = None            # resulting ledger status
    execution_result: str | None = None         # delivered | failed | provider_unavailable
    attempt_seq: int | None = None
    attempt_uid: str | None = None
    provider_ref: str | None = None
    retry_recommended: bool = False
    failure_class: str | None = None
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome, "notification_uid": self.notification_uid,
            "channel": self.channel, "provider": self.provider, "ledger_status": self.ledger_status,
            "execution_result": self.execution_result, "attempt_seq": self.attempt_seq,
            "attempt_uid": self.attempt_uid, "provider_ref": self.provider_ref,
            "retry_recommended": self.retry_recommended, "failure_class": self.failure_class,
            "description": self.description,
        }


# --- table access (reflection) -----------------------------------------------

def _attempts_table() -> Table:
    from app.db import engine, metadata
    t = metadata.tables.get("notification_delivery_attempts")
    return t if t is not None else Table("notification_delivery_attempts", metadata, autoload_with=engine)


# --- provider-outcome normalization ------------------------------------------

#: Map an F5.2 ``DeliveryResult`` to ``(execution_result, terminal_ledger_status, retry)``.
#: ``terminal_ledger_status`` is ``None`` for a **transient** outcome — the notification stays
#: ``pending`` (Model A: transient provider behavior is attempt-scoped, never a status).
def _normalize(result) -> tuple[str, str | None, bool]:
    if result.outcome == providers.DELIVERED:
        return DELIVERED, ledger.DELIVERED, False
    # A transient provider outage is retry-eligible: recorded only in the attempt; the
    # notification remains pending (no status transition, no retry scheduled).
    if result.failure_class == providers.FAILURE_UNAVAILABLE:
        return PROVIDER_UNAVAILABLE, None, True
    # provider_error / provider_not_configured (disabled) -> hard, terminal failure.
    return FAILED, ledger.FAILED, False


# --- delivery-attempt recording (immutable, append-only) ---------------------

def delivery_attempts(notification_id: int, *, conn=None) -> list[dict]:
    """Read-only append-only attempt history for a notification (oldest first)."""
    t = _attempts_table()

    def _do(c):
        return [dict(r) for r in c.execute(
            select(t).where(t.c.notification_id == notification_id).order_by(t.c.attempt_seq)
        ).mappings().all()]

    return _run(conn, _do)


def _next_attempt_seq(c, notification_id: int) -> int:
    t = _attempts_table()
    current = c.execute(select(func.max(t.c.attempt_seq)).where(t.c.notification_id == notification_id)).scalar()
    return (current or 0) + 1


# --- the dispatch service ----------------------------------------------------

def dispatch_notification(notification_uid: str | None = None, *, notification_id: int | None = None,
                          registry=None, conn=None, now=None) -> DispatchResult:
    """Dispatch a single **pending** notification intent through its F5.2 provider.

    Execution only — never creates intents, evaluates eligibility, or mutates
    workflow/domain/evidence state. Records one immutable delivery attempt and transitions
    the ledger. A non-``pending`` intent is rejected without any provider invocation.
    """
    from datetime import UTC, datetime
    now = now or datetime.now(UTC)
    registry = registry or providers.default_registry()

    def _do(c) -> DispatchResult:
        rec = ledger.get_notification(notification_uid=notification_uid, notification_id=notification_id, conn=c)
        if rec is None:
            return DispatchResult(outcome=REJECTED, notification_uid=notification_uid,
                                  description="notification not found")
        # dispatch policy: only pending intents; suppressed/disabled/delivered/failed/dead
        # are never dispatched (no provider invocation).
        if rec.status != DISPATCHABLE_STATUS:
            return DispatchResult(outcome=REJECTED, notification_uid=rec.notification_uid, channel=rec.channel,
                                  ledger_status=rec.status,
                                  description=f"intent not dispatchable (status={rec.status})")
        if rec.channel not in registry:
            return DispatchResult(outcome=REJECTED, notification_uid=rec.notification_uid, channel=rec.channel,
                                  ledger_status=rec.status, description="no provider registered for channel")

        provider = registry.get(rec.channel)
        meta = rec.notification_metadata or {}
        started = now
        # invoke the provider (F5.2 normalizes exceptions/disabled internally).
        result = provider.deliver_result(
            recipient=rec.recipient_ref, title=rec.title, body=rec.body,
            metadata={"correlation_id": meta.get("correlation_id"), "causation_id": meta.get("causation_id")},
        )
        completed = now
        execution_result, new_status, retry = _normalize(result)

        # 1. append-only delivery attempt (never updated/deleted; unique per seq).
        t = _attempts_table()
        seq = _next_attempt_seq(c, rec.id)
        attempt_uid = str(uuid.uuid4())
        c.execute(t.insert().values(
            attempt_uid=attempt_uid, notification_id=rec.id, notification_uid=rec.notification_uid,
            attempt_seq=seq, provider=provider.identifier, channel=rec.channel,
            execution_started_at=started, execution_completed_at=completed,
            provider_message_id=result.provider_ref, provider_status=result.outcome,
            execution_result=execution_result, retry_recommended=retry, failure_class=result.failure_class,
            correlation_ref=meta.get("correlation_id"), causation_ref=meta.get("causation_id"),
            attempt_metadata={"mapping_id": meta.get("mapping_id"), "source_event_type": meta.get("source_event_type")},
        ))

        # 2. Notification row changes ONLY on a durable disposition change (pure-ledger).
        #    A transient outcome (new_status is None) leaves the notification row COMPLETELY
        #    untouched — no status, no timestamps, and no execution-summary fields (attempts/
        #    last_error/updated_at). The appended attempt above is the sole record. Execution
        #    history is owned exclusively by notification_delivery_attempts.
        if new_status is None:
            resulting_status = ledger.PENDING  # unchanged; row not written
        else:
            n = ledger._notifications_table()
            # Only the disposition + its dedicated timestamp; no execution summaries.
            values = {"status": new_status}
            if new_status == ledger.DELIVERED:
                values["delivered_at"] = completed
            elif new_status == ledger.FAILED:
                values["failed_at"] = completed
            # conditional (idempotent): only transition from pending.
            c.execute(n.update().where(n.c.id == rec.id, n.c.status == DISPATCHABLE_STATUS).values(**values))
            resulting_status = new_status

        return DispatchResult(
            outcome=execution_result, notification_uid=rec.notification_uid, channel=rec.channel,
            provider=provider.identifier, ledger_status=resulting_status, execution_result=execution_result,
            attempt_seq=seq, attempt_uid=attempt_uid, provider_ref=result.provider_ref,
            retry_recommended=retry, failure_class=result.failure_class,
            description=result.description,
        )

    return _run(conn, _do)


def dispatch_pending_notifications(*, limit: int = 100, registry=None, conn=None) -> dict:
    """Dispatch a bounded batch of pending intents. Dispatch execution only — no retry
    scheduling, escalation, or batching beyond a single pass over pending intents."""
    registry = registry or providers.default_registry()
    summary = {DELIVERED: 0, FAILED: 0, PROVIDER_UNAVAILABLE: 0, REJECTED: 0}

    def _do(c):
        n = ledger._notifications_table()
        ids = [r[0] for r in c.execute(
            select(n.c.id).where(n.c.status == DISPATCHABLE_STATUS).order_by(n.c.id).limit(limit)
        ).all()]
        for nid in ids:
            res = dispatch_notification(notification_id=nid, registry=registry, conn=c)
            summary[res.outcome] = summary.get(res.outcome, 0) + 1
        return summary

    return _run(conn, _do)


def _run(conn, fn):
    if conn is not None:
        return fn(conn)
    from app.db import engine
    with engine.begin() as c:
        return fn(c)
