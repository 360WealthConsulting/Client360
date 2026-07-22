"""Compliance review service (Phase D.7).

A durable, auditable, human-controlled review layer for governed Advisor
Recommendations. It READS Advisor Intelligence (to snapshot a recommendation) and the
D.6 Rule Catalog (to validate the governing rule + version); it never executes a rule,
never changes the deterministic recommendation result, and is never imported by Advisor
Intelligence. Every write is a recorded fact:

- submit  -> creates a review (idempotent; at most one OPEN review per recommendation
             snapshot), snapshotting the recommendation, evidence, governing rule, and
             rule version.
- assign  -> records the assigned reviewer; the review becomes ``pending_review`` only
             if that principal has established authority, else
             ``blocked_pending_authorized_reviewer``.
- decide  -> appends an immutable decision. Final approval double-gates on the decision
             capability AND a recorded ``ReviewerAuthority`` AND a Rule-Catalog version
             match; without them the approval is blocked (never silently granted).

Concurrency: decisions/assignments pass an ``expected_status``; the row is locked
(``FOR UPDATE``) and a mismatch fails loudly rather than overwriting. The decision
ledger is append-only (DB trigger) — corrections create a NEW decision that references
the prior via ``supersedes_decision_id``.
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select

from app.db import compliance_decisions, compliance_reviews, engine, people
from app.security.authorization import accessible_person_ids, record_in_scope
from app.services.advisor_intelligence import get_client_signals
from app.services.compliance._common import clamp_page, load_for_update, now, page_count
from app.services.compliance.reviewer_authority import reviewer_authority
from app.services.compliance.rule_catalog import RuleCatalog, compare_versions

# --- lifecycle ---------------------------------------------------------------

OPEN_STATUSES = frozenset({
    "pending_submission", "pending_assignment", "pending_review",
    "blocked_pending_authorized_reviewer",
})
DECISION_TYPES = frozenset({"approved", "approved_with_conditions", "returned", "declined"})
_APPROVING = frozenset({"approved", "approved_with_conditions"})

# Explicit allowed source states per action (no generic state machine).
_ASSIGN_FROM = frozenset({"pending_assignment", "pending_review", "blocked_pending_authorized_reviewer"})
_DECIDE_FROM = frozenset({"pending_review", "blocked_pending_authorized_reviewer"})
_DECIDED_STATES = frozenset({"approved", "approved_with_conditions", "returned", "declined"})


class ComplianceReviewError(RuntimeError):
    """Base class for compliance-review domain errors."""


class IneligibleRecommendationError(ComplianceReviewError):
    """The target is not a governed Advisor Recommendation in the caller's scope."""


class StaleReviewError(ComplianceReviewError):
    """The review changed since the caller loaded it; the action was rejected."""


class InvalidTransitionError(ComplianceReviewError):
    """The action is not allowed from the review's current status."""


class DecisionValidationError(ComplianceReviewError):
    """The decision is missing required comments/exceptions."""


class ApprovalBlockedError(ComplianceReviewError):
    """A final approval was blocked (no authorized reviewer, or a rule/version
    mismatch). The review is moved to ``blocked_pending_authorized_reviewer``; no
    approval decision is recorded."""


# Application timestamp for every write (shared compliance helper).
_now = now


# --- eligibility + snapshot --------------------------------------------------

def eligible_recommendation(principal, person_id: int, recommendation_id: str):
    """Return the governed recommendation Signal for this person + id, or ``None``.
    Enforces person record-scope first (scope-first), so an inaccessible client can
    never be submitted. Only ``category == 'recommendation'`` signals are eligible."""
    if not record_in_scope(principal, "person", person_id):
        return None
    for sig in get_client_signals(principal, person_id):
        if sig.id == recommendation_id and sig.category == "recommendation":
            return sig
    return None


def _person_household(person_id: int) -> int | None:
    with engine.connect() as conn:
        return conn.scalar(select(people.c.household_id).where(people.c.id == person_id))


