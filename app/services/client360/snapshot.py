"""Client 360 compact executive snapshot (Phase D.40) — a read-only summary a future AI can consume.

Composes existing scoped reads only; generates no recommendations and mutates nothing. Figures are
presented side by side (assets, revenue pipeline, tax, insurance, compliance, deadlines, open work,
last communication) — never summed into a single composite (units differ).
"""
from __future__ import annotations


def build(principal, ctx) -> dict:
    pid, hid = ctx.get("person_id"), ctx.get("household_id")
    portfolio = ctx.get("portfolio") or {}
    snap = ctx.get("snapshot") or {}

    revenue = _revenue(principal, pid)
    compliance = _compliance(principal, pid, hid)
    return {
        "kind": "client_snapshot",
        "entity_type": ctx["entity_type"],
        "entity_id": ctx["entity_id"],
        "assets": {"aum": portfolio.get("aum", portfolio.get("total_aum")) or 0,
                   "cash": portfolio.get("cash") or 0,
                   "household_aum": (portfolio.get("household") or {}).get("aum",
                                    (portfolio.get("household") or {}).get("total_aum")) or 0},
        "revenue": revenue,
        "tax": snap.get("tax") or {"active": 0},
        "insurance": snap.get("insurance") or {"policy_count": 0, "total_face": 0},
        "compliance": compliance,
        "open_work": _open_work(principal, pid),
        "open_exceptions": snap.get("open_exceptions", 0),
        "last_communication": ctx.get("last_contact"),
        "next_activity": ctx.get("next_activity"),
        "upcoming_deadlines": _deadlines(principal, pid),
        "not_summed": True,
    }


def _revenue(principal, pid):
    if not pid or not principal.can("opportunity.view"):
        return None
    try:
        from app.services.opportunity.service import opportunities_for_person
        rows = opportunities_for_person(principal, pid, open_only=True, limit=100)
        value = sum(float(r.get("expected_revenue") or 0) for r in rows)
        return {"open_opportunities": len(rows), "expected_revenue": value}
    except Exception:
        return None


def _open_work(principal, pid):
    if not pid or not principal.can("advisor_work.read"):
        return None
    try:
        from app.services.advisor_work import person_work
        return len(person_work(principal, pid, open_only=True))
    except Exception:
        return None


def _compliance(principal, pid, hid):
    if not pid or not principal.can("compliance.review.read"):
        return None
    try:
        from app.services.compliance.reviews import person_reviews
        open_states = {"pending_submission", "pending_assignment", "pending_review",
                       "blocked_pending_authorized_reviewer"}
        rows = person_reviews(principal, pid)
        return {"open_reviews": sum(1 for r in rows if r.get("status") in open_states)}
    except Exception:
        return None


def _deadlines(principal, pid):
    if not pid:
        return []
    try:
        from app.services.insurance import reviews_due_for_people
        return [{"type": r.get("review_type"), "due_date": str(r.get("due_date")),
                 "policy_id": r.get("policy_id")} for r in reviews_due_for_people({pid})[:5]]
    except Exception:
        return []
