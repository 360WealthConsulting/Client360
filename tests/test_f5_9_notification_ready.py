"""F5.9 / Epic 5 — Ready notification claim tests (ADR-017).

Covers the neutral claim-contract relocation, the candidate repository, the readiness
evaluator, and the claim_next_ready orchestrator: zero-attempt immediate readiness, inclusive
retry-timing boundary, fail-closed invalid/missing timing, head-of-line-blocking prevention,
bounded scanning with content-free diagnostics, deterministic ordering, read-only behavior,
and F5.6 compatibility (default-claim swap + injected-claim tests still valid).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import engine
from app.services import notification_dispatch as dispatch
from app.services import notifications as ledger
from app.services.notification_claims import PendingNotificationClaim
from app.services.notification_ready import (
    CandidateRepository,
    ClaimDiagnostics,
    ReadinessEvaluator,
    ReadinessVerdict,
    _select_ready,
    claim_next_ready,
)
from app.services.notification_retry import RetryDecision, RetryReason

REPO_ROOT = Path(__file__).resolve().parents[1]
T = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)  # fixed injected clock


def _pending(channel="email"):
    return ledger.record_notification(
        notification_type="workflow.approval.requested", recipient_type="user",
        recipient_ref=f"user:{uuid.uuid4().hex[:12]}", channel=channel,
        title="template:workflow.approval.requested", body=None, status=ledger.PENDING,
        dedupe_key=f"f5.9-test:{uuid.uuid4().hex}",
        metadata={"correlation_id": "workflow_instance:1", "causation_id": None})


def _attempt(rec, completed_at, *, seq=1, retry_recommended=True):
    t = dispatch._attempts_table()
    with engine.begin() as c:
        c.execute(t.insert().values(
            attempt_uid=str(uuid.uuid4()), notification_id=rec.id, notification_uid=rec.notification_uid,
            attempt_seq=seq, provider="fake-email", channel=rec.channel,
            execution_completed_at=completed_at, execution_result="provider_unavailable",
            provider_status="failed", retry_recommended=retry_recommended,
            failure_class="provider_unavailable"))


def _only(mine_ids):
    """attempted_ids excluding everything pending except `mine_ids` (deterministic isolation)."""
    n = ledger._notifications_table()
    with engine.connect() as c:
        allp = {r[0] for r in c.execute(select(n.c.id).where(n.c.status == ledger.PENDING)).all()}
    return allp - set(mine_ids)


def _decision(reason, *, retry_delay=None, completed=1):
    eligible = reason is RetryReason.RETRYABLE_TRANSIENT
    return RetryDecision(eligible=eligible, completed_attempts=completed,
                         retry_ordinal=(completed if eligible else None),
                         next_attempt_number=(completed + 1 if eligible else None),
                         max_attempts=4, retry_delay=retry_delay, reason=reason, policy_id="test")


class _FakeRepo:
    def __init__(self, completed_at=None, *, raise_ts=False):
        self._conn = None
        self._ts = completed_at
        self._raise = raise_ts

    def latest_completed_at(self, nid):
        if self._raise:
            raise RuntimeError("read failed")
        return self._ts


class _FakeEval:
    """Returns pre-scripted verdicts in candidate order."""
    def __init__(self, verdicts):
        self._v = list(verdicts)
        self._i = 0

    def is_ready(self, candidate, *, now):
        v = self._v[self._i]
        self._i += 1
        return v


# --- contract relocation -----------------------------------------------------

def test_claim_contract_lives_in_neutral_module_and_is_shared():
    import app.services.notification_claims as claims
    import app.services.notification_ready as ready
    import app.services.notification_worker as worker
    assert claims.PendingNotificationClaim is worker.PendingNotificationClaim  # re-export, same object
    assert claims.PendingNotificationClaim is ready.PendingNotificationClaim
    # old F5.6 import path still resolves + constructs + isinstance works
    from app.services.notification_worker import PendingNotificationClaim as OldPath
    c = OldPath(notification_id=1, notification_uid="u", created_at=None)
    assert isinstance(c, claims.PendingNotificationClaim) and c.notification_id == 1


def test_ready_module_does_not_import_worker():
    src = (REPO_ROOT / "app" / "services" / "notification_ready.py").read_text()
    assert "notification_worker" not in src


# --- repository --------------------------------------------------------------

def test_repository_deterministic_order_exclusion_and_bound():
    a, b, cc = _pending(), _pending(), _pending()
    ids = [a.id, b.id, cc.id]
    with engine.connect() as conn:
        repo = CandidateRepository(conn)
        got = repo.pending_candidates(exclude=_only(ids), limit=100)
        assert [g.notification_id for g in got] == sorted(ids)  # deterministic id-asc
        assert all(isinstance(g, PendingNotificationClaim) for g in got)
        # exclusion
        got2 = repo.pending_candidates(exclude=_only(ids) | {a.id}, limit=100)
        assert a.id not in [g.notification_id for g in got2]
        # bound
        got3 = repo.pending_candidates(exclude=_only(ids), limit=2)
        assert len(got3) == 2


def test_repository_latest_completed_at():
    rec = _pending()
    with engine.connect() as conn:
        assert CandidateRepository(conn).latest_completed_at(rec.id) is None  # no attempts
    _attempt(rec, T - timedelta(seconds=10), seq=1)
    _attempt(rec, T - timedelta(seconds=5), seq=2)
    with engine.connect() as conn:
        assert CandidateRepository(conn).latest_completed_at(rec.id) == T - timedelta(seconds=5)  # latest seq


def test_repository_is_read_only():
    rec = _pending()
    with engine.connect() as c:
        n_before = c.execute(select(func.count()).select_from(ledger._notifications_table())).scalar_one()
    with engine.connect() as conn:
        CandidateRepository(conn).pending_candidates(exclude=_only([rec.id]), limit=10)
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(ledger._notifications_table())).scalar_one() == n_before
        assert ledger.get_notification(notification_uid=rec.notification_uid).status == ledger.PENDING


# --- evaluator (isolated via fakes) ------------------------------------------

def _eval(decision, *, completed_at=None, raise_ts=False):
    ev = ReadinessEvaluator(_FakeRepo(completed_at, raise_ts=raise_ts), evaluate=lambda **kw: decision)
    return ev.is_ready(PendingNotificationClaim(notification_id=1), now=T)


def test_evaluator_zero_attempts_ready():
    v = _eval(_decision(RetryReason.NOT_APPLICABLE_NO_ATTEMPTS, completed=0))
    assert v == ReadinessVerdict(True, "zero_attempt_ready")


def test_evaluator_retry_before_due_not_ready():
    d = _decision(RetryReason.RETRYABLE_TRANSIENT, retry_delay=timedelta(seconds=30))
    v = _eval(d, completed_at=T - timedelta(seconds=1))  # due = T+29s > now
    assert v.ready is False and v.category == "retry_not_due"


def test_evaluator_retry_exactly_due_is_ready_inclusive():
    d = _decision(RetryReason.RETRYABLE_TRANSIENT, retry_delay=timedelta(seconds=30))
    v = _eval(d, completed_at=T - timedelta(seconds=30))  # due == now
    assert v.ready is True and v.category == "retry_ready"


def test_evaluator_retry_after_due_is_ready():
    d = _decision(RetryReason.RETRYABLE_TRANSIENT, retry_delay=timedelta(seconds=30))
    v = _eval(d, completed_at=T - timedelta(seconds=60))  # due = T-30s < now
    assert v.ready is True and v.category == "retry_ready"


@pytest.mark.parametrize("reason", [RetryReason.TERMINAL_DISPOSITION, RetryReason.EXHAUSTED,
                                    RetryReason.NON_RETRYABLE_FAILURE])
def test_evaluator_ineligible(reason):
    v = _eval(_decision(reason))
    assert v.ready is False and v.category == "retry_ineligible"


def test_evaluator_missing_timestamp_fails_closed():
    d = _decision(RetryReason.RETRYABLE_TRANSIENT, retry_delay=timedelta(seconds=30))
    v = _eval(d, completed_at=None)  # inconsistent: attempted but no timestamp
    assert v.ready is False and v.category == "missing_attempt_timestamp"


def test_evaluator_naive_timestamp_fails_closed():
    d = _decision(RetryReason.RETRYABLE_TRANSIENT, retry_delay=timedelta(seconds=30))
    v = _eval(d, completed_at=datetime(2026, 7, 1, 12, 0, 0))  # naive
    assert v.ready is False and v.category == "missing_attempt_timestamp"


def test_evaluator_attempt_read_failure_is_evaluation_error():
    d = _decision(RetryReason.RETRYABLE_TRANSIENT, retry_delay=timedelta(seconds=30))
    ev = ReadinessEvaluator(_FakeRepo(raise_ts=True), evaluate=lambda **kw: d)
    v = ev.is_ready(PendingNotificationClaim(notification_id=1), now=T)
    assert v.ready is False and v.category == "evaluation_errors"


def test_evaluator_f57_failure_is_evaluation_error():
    def _boom(**kw):
        raise RuntimeError("f5.7 down")
    ev = ReadinessEvaluator(_FakeRepo(), evaluate=_boom)
    v = ev.is_ready(PendingNotificationClaim(notification_id=1), now=T)
    assert v.ready is False and v.category == "evaluation_errors"


# --- orchestrator scan logic (_select_ready, fakes) --------------------------

def _cands(n):
    return [PendingNotificationClaim(notification_id=i) for i in range(1, n + 1)]


def test_select_ready_not_due_does_not_block_later_ready():
    diag = ClaimDiagnostics()
    ev = _FakeEval([ReadinessVerdict(False, "retry_not_due"), ReadinessVerdict(True, "retry_ready")])
    claim = _select_ready(_cands(2), ev, now=T, scan_limit=100, diag=diag)
    assert claim.notification_id == 2  # earliest not-due did not block
    assert diag.retry_not_due == 1 and diag.retry_ready == 1 and diag.claim_returned is True


def test_select_ready_ineligible_and_invalid_do_not_block():
    diag = ClaimDiagnostics()
    ev = _FakeEval([ReadinessVerdict(False, "retry_ineligible"),
                    ReadinessVerdict(False, "missing_attempt_timestamp"),
                    ReadinessVerdict(True, "zero_attempt_ready")])
    claim = _select_ready(_cands(3), ev, now=T, scan_limit=100, diag=diag)
    assert claim.notification_id == 3
    assert diag.retry_ineligible == 1 and diag.missing_attempt_timestamp == 1


def test_select_ready_scan_bound_reached_returns_none():
    diag = ClaimDiagnostics()
    ev = _FakeEval([ReadinessVerdict(False, "retry_not_due")] * 5)
    claim = _select_ready(_cands(5), ev, now=T, scan_limit=5, diag=diag)  # full bound, none ready
    assert claim is None and diag.scan_bound_reached is True and diag.no_ready_claim is True


def test_select_ready_exhausted_returns_none_without_bound_flag():
    diag = ClaimDiagnostics()
    ev = _FakeEval([ReadinessVerdict(False, "retry_ineligible")] * 3)
    claim = _select_ready(_cands(3), ev, now=T, scan_limit=100, diag=diag)  # fewer than bound
    assert claim is None and diag.scan_bound_reached is False and diag.no_ready_claim is True


def test_select_ready_one_claim_per_call():
    diag = ClaimDiagnostics()
    ev = _FakeEval([ReadinessVerdict(True, "zero_attempt_ready"), ReadinessVerdict(True, "retry_ready")])
    claim = _select_ready(_cands(2), ev, now=T, scan_limit=100, diag=diag)
    assert claim.notification_id == 1 and diag.candidates_inspected == 1  # stops at first ready


# --- orchestrator end-to-end (real repo/evaluator/F5.7, isolated candidates) --

def test_claim_next_ready_returns_zero_attempt_candidate():
    rec = _pending()
    got = []
    claim = claim_next_ready(attempted_ids=_only([rec.id]), now=T, observe=got.append)
    assert claim is not None and claim.notification_id == rec.id
    assert got[0].zero_attempt_ready == 1 and got[0].claim_returned is True


def test_claim_next_ready_skips_not_due_returns_later_ready():
    not_due = _pending()
    _attempt(not_due, T, seq=1)          # due = T + 30s -> not due at now=T
    ready = _pending()                    # zero attempts -> ready
    claim = claim_next_ready(attempted_ids=_only([not_due.id, ready.id]), now=T)
    assert claim.notification_id == ready.id  # head-of-line prevented (not_due has lower id)


def test_claim_next_ready_retry_exactly_due_is_claimed():
    rec = _pending()
    _attempt(rec, T - timedelta(seconds=30), seq=1)  # due == now (inclusive)
    claim = claim_next_ready(attempted_ids=_only([rec.id]), now=T)
    assert claim is not None and claim.notification_id == rec.id


def test_claim_next_ready_none_when_all_not_due():
    rec = _pending()
    _attempt(rec, T, seq=1)  # not due
    got = []
    claim = claim_next_ready(attempted_ids=_only([rec.id]), now=T, observe=got.append)
    assert claim is None and got[0].no_ready_claim is True and got[0].retry_not_due == 1


def test_diagnostics_emitted_once_and_content_free():
    rec = _pending()
    events = []
    claim_next_ready(attempted_ids=_only([rec.id]), now=T, observe=events.append)
    assert len(events) == 1
    blob = str(events[0].to_dict())
    for forbidden in ("user:", "template:", "title", "body", rec.recipient_ref):
        assert forbidden not in blob


def test_scan_limit_validation():
    for bad in (0, -1, True, 1.5, "x"):
        with pytest.raises(ValueError):
            claim_next_ready(scan_limit=bad)


def test_naive_now_rejected():
    with pytest.raises(ValueError):
        claim_next_ready(now=datetime(2026, 7, 1, 12, 0, 0))  # naive


def test_claim_next_ready_is_read_only():
    rec = _pending()
    _attempt(rec, T - timedelta(seconds=60), seq=1)
    with engine.connect() as c:
        n_att = c.execute(select(func.count()).select_from(dispatch._attempts_table())
                          .where(dispatch._attempts_table().c.notification_id == rec.id)).scalar_one()
    claim_next_ready(attempted_ids=_only([rec.id]), now=T)
    after = ledger.get_notification(notification_uid=rec.notification_uid)
    with engine.connect() as c:
        n_att2 = c.execute(select(func.count()).select_from(dispatch._attempts_table())
                           .where(dispatch._attempts_table().c.notification_id == rec.id)).scalar_one()
    assert after.status == ledger.PENDING and after.attempts == 0 and n_att2 == n_att  # nothing mutated


# --- F5.6 compatibility ------------------------------------------------------

def test_f56_default_claim_is_claim_next_ready():
    from app.services import notification_ready
    from app.services.notification_worker import claim_next_ready as worker_default
    assert worker_default is notification_ready.claim_next_ready


def test_f56_injected_claim_still_works():
    from app.services.notification_providers import default_registry
    from app.services.notification_worker import run_dispatch_cycle
    rec = _pending("in_app")
    # inject a claim that yields exactly this notification once, then None
    seen = {"done": False}

    def _claim(attempted, **kw):
        if seen["done"] or rec.id in attempted:
            return None
        seen["done"] = True
        return PendingNotificationClaim(notification_id=rec.id)

    m = run_dispatch_cycle(registry=default_registry(), claim=_claim)
    assert m.delivered == 1
    assert ledger.get_notification(notification_uid=rec.notification_uid).status == ledger.DELIVERED