def submit_review(principal, *, person_id: int, recommendation_id: str, actor_user_id: int):
    """Create (idempotently) a compliance review for a governed recommendation.

    Snapshots the recommendation, evidence, governing rule, and rule version. If an
    OPEN review already exists for the same (recommendation, rule, version, source),
    it is returned unchanged — no duplicate."""
    sig = eligible_recommendation(principal, person_id, recommendation_id)
    if sig is None:
        raise IneligibleRecommendationError(
            "Not a governed recommendation in your scope.")
    rec = sig.recommendation
    source = sig.source_record
    household_id = _person_household(person_id)
    with engine.begin() as conn:
        existing = conn.execute(
            select(compliance_reviews).where(
                compliance_reviews.c.recommendation_id == sig.id,
                compliance_reviews.c.governing_rule == rec.governing_rule,
                compliance_reviews.c.rule_version == rec.rule_version,
                compliance_reviews.c.source_entity_type == source.entity_type,
                compliance_reviews.c.source_entity_id == source.entity_id,
                compliance_reviews.c.status.in_(tuple(OPEN_STATUSES)),
            )
        ).mappings().first()
        if existing is not None:
            return dict(existing)
        now = _now()
        row = conn.execute(
            compliance_reviews.insert().values(
                recommendation_id=sig.id,
                recommendation_type=rec.recommendation_type,
                source_entity_type=source.entity_type,
                source_entity_id=source.entity_id,
                person_id=person_id,
                household_id=household_id,
                governing_rule=rec.governing_rule,
                rule_version=rec.rule_version,
                policy_gate=sig.policy_gate.value,
                recommendation_snapshot=sig.to_dict(),
                evidence_snapshot=list(sig.evidence),
                status="pending_assignment",
                submitted_at=now,
                submitted_by=actor_user_id,
                created_at=now,
                updated_at=now,
            ).returning(compliance_reviews)
        ).mappings().one()
    return dict(row)


# --- catalog validation ------------------------------------------------------

def validate_against_catalog(review: dict) -> dict:
    """Validate the review's snapshotted governing rule + version against the current
    D.6 Rule Catalog (reused — no duplicated parsing/semver). Returns a result dict:
    ``{"ok": bool, "reason": str|None, "catalog_rule": RuleDefinition|None}``. A missing
    catalog rule or a version mismatch is a hard block for final approval."""
    catalog = RuleCatalog.from_registry()
    match = next((r for r in catalog.list_rules()
                 if r.governing_rule == review["governing_rule"]), None)
    if match is None:
        return {"ok": False, "reason": "governing rule not found in the Rule Catalog",
                "catalog_rule": None}
    if compare_versions(match.version, review["rule_version"]) != 0:
        return {"ok": False,
                "reason": f"rule version mismatch (catalog {match.version} vs reviewed {review['rule_version']})",
                "catalog_rule": match}
    return {"ok": True, "reason": None, "catalog_rule": match}


# --- assignment --------------------------------------------------------------

def assign_reviewer(principal, review_id: int, *, expected_status: str,
                    reviewer_principal_id: int | None, reviewer_role: str,
                    reviewer_name: str | None, actor_user_id: int):
    """Assign a reviewer. Becomes ``pending_review`` only if the assigned principal has
    recorded authority for this rule/gate; otherwise ``blocked_pending_authorized_reviewer``.
    Never fabricates a reviewer name."""
    with engine.begin() as conn:
        review = _load_for_update(conn, review_id, expected_status)
        if review["status"] not in _ASSIGN_FROM:
            raise InvalidTransitionError(f"cannot assign from status {review['status']}")
        authority = reviewer_authority(
            reviewer_principal_id, rule_id=review["governing_rule"],
            policy_gate=review["policy_gate"])
        new_status = "pending_review" if authority is not None else "blocked_pending_authorized_reviewer"
        conn.execute(
            compliance_reviews.update().where(compliance_reviews.c.id == review_id).values(
                assigned_reviewer_principal_id=reviewer_principal_id,
                assigned_reviewer_role=reviewer_role,
                assigned_reviewer_name=reviewer_name,
                status=new_status,
                updated_at=_now(),
            )
        )
    return {"status": new_status, "authorized": authority is not None}


# --- decisions (append-only) -------------------------------------------------

def _require_comments(decision: str, comments: str | None, exceptions: str | None) -> None:
    if decision == "approved_with_conditions" and not (comments or exceptions):
        raise DecisionValidationError("approved_with_conditions requires comments or exceptions")
    if decision in ("returned", "declined") and not comments:
        raise DecisionValidationError(f"{decision} requires comments")


