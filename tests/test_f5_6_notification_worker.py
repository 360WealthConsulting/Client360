"""F5.6 / Epic 5 — Notification dispatch worker tests (ADR-017).

Covers the worker-only mechanism: the ``claim_next_pending`` abstraction, sequential
single-at-a-time dispatch through the F5.5 dispatcher, cycle limit, cycle-local exclusion of
already-attempted (transiently-failed, still-pending) notifications, terminal notifications
never reclaimed, worker-level error isolation, cooperative shutdown, content-free metrics,
unchanged F5.5 transaction behavior, and the absence of scheduler activation.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.db import engine
from app.services import notification_dispatch as dispatch
from app.services import notifications as ledger
from app.services.notification_providers import (
    FAILURE_UNAVAILABLE,
    DeliveryResult,
    default_registry,
)
from app.services.notification_worker import (
    DispatchCycleMetrics,
    PendingNotificationClaim,
    claim_next_pending,
    run_dispatch_cycle,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pending(channel="in_app"):
    return ledger.record_notification(
        notification_type="workflow.approval.requested", recipient_type="user",
        recipient_ref=f"user:{uuid.uuid4().hex[:12]}", channel=channel,
        title="template:workflow.approval.requested", body=None, status=ledger.PENDING,
        dedupe_key=f"f5.6-test:{uuid.uuid4().hex}",
        metadata={"correlation_id": "workflow_instance:1", "causation_id": None})


class _FakeProvider:
    def __init__(self, channel, result):
        self.identifier, self.channel, self._result = f"fake-{channel}", channel, result

    def deliver_result(self, *, recipient, title, body=None, metadata=None):
        return self._result


class _FakeRegistry:
    def __init__(self, providers):
        self._p = providers

    def __contains__(self, ch):
        return ch in self._p

    def get(self, ch):
        return self._p[ch]


def _transient_registry(channel="email"):
    r = DeliveryResult(outcome="failed", channel=channel, delivered=False,
                       failure_class=FAILURE_UNAVAILABLE, description=f"{channel} unavailable")
    return _FakeRegistry({channel: _FakeProvider(channel, r)})


# --- claim abstraction -------------------------------------------------------

def test_claim_returns_oldest_pending_excluding_attempted():
    _pending(); _pending()  # ensure >= 2 pending exist
    first = claim_next_pending()
    # a claim value object, not a bare id
    assert isinstance(first, PendingNotificationClaim) and first.notification_id
    assert first.notification_uid is not None and first.created_at is not None
    second = claim_next_pending(attempted_ids={first.notification_id})
    assert second.notification_id != first.notification_id


def test_claim_excludes_non_pending():
    rec = _pending()
    dispatch.dispatch_notification(rec.notification_uid, registry=default_registry())  # -> delivered
    # a delivered notification is never returned by the claim (scoped to this rec to avoid
    # sweeping the accumulated test DB)
    assert _scoped_claim([rec.id])(set()) is None


# --- cycle behavior ----------------------------------------------------------

def test_empty_queue_returns_idle_cycle():
    # a claim that finds no pending work -> idle cycle (no writes, nothing dispatched)
    m = run_dispatch_cycle(registry=default_registry(), claim=lambda attempted, **kw: None)
    assert isinstance(m, DispatchCycleMetrics)
    assert m.scanned == 0 and m.idle is True and m.dispatched == 0
    assert m.stopped is False and m.cycle_limit_reached is False


def test_single_pending_is_dispatched_through_f55():
    rec = _pending("in_app")
    m = run_dispatch_cycle(registry=default_registry(), claim=_scoped_claim([rec.id]))
    assert m.scanned == 1 and m.dispatched == 1 and m.delivered == 1
    assert ledger.get_notification(notification_uid=rec.notification_uid).status == ledger.DELIVERED


def test_multiple_dispatched_sequentially():
    recs = [_pending("in_app") for _ in range(3)]
    m = run_dispatch_cycle(registry=default_registry(), claim=_scoped_claim([r.id for r in recs]))
    assert m.scanned == 3 and m.delivered == 3
    for r in recs:
        assert ledger.get_notification(notification_uid=r.notification_uid).status == ledger.DELIVERED


def test_cycle_limit_is_honored():
    recs = [_pending("in_app") for _ in range(4)]
    m = run_dispatch_cycle(registry=default_registry(), cycle_limit=2, claim=_scoped_claim([r.id for r in recs]))
    assert m.scanned == 2 and m.cycle_limit_reached is True
    delivered = sum(ledger.get_notification(notification_uid=r.notification_uid).status == ledger.DELIVERED for r in recs)
    assert delivered == 2  # only two processed


# --- cycle-local transient exclusion -----------------------------------------

def test_transient_failure_not_reattempted_same_cycle():
    rec = _pending("email")
    reg = _transient_registry("email")
    m = run_dispatch_cycle(registry=reg, claim=_scoped_claim([rec.id]))
    # one attempt only; the still-pending notification is excluded for the rest of the cycle
    assert m.scanned == 1 and m.transient_failures == 1
    assert ledger.get_notification(notification_uid=rec.notification_uid).status == ledger.PENDING
    assert len(dispatch.delivery_attempts(rec.id)) == 1  # not re-dispatched this cycle


def test_transient_notification_seen_again_in_later_cycle():
    rec = _pending("email")
    reg = _transient_registry("email")
    run_dispatch_cycle(registry=reg, claim=_scoped_claim([rec.id]))
    # a fresh cycle (new attempted set) encounters the still-pending notification again
    m2 = run_dispatch_cycle(registry=reg, claim=_scoped_claim([rec.id]))
    assert m2.scanned == 1 and m2.transient_failures == 1
    assert len(dispatch.delivery_attempts(rec.id)) == 2  # a second attempt, later cycle


def test_terminal_notification_not_reclaimed():
    rec = _pending("in_app")
    run_dispatch_cycle(registry=default_registry(), claim=_scoped_claim([rec.id]))  # -> delivered
    # a second cycle over the same scope finds nothing pending
    m2 = run_dispatch_cycle(registry=default_registry(), claim=_scoped_claim([rec.id]))
    assert m2.scanned == 0 and m2.idle is True


# --- error isolation ---------------------------------------------------------

def test_worker_error_counted_and_does_not_stop_cycle():
    recs = [_pending("in_app") for _ in range(3)]
    ids = [r.id for r in recs]
    boom_id = ids[1]

    def _dispatch_fn(*, notification_id, registry=None):
        if notification_id == boom_id:
            raise RuntimeError("boom")
        return dispatch.dispatch_notification(notification_id=notification_id, registry=registry)

    m = run_dispatch_cycle(registry=default_registry(), claim=_scoped_claim(ids), dispatch_fn=_dispatch_fn)
    assert m.scanned == 3 and m.worker_errors == 1 and m.delivered == 2  # others still processed


# --- cooperative shutdown ----------------------------------------------------

def test_cooperative_shutdown_stops_before_next_claim():
    recs = [_pending("in_app") for _ in range(3)]
    ids = [r.id for r in recs]
    calls = {"n": 0}

    def _stop():
        # allow exactly one dispatch, then request stop before the next claim
        calls["n"] += 1
        return calls["n"] > 1

    m = run_dispatch_cycle(registry=default_registry(), claim=_scoped_claim(ids), stop=_stop)
    assert m.stopped is True and m.scanned == 1
    delivered = sum(ledger.get_notification(notification_uid=r.notification_uid).status == ledger.DELIVERED for r in recs)
    assert delivered == 1  # only the in-flight one finished; no further claim


# --- metrics content-free ----------------------------------------------------

def test_metrics_are_content_free():
    rec = _pending("in_app")
    m = run_dispatch_cycle(registry=default_registry(), claim=_scoped_claim([rec.id]))
    d = m.to_dict()
    assert set(d) == {"scanned", "dispatched", "delivered", "failed", "transient_failures",
                      "rejected", "worker_errors", "runtime_ms", "poll_latency_ms",
                      "idle", "stopped", "cycle_limit_reached"}
    blob = str(d)
    for forbidden in ("user:", "template:", "title", "body", rec.recipient_ref):
        assert forbidden not in blob
    assert m.runtime_ms >= 0 and m.poll_latency_ms >= 0


# --- F5.5 transaction behavior unchanged + no scheduler ----------------------

def test_worker_does_not_share_a_transaction_or_touch_audit():
    from app.db import audit_events
    from app.platform.outbox import outbox_events
    recs = [_pending("in_app") for _ in range(2)]
    with engine.connect() as c:
        a_before = c.execute(select(func.count()).select_from(audit_events)).scalar_one()
        o_before = c.execute(select(func.count()).select_from(outbox_events)).scalar_one()
    run_dispatch_cycle(registry=default_registry(), claim=_scoped_claim([r.id for r in recs]))
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(audit_events)).scalar_one() == a_before
        assert c.execute(select(func.count()).select_from(outbox_events)).scalar_one() == o_before
    # each dispatched in its own txn (F5.5): both delivered independently
    for r in recs:
        assert ledger.get_notification(notification_uid=r.notification_uid).status == ledger.DELIVERED


def test_no_scheduler_or_recurring_activation_introduced():
    # check for actual scheduler WIRING (imports/calls), not descriptive prose.
    source = (REPO_ROOT / "app" / "services" / "notification_worker.py").read_text()
    for forbidden in ("add_job(", "import apscheduler", "from apscheduler", "on_event",
                      "start_scheduler(", "trigger=", "CronCreate", "_scheduler."):
        assert forbidden not in source
    # worker is not wired into the scheduler
    sched = (REPO_ROOT / "app" / "jobs" / "scheduler.py").read_text()
    assert "notification_worker" not in sched and "run_dispatch_cycle" not in sched
    assert (REPO_ROOT / "docs" / "NOTIFICATION_WORKER.md").is_file()


# --- helpers -----------------------------------------------------------------

def _scoped_claim(ids):
    """A claim that only ever considers the given notification ids (test isolation from the
    accumulated DB); returns a PendingNotificationClaim like the real claim_next_pending."""
    n = ledger._notifications_table()

    def _claim(attempted, *, conn=None):
        from app.db import engine as _engine
        q = (select(n.c.id, n.c.notification_uid, n.c.created_at)
             .where(n.c.status == ledger.PENDING, n.c.id.in_(ids)).order_by(n.c.id).limit(1))
        if attempted:
            q = q.where(n.c.id.notin_(attempted))
        with _engine.connect() as c:
            row = c.execute(q).first()
        if row is None:
            return None
        return PendingNotificationClaim(notification_id=row[0], notification_uid=row[1], created_at=row[2])

    return _claim
