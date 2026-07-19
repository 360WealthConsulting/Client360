"""F5.5 / Epic 5 — Notification dispatch & delivery-attempt tests (ADR-017).

Covers successful dispatch, provider failure, provider-unavailable (retry-eligible),
immutable append-only delivery-attempt history, duplicate-dispatch prevention / idempotent
execution, rejection of suppressed/disabled/delivered/failed intents, provider-outcome
normalization, retry metadata (recorded, never executed), transaction boundaries, and the
strict scope contract (no intent creation, no eligibility/consent evaluation, no
workflow/domain/business-event/audit/evidence mutation, no content in results/logs).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import engine, households, people
from app.services import notifications as ledger
from app.services.notification_dispatch import (
    DELIVERED,
    FAILED,
    PROVIDER_UNAVAILABLE,
    REJECTED,
    DispatchResult,
    delivery_attempts,
    dispatch_notification,
    dispatch_pending_notifications,
)
from app.services.notification_providers import (
    FAILURE_ERROR,
    FAILURE_UNAVAILABLE,
    DeliveryResult,
    default_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pending(channel="in_app", *, recipient=None, metadata=None):
    """Create a pending intent directly in the F5.1 ledger (dispatch's input)."""
    r = recipient or f"user:{uuid.uuid4().hex[:12]}"
    return ledger.record_notification(
        notification_type="workflow.approval.requested", recipient_type="user", recipient_ref=r,
        channel=channel, title="template:workflow.approval.requested", body=None, status=ledger.PENDING,
        dedupe_key=f"f5.5-test:{uuid.uuid4().hex}",
        metadata=metadata or {"correlation_id": "workflow_instance:1", "causation_id": None},
    )


class _FakeProvider:
    def __init__(self, identifier, channel, result, *, boom=False):
        self.identifier, self.channel, self._result, self._boom = identifier, channel, result, boom
        self.calls = 0

    def deliver_result(self, *, recipient, title, body=None, metadata=None):
        self.calls += 1
        if self._boom:
            raise AssertionError("provider must not be invoked for a rejected intent")
        return self._result


class _FakeRegistry:
    def __init__(self, providers):
        self._p = providers

    def __contains__(self, ch):
        return ch in self._p

    def get(self, ch):
        return self._p[ch]


def _reg(channel, outcome, *, failure_class=None, provider_ref=None, boom=False):
    result = DeliveryResult(outcome=outcome, channel=channel, delivered=(outcome == "delivered"),
                            provider_ref=provider_ref, failure_class=failure_class,
                            description=f"{channel} {outcome}")
    return _FakeRegistry({channel: _FakeProvider(f"fake-{channel}", channel, result, boom=boom)})


# --- successful dispatch (real in_app provider) ------------------------------

def test_successful_dispatch_delivers_and_records_attempt():
    rec = _pending("in_app")
    res = dispatch_notification(rec.notification_uid, registry=default_registry())
    assert isinstance(res, DispatchResult)
    assert res.outcome == DELIVERED and res.execution_result == DELIVERED and res.attempt_seq == 1
    assert res.provider_ref and res.retry_recommended is False
    after = ledger.get_notification(notification_uid=rec.notification_uid)
    # pure-ledger: only the disposition + its timestamp change; no execution summaries on the row.
    assert after.status == ledger.DELIVERED and after.delivered_at is not None
    assert after.attempts == 0 and after.last_error is None  # execution summaries live in attempts
    atts = delivery_attempts(rec.id)
    assert len(atts) == 1 and atts[0]["execution_result"] == "delivered" and atts[0]["provider_status"] == "delivered"


# --- provider failure & unavailable ------------------------------------------

def test_provider_error_becomes_terminal_failed():
    rec = _pending("email")
    reg = _reg("email", "failed", failure_class=FAILURE_ERROR)
    res = dispatch_notification(rec.notification_uid, registry=reg)
    assert res.outcome == FAILED and res.retry_recommended is False
    after = ledger.get_notification(notification_uid=rec.notification_uid)
    assert after.status == ledger.FAILED and after.failed_at is not None
    assert after.last_error is None and after.attempts == 0  # failure detail lives in the attempt row
    att = delivery_attempts(rec.id)[0]
    assert att["execution_result"] == "failed" and att["failure_class"] == FAILURE_ERROR


def test_disabled_provider_outcome_becomes_failed():
    # A disabled provider reports outcome=disabled (not_configured) -> terminal failed at dispatch.
    rec = _pending("email")
    res = dispatch_notification(rec.notification_uid, registry=default_registry())  # email disabled
    assert res.outcome == FAILED
    assert ledger.get_notification(notification_uid=rec.notification_uid).status == ledger.FAILED
    assert delivery_attempts(rec.id)[0]["provider_status"] == "disabled"


def test_transient_provider_failure_leaves_notification_row_untouched():
    # Pure-ledger: a transient provider outcome is attempt-scoped ONLY. The notification row
    # is not written at all — status, timestamps, and execution-summary fields all unchanged.
    rec = _pending("email")
    reg = _reg("email", "failed", failure_class=FAILURE_UNAVAILABLE)
    res = dispatch_notification(rec.notification_uid, registry=reg)
    assert res.outcome == PROVIDER_UNAVAILABLE and res.retry_recommended is True
    after = ledger.get_notification(notification_uid=rec.notification_uid)
    assert after.status == ledger.PENDING            # disposition unchanged
    assert after.attempts == 0                        # no denormalized counter
    assert after.last_error is None                   # no execution summary on the row
    assert after.updated_at is None                   # transient activity did not touch the row
    assert after.delivered_at is None and after.failed_at is None and after.provider_ref is None
    # execution + retry metadata live exclusively in the immutable attempt record
    att = delivery_attempts(rec.id)[0]
    assert att["execution_result"] == "provider_unavailable" and att["retry_recommended"] is True
    assert att["failure_class"] == FAILURE_UNAVAILABLE


# --- rejection of non-pending intents (no provider invocation) ---------------

@pytest.mark.parametrize("status", [ledger.SUPPRESSED, ledger.DISABLED, ledger.DELIVERED, ledger.FAILED])
def test_non_pending_intent_is_rejected_without_provider_call(status):
    rec = ledger.record_notification(
        notification_type="t", recipient_type="user", recipient_ref=f"user:{uuid.uuid4().hex[:10]}",
        channel="in_app", title="template:t", status=status, dedupe_key=f"f5.5-rej:{uuid.uuid4().hex}")
    reg = _reg("in_app", "delivered", boom=True)  # provider raises if invoked
    res = dispatch_notification(rec.notification_uid, registry=reg)
    assert res.outcome == REJECTED and res.ledger_status == status
    assert delivery_attempts(rec.id) == []  # no attempt recorded
    assert ledger.get_notification(notification_uid=rec.notification_uid).status == status  # unchanged


# --- duplicate-dispatch prevention / idempotency -----------------------------

def test_duplicate_dispatch_prevented_after_terminal():
    rec = _pending("in_app")
    first = dispatch_notification(rec.notification_uid, registry=default_registry())
    second = dispatch_notification(rec.notification_uid, registry=default_registry())
    assert first.outcome == DELIVERED and second.outcome == REJECTED
    assert len(delivery_attempts(rec.id)) == 1  # no second attempt


# --- immutable / append-only delivery history --------------------------------

def test_delivery_attempts_are_immutable():
    from sqlalchemy import text
    rec = _pending("in_app")
    dispatch_notification(rec.notification_uid, registry=default_registry())
    with engine.begin() as c:
        with pytest.raises(Exception):
            c.execute(text("UPDATE notification_delivery_attempts SET provider='x' WHERE notification_id=:n"), {"n": rec.id})
    with engine.begin() as c:
        with pytest.raises(Exception):
            c.execute(text("DELETE FROM notification_delivery_attempts WHERE notification_id=:n"), {"n": rec.id})
    assert len(delivery_attempts(rec.id)) == 1  # still present, unmodified


def test_append_only_sequence_across_transient_failures():
    # A transient failure leaves the notification pending (Model A), so a subsequent dispatch
    # naturally appends attempt 2 — proving append-only history with an incrementing sequence,
    # with no notification status change between attempts.
    rec = _pending("email")
    reg = _reg("email", "failed", failure_class=FAILURE_UNAVAILABLE)
    r1 = dispatch_notification(rec.notification_uid, registry=reg)
    assert r1.attempt_seq == 1
    assert ledger.get_notification(notification_uid=rec.id and rec.notification_uid).status == ledger.PENDING
    r2 = dispatch_notification(rec.notification_uid, registry=reg)  # still pending -> retried
    assert r2.attempt_seq == 2
    atts = delivery_attempts(rec.id)
    assert [a["attempt_seq"] for a in atts] == [1, 2]  # both retained, append-only
    assert atts[0]["attempt_uid"] != atts[1]["attempt_uid"]


# --- normalization / references ----------------------------------------------

def test_attempt_records_correlation_and_causation_references():
    rec = _pending("in_app", metadata={"correlation_id": "workflow_instance:99", "causation_id": "evt-abc",
                                        "mapping_id": "m1", "source_event_type": "workflow.approval.requested"})
    dispatch_notification(rec.notification_uid, registry=default_registry())
    att = delivery_attempts(rec.id)[0]
    assert att["correlation_ref"] == "workflow_instance:99" and att["causation_ref"] == "evt-abc"


# --- transaction boundary ----------------------------------------------------

def test_dispatch_rolls_back_with_caller_transaction():
    rec = _pending("in_app")
    try:
        with engine.begin() as c:
            dispatch_notification(rec.notification_uid, registry=default_registry(), conn=c)
            raise RuntimeError("roll back")
    except RuntimeError:
        pass
    assert ledger.get_notification(notification_uid=rec.notification_uid).status == ledger.PENDING
    assert delivery_attempts(rec.id) == []  # attempt rolled back with the caller txn


# --- batch dispatch ----------------------------------------------------------

def test_batch_dispatches_pending_intents():
    mine = [_pending("in_app") for _ in range(3)]
    summary = dispatch_pending_notifications(limit=500, registry=default_registry())
    assert summary[DELIVERED] >= 3
    for rec in mine:
        assert ledger.get_notification(notification_uid=rec.notification_uid).status == ledger.DELIVERED


# --- no side effects (scope contract) ----------------------------------------

def test_dispatch_causes_no_workflow_domain_audit_or_event_mutation():
    from app.db import audit_events
    from app.platform.outbox import outbox_events
    from app.services.workflow_automation import launch_workflow, workflow_detail
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"F55 {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"F55 {s}", active=True).returning(people.c.id)).scalar_one()
    iid = launch_workflow("client_onboarding", actor_user_id=None, person_id=pid, household_id=hid, idempotency_key=f"f55-{s}")
    wf_before = (workflow_detail(iid)["workflow"]["status"], tuple(x["status"] for x in workflow_detail(iid)["steps"]))
    rec = _pending("in_app")
    with engine.connect() as c:
        a_before = c.execute(select(func.count()).select_from(audit_events)).scalar_one()
        o_before = c.execute(select(func.count()).select_from(outbox_events)).scalar_one()
    dispatch_notification(rec.notification_uid, registry=default_registry())
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(audit_events)).scalar_one() == a_before   # no audit/evidence
        assert c.execute(select(func.count()).select_from(outbox_events)).scalar_one() == o_before   # no events
    wf_after = (workflow_detail(iid)["workflow"]["status"], tuple(x["status"] for x in workflow_detail(iid)["steps"]))
    assert wf_before == wf_after  # no workflow/domain mutation


def test_result_content_free_and_scope_contract():
    rec = _pending("in_app")
    res = dispatch_notification(rec.notification_uid, registry=default_registry())
    blob = str(res.to_dict())
    assert "title" not in blob and "body" not in blob
    source = (REPO_ROOT / "app" / "services" / "notification_dispatch.py").read_text()
    for forbidden in ("record_notification(", "create_intent", "evaluate_delivery", "record_preference",
                      "write_audit_event", "record_evidence", "APIRouter", "add_job", "schedule_retry"):
        assert forbidden not in source
    assert (REPO_ROOT / "docs" / "NOTIFICATION_DISPATCH.md").is_file()
