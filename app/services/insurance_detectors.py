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

The detectors are the callable, idempotent entry points. Phase 6 added the single
``run_insurance_scan()`` orchestrator, wired into the shared scheduler
(``app/jobs/scheduler.py`` → ``insurance-detector-scan``) and also driven by the operational
manual-scan endpoint.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.db import (
    engine,
    exceptions,
    insurance_ce_records,
    insurance_commissions,
    insurance_licenses,
    insurance_policies,
    insurance_policy_reviews,
)
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


# --- shared scan plumbing (one implementation for every insurance scan) ------

def _exception_status(*, prefix=None, domain=None):
    """id -> status snapshot for the insurance exceptions selected by dedupe-key ``prefix``
    or ``domain``. The single snapshot every scan's before/after diff runs against."""
    with engine.connect() as c:
        query = select(exceptions.c.id, exceptions.c.status)
        if prefix is not None:
            query = query.where(exceptions.c.dedupe_key.like(f"{prefix}%"))
        if domain is not None:
            query = query.where(exceptions.c.domain == domain)
        return {r[0]: r[1] for r in c.execute(query)}


def _scan_delta(before, after):
    """opened / reopened / resolved / skipped from two id->status snapshots — the scan diff
    arithmetic in exactly one place. ``skipped`` = still-open conditions re-confirmed (idempotent
    no-ops)."""
    def active(st):
        return st not in _CLOSED
    opened = sum(1 for i, st in after.items() if active(st) and i not in before)
    reopened = sum(1 for i, st in after.items() if active(st) and i in before and not active(before[i]))
    resolved = sum(1 for i, st in after.items() if not active(st) and i in before and active(before[i]))
    skipped = sum(1 for i, st in after.items() if active(st) and i in before and active(before[i]))
    return opened, reopened, resolved, skipped


def _run_detector_deltas(pairs, *, actor_user_id, today):
    """Run each ``(dedupe_prefix, detector)`` pair — isolated by prefix — and aggregate honest
    opened/reopened/resolved + failures across them. Shared by the licensing and commission
    family scans so the per-prefix diff loop exists once."""
    opened = reopened = resolved = 0
    failures = []
    for prefix, detect in pairs:
        before = _exception_status(prefix=prefix)
        result = detect(actor_user_id=actor_user_id, today=today)
        after = _exception_status(prefix=prefix)
        o, r, res, _skipped = _scan_delta(before, after)
        opened += o
        reopened += r
        resolved += res
        failures += result["failures"]
    return {
        "exceptions_opened": opened,
        "exceptions_reopened": reopened,
        "exceptions_resolved": resolved,
        "failures": len(failures),
        "failure_detail": failures,
    }


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