def _load_for_update(conn, review_id: int, expected_status: str | None):
    return load_for_update(
        conn, compliance_reviews, review_id, expected_status, noun="review",
        not_found_error=ComplianceReviewError, stale_error=StaleReviewError)


def record_decision(principal, review_id: int, *, decision: str, expected_status: str,
                    actor_user_id: int, reviewer_role: str | None = None,
                    reviewer_name: str | None = None, scope_reviewed: str | None = None,
                    comments: str | None = None, exceptions: str | None = None,
                    supersedes_decision_id: int | None = None):
    """Append an immutable decision and transition the review. Final approval requires a
    recorded ReviewerAuthority AND a Rule-Catalog version match; otherwise the review is
    moved to ``blocked_pending_authorized_reviewer`` and ``ApprovalBlockedError`` is
    raised (no approval decision recorded)."""
    if decision not in DECISION_TYPES:
        raise DecisionValidationError(f"unknown decision {decision!r}")
    _require_comments(decision, comments, exceptions)
    with engine.begin() as conn:
        review = _load_for_update(conn, review_id, expected_status)
        # A reconsideration (supersedes a prior decision) may also proceed from a
        # decided state; a fresh decision may only proceed from an open review state.
        allowed = _DECIDE_FROM if supersedes_decision_id is None else (_DECIDE_FROM | _DECIDED_STATES)
        if review["status"] not in allowed:
            raise InvalidTransitionError(f"cannot decide from status {review['status']}")

        if decision in _APPROVING:
            # Approval double-gate: catalog match + recorded reviewer authority.
            if review["status"] != "pending_review":
                _block(conn, review_id)
                raise ApprovalBlockedError("approval requires an assigned, authorized reviewer")
            catalog = validate_against_catalog(review)
            if not catalog["ok"]:
                _block(conn, review_id)
                raise ApprovalBlockedError(f"approval blocked: {catalog['reason']}")
            authority = reviewer_authority(
                review["assigned_reviewer_principal_id"],
                rule_id=review["governing_rule"], policy_gate=review["policy_gate"])
            if authority is None:
                _block(conn, review_id)
                raise ApprovalBlockedError(
                    "approval blocked: no authorized reviewer for this rule "
                    "(blocked_pending_authorized_reviewer)")
            reviewer_role = reviewer_role or authority["reviewer_role"]
            reviewer_name = reviewer_name or authority["reviewer_name"]

        now = _now()
        decision_row = conn.execute(
            compliance_decisions.insert().values(
                compliance_review_id=review_id,
                decision=decision,
                reviewer_role=reviewer_role,
                reviewer_name=reviewer_name,
                reviewer_principal_id=actor_user_id,
                decided_at=now,
                scope_reviewed=scope_reviewed,
                comments=comments,
                exceptions=exceptions,
                governing_rule=review["governing_rule"],
                rule_version=review["rule_version"],
                evidence_snapshot=review["evidence_snapshot"],
                supersedes_decision_id=supersedes_decision_id,
            ).returning(compliance_decisions.c.id)
        ).scalar_one()
        conn.execute(
            compliance_reviews.update().where(compliance_reviews.c.id == review_id).values(
                status=decision, updated_at=now)
        )
    return {"decision_id": decision_row, "status": decision}


def _block(conn, review_id: int) -> None:
    conn.execute(
        compliance_reviews.update().where(compliance_reviews.c.id == review_id).values(
            status="blocked_pending_authorized_reviewer", updated_at=_now())
    )


# --- reads (queue + detail), record-scoped -----------------------------------

def _scope_clause(principal, conn):
    """A SQL clause limiting reviews to the principal's accessible book, or None for a
    firm-wide reader. Returns ``False`` sentinel when the principal has no access."""
    ids = accessible_person_ids(conn, principal)
    if ids is None:
        return None  # record.read_all -> unrestricted
    if not ids:
        return "empty"
    return or_(
        compliance_reviews.c.person_id.in_(tuple(ids)),
        and_(compliance_reviews.c.person_id.is_(None),
             compliance_reviews.c.household_id.is_not(None),
             compliance_reviews.c.household_id.in_(
                 select(people.c.household_id).where(people.c.id.in_(tuple(ids))))),
    )


