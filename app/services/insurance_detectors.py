"""Insurance in-force obligation calendar — overdue-review detector.

Release 0.10.0, Phase 3 (NON-REGULATED). Translates STORED servicing data
(``insurance_policy_reviews``) into shared Exception Engine records with
``domain='insurance'``, reusing the exact raise / stable-dedupe / auto-resolve
contract the benefits and tax detectors use — **no second exception engine, state
machine, or history model.**

An overdue servicing review is an OPERATIONAL lapse. Nothing here evaluates
suitability, replacement/1035, licensing, or CE, or makes any compliance
determination; those remain behind the AD-5 compliance gate. The scan flips a
past-due review to ``overdue`` (publishing the shared timeline event) and raises
``INS_REVIEW_OVERDUE``; when the review is completed/cancelled/deferred the
exception auto-resolves so recurrence reopens it.

Live scheduler (cron) wiring is Phase 6; this module is the callable, idempotent
entry point (also driven by the operational manual-scan endpoint).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from app.db import engine, exceptions, insurance_policy_reviews
from app.services import exception_engine as ee
from app.services import insurance as ins

_DEDUPE_PREFIX = "ins:review_overdue:"
_CLOSED = ("resolved", "cancelled")
# A review still "open" for servicing (eligible to be flipped to overdue)...
_OPEN_REVIEW = ("due", "scheduled", "in_progress")
# ...and the full set whose past-due state keeps the exception raised.
_OVERDUE_CONDITION = ("due", "scheduled", "in_progress", "overdue")


def _today(today):
    return today or date.today()


def _due_datetime(d):
    return datetime(d.year, d.month, d.day, 23, 59, tzinfo=UTC)


def _overdue_conditions(today):
    """Reviews past their due date and not yet closed, each with the exception scope
    resolved from the review's policy/case anchor (organization + person/household)."""
    conditions = {}
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(insurance_policy_reviews).where(
            insurance_policy_reviews.c.status.in_(_OVERDUE_CONDITION),
            insurance_policy_reviews.c.due_date < today)).mappings()]
        for review in rows:
            anchor = ins._review_anchor(c, review)
            org_id = anchor.get("organization_id")
            conditions[f"{_DEDUPE_PREFIX}{review['id']}"] = {
                "review": review,
                "anchor": anchor,
                "scope": {
                    "related_entity_type": "organization" if org_id else None,
                    "related_entity_id": org_id,
                    "person_id": anchor.get("person_id"),
                    "household_id": anchor.get("household_id"),
                    "title": "Insurance policy review overdue",
                    "sla_due_at": _due_datetime(review["due_date"]),
                },
            }
    return conditions


def _flip_overdue(review, anchor, actor_user_id):
    """Move a still-open review to ``overdue`` and publish the shared timeline event.
    Re-checked under the transaction so a concurrent completion is never overwritten."""
    with engine.begin() as c:
        current = c.execute(select(insurance_policy_reviews.c.status).where(
            insurance_policy_reviews.c.id == review["id"])).scalar_one_or_none()
        if current not in _OPEN_REVIEW:
            return False
        c.execute(insurance_policy_reviews.update().where(
            insurance_policy_reviews.c.id == review["id"]).values(
            status="overdue", updated_at=ins._now()))
        ins._publish_review(c, review, anchor, action="insurance.review.overdue",
                            status="overdue", actor_user_id=actor_user_id, request_id=None,
                            metadata={"review_type": review["review_type"]})
    return True


def _auto_resolve(exception_id, status, actor_user_id):
    """System-resolve an exception whose review is no longer overdue, so recurrence reopens
    it. Un-started exceptions are advanced open→in_progress first (both legal transitions)."""
    if status in ("open", "acknowledged", "reopened"):
        ee.begin_work(exception_id, principal=None, actor_user_id=actor_user_id)
    ee.resolve(exception_id, "auto_source_cleared", principal=None, actor_user_id=actor_user_id)


def _auto_close_cleared(conditions, actor_user_id, failures):
    with engine.connect() as c:
        stale = c.execute(select(exceptions.c.id, exceptions.c.status, exceptions.c.dedupe_key).where(
            exceptions.c.dedupe_key.like(f"{_DEDUPE_PREFIX}%"),
            exceptions.c.status.notin_(_CLOSED))).mappings().all()
    closed = 0
    for row in stale:
        if row["dedupe_key"] in conditions:
            continue
        try:
            _auto_resolve(row["id"], row["status"], actor_user_id)
            closed += 1
        except Exception as exc:  # pragma: no cover - defensive isolation
            failures.append({"dedupe_key": row["dedupe_key"], "error": type(exc).__name__})
    return closed


def detect_reviews_overdue(*, actor_user_id=None, today=None):
    """Raise ``INS_REVIEW_OVERDUE`` for each past-due review (idempotent) and flip
    still-open reviews to ``overdue``. Auto-close any open exception whose review has
    since been completed/cancelled/deferred. Each condition is isolated."""
    today = _today(today)
    conditions = _overdue_conditions(today)
    raised, flipped, failures = 0, 0, []
    for key, cond in conditions.items():
        review = cond["review"]
        try:
            if review["status"] in _OPEN_REVIEW and _flip_overdue(review, cond["anchor"], actor_user_id):
                flipped += 1
            ee.raise_exception(code="INS_REVIEW_OVERDUE", actor_user_id=actor_user_id,
                               principal=None, source="system", dedupe_key=key, **cond["scope"])
            raised += 1
        except Exception as exc:  # pragma: no cover - defensive isolation
            failures.append({"dedupe_key": key, "error": type(exc).__name__})
    closed = _auto_close_cleared(conditions, actor_user_id, failures)
    return {"raised": raised, "reviews_marked_overdue": flipped, "closed": closed, "failures": failures}


def _open_review_exception_status():
    with engine.connect() as c:
        return {r["id"]: r["status"] for r in c.execute(select(
            exceptions.c.id, exceptions.c.status).where(
            exceptions.c.dedupe_key.like(f"{_DEDUPE_PREFIX}%"))).mappings()}


def run_insurance_review_scan(*, actor_user_id=None, today=None):
    """Scheduled/manual entry point for the in-force obligation calendar. Idempotent:
    re-running neither double-raises (dedupe) nor re-flips (guarded). Returns an honest
    execution result — reviews flipped overdue and exceptions opened/reopened/resolved."""
    before = _open_review_exception_status()
    result = detect_reviews_overdue(actor_user_id=actor_user_id, today=today)
    after = _open_review_exception_status()

    def active(st):
        return st not in _CLOSED

    opened = sum(1 for i, st in after.items() if active(st) and i not in before)
    reopened = sum(1 for i, st in after.items() if active(st) and i in before and not active(before[i]))
    resolved = sum(1 for i, st in after.items() if not active(st) and i in before and active(before[i]))
    return {
        "reviews_marked_overdue": result["reviews_marked_overdue"],
        "exceptions_opened": opened,
        "exceptions_reopened": reopened,
        "exceptions_resolved": resolved,
        "failures": len(result["failures"]),
        "failure_detail": result["failures"],
    }