def _auto_close_cleared(conditions, actor_user_id, failures, prefix=_DEDUPE_PREFIX):
    with engine.connect() as c:
        stale = c.execute(select(exceptions.c.id, exceptions.c.status, exceptions.c.dedupe_key).where(
            exceptions.c.dedupe_key.like(f"{prefix}%"),
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


def run_insurance_review_scan(*, actor_user_id=None, today=None):
    """Scheduled/manual entry point for the in-force obligation calendar. Idempotent:
    re-running neither double-raises (dedupe) nor re-flips (guarded). Returns an honest
    execution result — reviews flipped overdue and exceptions opened/reopened/resolved."""
    before = _exception_status(prefix=_DEDUPE_PREFIX)
    result = detect_reviews_overdue(actor_user_id=actor_user_id, today=today)
    after = _exception_status(prefix=_DEDUPE_PREFIX)
    opened, reopened, resolved, _skipped = _scan_delta(before, after)
    return {
        "reviews_marked_overdue": result["reviews_marked_overdue"],
        "exceptions_opened": opened,
        "exceptions_reopened": reopened,
        "exceptions_resolved": resolved,
        "failures": len(result["failures"]),
        "failure_detail": result["failures"],
    }


# ============================================================================
# Phase 4 — producer licensing & CE EXPIRY reminders (date-driven, operational).
# These flag an upcoming license-expiry / CE-period-end date. They make NO
# licensing-validation or CE-satisfaction determination and block nothing; the
# regulated determinations stay behind the AD-5 gate. Licensing is firm-internal
# (not client-scoped), so the exceptions carry no person/household anchor and
# surface to oversight roles (record.read_all).
# ============================================================================
_LICENSE_PREFIX = "ins:license_expiring:"
_CE_PREFIX = "ins:ce_period_ending:"
LICENSE_EXPIRY_WINDOW_DAYS = 60
CE_PERIOD_WINDOW_DAYS = 90


def _firm_scope(title, sla_date):
    """A firm-internal (unanchored) exception scope — no client person/household."""
    return {"related_entity_type": None, "related_entity_id": None,
            "person_id": None, "household_id": None, "title": title,
            "sla_due_at": _due_datetime(sla_date)}


def _license_expiry_conditions(today):
    horizon = today + timedelta(days=LICENSE_EXPIRY_WINDOW_DAYS)
    with engine.connect() as c:
        rows = c.execute(select(insurance_licenses.c.id, insurance_licenses.c.expiry_date).where(
            insurance_licenses.c.status == "active",
            insurance_licenses.c.expiry_date.isnot(None),
            insurance_licenses.c.expiry_date <= horizon)).mappings().all()
    return {f"{_LICENSE_PREFIX}{r['id']}": _firm_scope("Producer license expiring", r["expiry_date"])
            for r in rows}


def _ce_period_conditions(today):
    horizon = today + timedelta(days=CE_PERIOD_WINDOW_DAYS)
    with engine.connect() as c:
        rows = c.execute(select(insurance_ce_records.c.id, insurance_ce_records.c.period_end).where(
            insurance_ce_records.c.status == "in_progress",
            insurance_ce_records.c.period_end.isnot(None),
            insurance_ce_records.c.period_end <= horizon)).mappings().all()
    return {f"{_CE_PREFIX}{r['id']}": _firm_scope("Continuing-education period ending", r["period_end"])
            for r in rows}


def _reconcile(prefix, code, conditions, *, actor_user_id):
    """Raise ``code`` for each current condition (idempotent) and auto-close any open
    exception in this family whose condition has cleared. Each item is isolated."""
    raised, failures = 0, []
    for key, scope in conditions.items():
        try:
            ee.raise_exception(code=code, actor_user_id=actor_user_id, principal=None,
                               source="system", dedupe_key=key, **scope)
            raised += 1
        except Exception as exc:  # pragma: no cover - defensive isolation
            failures.append({"dedupe_key": key, "error": type(exc).__name__})
    closed = _auto_close_cleared(conditions, actor_user_id, failures, prefix=prefix)
    return {"raised": raised, "closed": closed, "failures": failures}


def detect_licenses_expiring(*, actor_user_id=None, today=None):
    today = _today(today)
    return _reconcile(_LICENSE_PREFIX, "INS_LICENSE_EXPIRING",
                      _license_expiry_conditions(today), actor_user_id=actor_user_id)


def detect_ce_period_ending(*, actor_user_id=None, today=None):
    today = _today(today)
    return _reconcile(_CE_PREFIX, "INS_CE_PERIOD_ENDING",
                      _ce_period_conditions(today), actor_user_id=actor_user_id)


def run_insurance_licensing_scan(*, actor_user_id=None, today=None):
    """Scheduled/manual entry point for licensing & CE expiry reminders. Idempotent
    (stable dedupe). Returns honest opened/reopened/resolved counts."""
    return _run_detector_deltas(
        [(_LICENSE_PREFIX, detect_licenses_expiring), (_CE_PREFIX, detect_ce_period_ending)],
        actor_user_id=actor_user_id, today=today)


# ============================================================================
# Phase 5 — commission reconciliation exceptions (operational/financial).
# A reconciled row whose received amount differs from expected raises
# INS_COMMISSION_VARIANCE; an expected row past its due date with nothing received
# raises INS_COMMISSION_OUTSTANDING. Both are money-reconciliation gaps — NOT a
# compliance conclusion.
#
# PRIVACY: these are FIRM-INTERNAL back-office financial exceptions. They anchor the client
# ORGANIZATION when the policy has one (for organization record scope + org-scoped work
# queues), but they carry NO person/household anchor — that is the only thing that would make
# the SHARED engine publish a client-facing "Commission variance" timeline event, and sensitive
# compensation must never surface there. The policy/commission ids also travel in the
# description/metadata for triage. They surface to oversight/operations roles, like the
# licensing/CE expiry reminders. Idempotent and auto-resolving through the SHARED engine; the
# orchestrated run_insurance_scan() is wired into the scheduler (Phase 6).
# ============================================================================
_COMMISSION_VARIANCE_PREFIX = "ins:commission_variance:"
_COMMISSION_OUTSTANDING_PREFIX = "ins:commission_outstanding:"


def _commission_scope(row, title):
    """Organization-scoped, firm-internal scope. Anchors the client ORGANIZATION when the
    policy has one (``related_entity_type='organization'``) so the exception gets organization
    record scope and reaches org-scoped work queues — but NEVER sets person/household, which is
    the only thing that would publish a client-facing timeline event. Compensation therefore
    stays off the client timeline; the policy/commission ids ride in description + metadata."""
    org_id = row["organization_id"]
    return {"related_entity_type": "organization" if org_id else None,
            "related_entity_id": org_id,
            "person_id": None, "household_id": None,
            "title": title,
            "description": f"Commission #{row['id']} on policy #{row['policy_id']}",
            "sla_due_at": _due_datetime(row["due_date"]) if row["due_date"] else None,
            "metadata": {"commission_id": row["id"], "policy_id": row["policy_id"]}}


def _commission_variance_conditions(today):
    conditions = {}
    with engine.connect() as c:
        rows = c.execute(select(insurance_commissions).where(
            insurance_commissions.c.status.in_(("partial", "variance")))).mappings().all()
        for row in rows:
            conditions[f"{_COMMISSION_VARIANCE_PREFIX}{row['id']}"] = _commission_scope(
                row, "Commission variance vs expected")
    return conditions


def _commission_outstanding_conditions(today):
    conditions = {}
    with engine.connect() as c:
        rows = c.execute(select(insurance_commissions).where(
            insurance_commissions.c.status == "expected",
            insurance_commissions.c.received_amount.is_(None),
            insurance_commissions.c.due_date.isnot(None),
            insurance_commissions.c.due_date < today)).mappings().all()
        for row in rows:
            conditions[f"{_COMMISSION_OUTSTANDING_PREFIX}{row['id']}"] = _commission_scope(
                row, "Expected commission outstanding")
    return conditions


def detect_commission_variance(*, actor_user_id=None, today=None):
    today = _today(today)
    return _reconcile(_COMMISSION_VARIANCE_PREFIX, "INS_COMMISSION_VARIANCE",
                      _commission_variance_conditions(today), actor_user_id=actor_user_id)


def detect_commissions_outstanding(*, actor_user_id=None, today=None):
    today = _today(today)
    return _reconcile(_COMMISSION_OUTSTANDING_PREFIX, "INS_COMMISSION_OUTSTANDING",
                      _commission_outstanding_conditions(today), actor_user_id=actor_user_id)


def run_insurance_commission_scan(*, actor_user_id=None, today=None):
    """Scheduled/manual entry point for commission reconciliation exceptions. Idempotent
    (stable dedupe). Returns honest opened/reopened/resolved counts."""
    return _run_detector_deltas(
        [(_COMMISSION_VARIANCE_PREFIX, detect_commission_variance),
         (_COMMISSION_OUTSTANDING_PREFIX, detect_commissions_outstanding)],
        actor_user_id=actor_user_id, today=today)


# ============================================================================
# Phase 6 — single orchestrated insurance scan.
# One entry point that runs EVERY insurance detector through the shared Exception
# Engine (no insurance-specific engine). Idempotent (stable dedupe), auto-resolving/
# reopening, with per-detector failure isolation so one detector — or one
# organization's bad data inside a detector — never aborts the rest. Honest aggregate
# reporting. This is the live scheduler + manual entry point (wired in app/jobs/scheduler.py).
# ============================================================================

def _count_insurance_organizations():
    """Distinct client organizations in the insurance book (organization-scoped policies)."""
    with engine.connect() as c:
        return c.execute(select(func.count(func.distinct(insurance_policies.c.organization_id)))
                         .where(insurance_policies.c.organization_id.isnot(None))).scalar_one()


def run_insurance_scan(*, actor_user_id=None, today=None):
    """Run every insurance detector as one idempotent, failure-isolated scan and report
    honestly. Reuses the shared Exception Engine and Work Management — no insurance-specific
    subsystem. Returns organizations scanned and exceptions opened / resolved / reopened /
    skipped (idempotent no-ops re-confirmed) / failures, plus each detector's own result."""
    before = _exception_status(domain="insurance")
    # Resolve sub-scans by module attribute at call time so a detector can be independently
    # stubbed/failed in tests and a real failure is isolated here.
    subscans = (
        ("reviews", run_insurance_review_scan),
        ("licensing", run_insurance_licensing_scan),
        ("commissions", run_insurance_commission_scan),
    )
    by_detector, failures = {}, []
    for name, fn in subscans:
        try:
            result = fn(actor_user_id=actor_user_id, today=today)
            by_detector[name] = result
            for fd in result.get("failure_detail", []):
                failures.append({"detector": name, **fd})
        except Exception as exc:  # pragma: no cover - defensive isolation: one detector can't abort the scan
            by_detector[name] = {"error": type(exc).__name__}
            failures.append({"detector": name, "error": type(exc).__name__})
    after = _exception_status(domain="insurance")
    opened, reopened, resolved, skipped = _scan_delta(before, after)
    return {
        "organizations_scanned": _count_insurance_organizations(),
        "exceptions_opened": opened,
        "exceptions_resolved": resolved,
        "exceptions_reopened": reopened,
        "exceptions_skipped": skipped,
        "failures": len(failures),
        "failure_detail": failures,
        "by_detector": by_detector,
    }