def list_reviews(principal, *, search=None, status=None, policy_gate=None,
                 recommendation_type=None, sort="submitted_at", descending=True,
                 page=1, page_size=25):
    """Record-scoped, filtered, sorted, paginated review queue. Returns
    ``{"rows": [...], "total": int, "page": int, "page_size": int, "pages": int}``."""
    sort_cols = {
        "submitted_at": compliance_reviews.c.submitted_at,
        "status": compliance_reviews.c.status,
        "recommendation_type": compliance_reviews.c.recommendation_type,
        "governing_rule": compliance_reviews.c.governing_rule,
        "rule_version": compliance_reviews.c.rule_version,
        "policy_gate": compliance_reviews.c.policy_gate,
    }
    col = sort_cols.get(sort, compliance_reviews.c.submitted_at)
    with engine.connect() as conn:
        scope = _scope_clause(principal, conn)
        if scope == "empty":
            return {"rows": [], "total": 0, "page": 1, "page_size": page_size, "pages": 0}
        conds = []
        if scope is not None:
            conds.append(scope)
        if status:
            conds.append(compliance_reviews.c.status == status)
        if policy_gate:
            conds.append(compliance_reviews.c.policy_gate == policy_gate)
        if recommendation_type:
            conds.append(compliance_reviews.c.recommendation_type == recommendation_type)
        if search:
            like = f"%{search.strip().lower()}%"
            conds.append(or_(
                func.lower(compliance_reviews.c.recommendation_id).like(like),
                func.lower(compliance_reviews.c.governing_rule).like(like),
                func.lower(compliance_reviews.c.recommendation_type).like(like)))
        where = and_(*conds) if conds else None
        total = conn.scalar(
            select(func.count()).select_from(compliance_reviews).where(where)
            if where is not None else select(func.count()).select_from(compliance_reviews))
        page, page_size = clamp_page(page, page_size)
        stmt = select(compliance_reviews)
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.order_by(col.desc() if descending else col.asc(),
                             compliance_reviews.c.id.desc())
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        rows = [dict(r) for r in conn.execute(stmt).mappings()]
    pages = page_count(total, page_size)
    return {"rows": rows, "total": total, "page": page, "page_size": page_size, "pages": pages}


def get_review(principal, review_id: int):
    """A single review (record-scoped) with its full, append-only decision history and
    the current Rule-Catalog validation. Returns ``None`` if not found or out of scope."""
    with engine.connect() as conn:
        review = conn.execute(
            select(compliance_reviews).where(compliance_reviews.c.id == review_id)
        ).mappings().first()
        if review is None:
            return None
        review = dict(review)
        # Scope-first: an inaccessible client's review is not exposed.
        pid, hid = review["person_id"], review["household_id"]
        if pid is not None and not record_in_scope(principal, "person", pid):
            return None
        if pid is None and hid is not None and not record_in_scope(principal, "household", hid):
            return None
        decisions = [dict(d) for d in conn.execute(
            select(compliance_decisions)
            .where(compliance_decisions.c.compliance_review_id == review_id)
            .order_by(compliance_decisions.c.decided_at.asc(), compliance_decisions.c.id.asc())
        ).mappings()]
    review["decisions"] = decisions
    review["catalog"] = validate_against_catalog(review)
    # Documents (Phase D.16) — Compliance may REFERENCE documents (read-only visibility); it
    # never owns them.
    if principal.can("documents.view"):
        from app.services.document_platform.relationships import documents_for_entity
        review["documents"] = documents_for_entity(principal, "compliance_review", review_id, limit=25)
    else:
        review["documents"] = None
    return review


def person_reviews(principal, person_id: int) -> list[dict]:
    """This client's compliance reviews (Phase D.11 composition read). Scope-first.

    Additive read for the Annual Review Workspace's "Compliance Summary" section
    (pending / blocked / completed counts + reviewer assignments). It reuses the
    same record-scope rule as the rest of this service and returns raw review rows;
    it recreates no compliance logic and leaves `list_reviews` / `get_review` /
    the decision lifecycle untouched. Bounded by one client's review volume.
    """
    if not record_in_scope(principal, "person", person_id):
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(compliance_reviews)
            .where(compliance_reviews.c.person_id == person_id)
            .order_by(compliance_reviews.c.submitted_at.desc(),
                      compliance_reviews.c.id.desc())
        ).mappings().all()
    return [dict(r) for r in rows]
