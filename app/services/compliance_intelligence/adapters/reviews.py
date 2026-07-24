"""Supervisory reviews adapter (Phase D.47).

Composes the AUTHORITATIVE compliance review ledger (``compliance.reviews``) into supervisory items — open
reviews needing oversight — and derives a ``missing_compliance_approval`` compliance exception for blocked
reviews. It NEVER submits, assigns, decides, or otherwise mutates a review (that stays with
``compliance.reviews`` — the authoritative approval engine). Read-only, fail-closed.
"""
from __future__ import annotations

from .. import stats
from ..model import ComplianceException, SupervisoryItem


def _priority(status):
    if status == "blocked_pending_authorized_reviewer":
        return "critical"
    if status == "pending_review":
        return "high"
    return "medium"


def _action(status):
    return {"pending_submission": "Submit the review",
            "pending_assignment": "Assign an authorized reviewer",
            "pending_review": "Record the compliance decision",
            "blocked_pending_authorized_reviewer": "Record reviewer authority, then decide"}.get(
        status, "Open the review")


def _item_from_review(row):
    from .. import registry
    rtype = "annual_review_oversight" if "annual" in str(row.get("recommendation_type") or "").lower() \
        else "suitability_review"
    tdef = registry.review_type(rtype)
    status = row.get("status")
    rule = row.get("governing_rule") or "compliance rule"
    version = row.get("rule_version") or ""
    return SupervisoryItem(
        item_id=f"sup:{rtype}:review:{row.get('id')}",
        review_type=rtype, status=status, priority=_priority(status),
        title=f"{(row.get('recommendation_type') or 'Suitability').replace('_', ' ').title()} review",
        summary=f"Compliance review is {str(status).replace('_', ' ')}.",
        explanation=f"A compliance review is in status '{status}', governed by {rule}"
                    + (f" v{version}" if version else "") + ".",
        governing_policy=rule,
        evidence=tuple(e for e in (f"review_id={row.get('id')}", f"rule={rule}",
                                   f"version={version}" if version else None,
                                   f"policy_gate={row.get('policy_gate')}") if e),
        authoritative_owner="compliance.reviews",
        required_reviewer=row.get("assigned_reviewer_role") or (tdef.approval_authority if tdef else "compliance"),
        due_date=None, deep_link="/compliance/reviews", recommended_action=_action(status),
        related_person_id=row.get("person_id"), related_household_id=row.get("household_id"),
        metadata={"reviewer_name": None})


def review_items(principal, *, person_id=None):
    """Return (supervisory_items, exceptions) from open compliance reviews. Firm-scoped via list_reviews, or
    person-scoped via person_reviews. Never raises."""
    try:
        from app.services.compliance.reviews import OPEN_STATUSES
        if person_id is not None:
            from app.services.compliance.reviews import person_reviews
            rows = person_reviews(principal, person_id)
        else:
            from app.services.compliance.reviews import list_reviews
            rows = list_reviews(principal, page=1, page_size=200)["rows"]
    except Exception:
        stats.note("adapter_failures", source="compliance.reviews")
        return [], []
    items, exceptions = [], []
    for row in rows:
        if row.get("status") not in OPEN_STATUSES:
            continue
        item = _item_from_review(row)
        items.append(item)
        stats.note("reviews_composed", review_type=item.review_type)
        if row.get("status") == "blocked_pending_authorized_reviewer":
            exceptions.append(ComplianceException(
                exception_id=f"exc:missing_compliance_approval:review:{row.get('id')}",
                exception_type="missing_compliance_approval", severity="critical", status="open",
                title="Compliance approval blocked",
                summary="A review is blocked pending an authorized reviewer.",
                explanation="The review cannot be approved because no reviewer holds the required authority.",
                governing_policy="supervisory approval policy",
                evidence=(f"review_id={row.get('id')}", f"rule={row.get('governing_rule')}"),
                owner="compliance.reviews", escalation="compliance officer",
                deep_link="/compliance/reviews",
                related_person_id=row.get("person_id"), related_household_id=row.get("household_id")))
    return items, exceptions
