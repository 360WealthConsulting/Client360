"""F5.7 / Epic 5 — Notification retry orchestration (decision layer) tests (ADR-017).

Covers the immutable RetryPolicy (validation, delay indexing) and RetryDecision (relative,
timeless), the ordered decision model (terminal / no-attempts / non-retryable / exhausted /
retryable-transient), retryability derived from the normalized retry_recommended field,
computed completed_attempts (no notification counter), determinism, content-free output, and
the read-only / no-mutation / no-migration contract.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import engine
from app.services import notification_dispatch as dispatch
from app.services import notifications as ledger
from app.services.notification_providers import FAILURE_ERROR, FAILURE_UNAVAILABLE, DeliveryResult
from app.services.notification_retry import (
    RetryDecision,
    RetryPolicy,
    RetryReason,
    default_retry_policy,
    evaluate_retry,
    summarize,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pending(channel="email"):
    return ledger.record_notification(
        notification_type="workflow.approval.requested", recipient_type="user",
        recipient_ref=f"user:{uuid.uuid4().hex[:12]}", channel=channel,
        title="template:workflow.approval.requested", body=None, status=ledger.PENDING,
        dedupe_key=f"f5.7-test:{uuid.uuid4().hex}",
        metadata={"correlation_id": "workflow_instance:1", "causation_id": None})


class _FakeProvider:
    def __init__(self, channel, result):
        self.identifier, self.channel, self._result = f"fake-{channel}", channel, result

    def deliver_result(self, *, recipient, title, body=None, metadata=None):
        return self._result


class _FakeRegistry:
    def __init__(self, ch, result):
        self._p = {ch: _FakeProvider(ch, result)}

    def __contains__(self, ch):
        return ch in self._p

    def get(self, ch):
        return self._p[ch]


def _transient_reg(ch="email"):
    return _FakeRegistry(ch, DeliveryResult(outcome="failed", channel=ch, delivered=False,
                                            failure_class=FAILURE_UNAVAILABLE, description="unavailable"))


def _dispatch_transient(rec, times):
    reg = _transient_reg("email")
    for _ in range(times):
        dispatch.dispatch_notification(rec.notification_uid, registry=reg)


# --- RetryPolicy (immutable) -------------------------------------------------

def test_default_policy_shape_and_immutability():
    p = default_retry_policy()
    assert p.policy_id == "default.v1" and p.max_attempts == 4
    assert len(p.retry_delays) == p.max_attempts - 1  # 3 retry delays for 4 total attempts
    with pytest.raises(Exception):  # frozen
        p.max_attempts = 9


def test_policy_validates_delay_schedule_at_construction():
    # must define exactly max_attempts-1 delays
    with pytest.raises(ValueError):
        RetryPolicy(policy_id="bad", max_attempts=4, retry_delays=(timedelta(seconds=1),))
    ok = RetryPolicy(policy_id="ok.v1", max_attempts=3,
                     retry_delays=(timedelta(seconds=5), timedelta(seconds=50)))
    assert ok.max_attempts == 3


def test_delay_for_retry_indexing_no_off_by_one():
    p = RetryPolicy(policy_id="p", max_attempts=4,
                    retry_delays=(timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10)))
    assert p.delay_for_retry(1) == timedelta(seconds=30)   # first retry
    assert p.delay_for_retry(2) == timedelta(minutes=2)
    assert p.delay_for_retry(3) == timedelta(minutes=10)   # last permitted retry (attempt 4)
    for bad in (0, 4):
        with pytest.raises(ValueError):
            p.delay_for_retry(bad)


# --- decision model ----------------------------------------------------------

def test_terminal_disposition_not_eligible():
    rec = _pending("in_app")
    from app.services.notification_providers import default_registry
    dispatch.dispatch_notification(rec.notification_uid, registry=default_registry())  # -> delivered
    d = evaluate_retry(notification_uid=rec.notification_uid)
    assert d.eligible is False and d.reason is RetryReason.TERMINAL_DISPOSITION
    assert d.retry_delay is None and d.retry_ordinal is None


def test_pending_zero_attempts_is_not_a_retry():
    rec = _pending("email")
    d = evaluate_retry(notification_uid=rec.notification_uid)
    assert d.eligible is False and d.reason is RetryReason.NOT_APPLICABLE_NO_ATTEMPTS
    assert d.completed_attempts == 0 and d.retry_delay is None
    assert d.retry_ordinal is None and d.next_attempt_number is None


def test_transient_attempt_is_retryable_with_correct_indexing():
    rec = _pending("email")
    _dispatch_transient(rec, 1)  # attempt 1 fails transiently -> completed_attempts = 1
    d = evaluate_retry(notification_uid=rec.notification_uid)
    assert d.eligible is True and d.reason is RetryReason.RETRYABLE_TRANSIENT
    assert d.completed_attempts == 1
    assert d.retry_ordinal == 1 and d.next_attempt_number == 2   # first retry, next attempt #2
    assert d.retry_delay == timedelta(seconds=30) and d.max_attempts == 4


def test_second_retry_indexing():
    rec = _pending("email")
    _dispatch_transient(rec, 2)  # completed_attempts = 2
    d = evaluate_retry(notification_uid=rec.notification_uid)
    assert d.retry_ordinal == 2 and d.next_attempt_number == 3
    assert d.retry_delay == timedelta(minutes=2)


def test_exhausted_after_max_attempts():
    rec = _pending("email")
    _dispatch_transient(rec, 4)  # completed_attempts = 4 == max_attempts
    d = evaluate_retry(notification_uid=rec.notification_uid)
    assert d.eligible is False and d.reason is RetryReason.EXHAUSTED
    assert d.completed_attempts == 4 and d.retry_delay is None


def test_non_retryable_latest_attempt():
    # A pending notification whose latest immutable attempt does NOT recommend retry
    # (defensive branch; constructed by appending such an attempt directly).
    rec = _pending("email")
    t = dispatch._attempts_table()
    with engine.begin() as c:
        c.execute(t.insert().values(
            attempt_uid=str(uuid.uuid4()), notification_id=rec.id, notification_uid=rec.notification_uid,
            attempt_seq=1, provider="fake-email", channel="email",
            execution_result="failed", provider_status="failed", retry_recommended=False,
            failure_class=FAILURE_ERROR))
    d = evaluate_retry(notification_uid=rec.notification_uid)
    assert d.eligible is False and d.reason is RetryReason.NON_RETRYABLE_FAILURE


# --- retryability source, determinism, injectable policy ---------------------

def test_retryability_derives_from_retry_recommended_field():
    rec = _pending("email")
    _dispatch_transient(rec, 1)
    att = dispatch.delivery_attempts(rec.id)[-1]
    assert att["retry_recommended"] is True  # the normalized field drives the decision
    assert evaluate_retry(notification_uid=rec.notification_uid).eligible is True


def test_deterministic_and_policy_injectable():
    rec = _pending("email")
    _dispatch_transient(rec, 1)
    d1 = evaluate_retry(notification_uid=rec.notification_uid)
    d2 = evaluate_retry(notification_uid=rec.notification_uid)
    assert d1 == d2  # same (history, policy) -> same decision; no wall-clock
    custom = RetryPolicy(policy_id="cust.v1", max_attempts=2, retry_delays=(timedelta(seconds=5),))
    d3 = evaluate_retry(notification_uid=rec.notification_uid, policy=custom)
    assert d3.max_attempts == 2 and d3.policy_id == "cust.v1" and d3.retry_delay == timedelta(seconds=5)


# --- pure ledger: no mutation, computed count, content-free ------------------

def test_evaluate_is_read_only_and_count_is_computed():
    rec = _pending("email")
    _dispatch_transient(rec, 1)
    before_attempts = len(dispatch.delivery_attempts(rec.id))
    before = ledger.get_notification(notification_uid=rec.notification_uid)
    with engine.connect() as c:
        n_before = c.execute(select(func.count()).select_from(dispatch._attempts_table())
                             .where(dispatch._attempts_table().c.notification_id == rec.id)).scalar_one()
    d = evaluate_retry(notification_uid=rec.notification_uid)
    after = ledger.get_notification(notification_uid=rec.notification_uid)
    # completed_attempts is COMPUTED from the attempt rows; the notification row is unchanged
    assert d.completed_attempts == before_attempts == n_before
    assert after.status == before.status == ledger.PENDING
    assert after.attempts == 0 and after.last_error is None and after.updated_at is None


def test_decision_is_immutable_and_content_free():
    rec = _pending("email")
    _dispatch_transient(rec, 1)
    d = evaluate_retry(notification_uid=rec.notification_uid)
    assert isinstance(d, RetryDecision)
    with pytest.raises(Exception):  # frozen
        d.eligible = False
    blob = str(d.to_dict())
    assert set(d.to_dict()) == {"eligible", "completed_attempts", "retry_ordinal",
                                "next_attempt_number", "max_attempts", "retry_delay_seconds",
                                "reason", "policy_id"}
    for forbidden in ("user:", "template:", "title", "body", "next_attempt_at", rec.recipient_ref):
        assert forbidden not in blob


def test_no_now_or_absolute_timestamp_accepted():
    import inspect
    sig = inspect.signature(evaluate_retry)
    assert "now" not in sig.parameters
    rec = _pending("email"); _dispatch_transient(rec, 1)
    assert "next_attempt_at" not in evaluate_retry(notification_uid=rec.notification_uid).to_dict()


def test_summarize_is_content_free():
    rec_a = _pending("email"); _dispatch_transient(rec_a, 1)          # retryable
    rec_b = _pending("email"); _dispatch_transient(rec_b, 4)          # exhausted
    rec_c = _pending("email")                                          # no attempts
    ds = [evaluate_retry(notification_uid=r.notification_uid) for r in (rec_a, rec_b, rec_c)]
    s = summarize(ds)
    assert s == {"inspected": 3, "retry_eligible": 1, "exhausted": 1, "not_retryable": 1}


def test_no_scope_violations_in_source():
    source = (REPO_ROOT / "app" / "services" / "notification_retry.py").read_text()
    for forbidden in ("deliver_result", "dispatch_notification(", "record_notification(",
                      "evaluate_delivery", "add_job", "datetime.now", "next_attempt_at",
                      "write_audit_event", "record_evidence", "APIRouter", ".update()", ".insert()"):
        assert forbidden not in source
    assert (REPO_ROOT / "docs" / "NOTIFICATION_RETRY.md").is_file()
