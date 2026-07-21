"""Pipeline Intelligence (Phase D.13) — deterministic, not AI.

Deterministic observations over the principal's in-scope pipeline: aging, stalled, missing
next action, proposal overdue, high-value dormant, missing discovery, referral concentration,
advisor imbalance, closing-this-month forecast, and capacity warnings. Reuses the deterministic
"observation" idea from Advisor Intelligence but is OWNED by the Opportunity domain and is NOT
registered into the Advisor Intelligence ``_PRODUCERS`` seam — so the D.5 golden regression is
untouched (see ADR-018). Each observation is a plain, JSON-safe dict with a stable id; there is
no scoring model and no AI. Thresholds are fixed constants (documented), never learned.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal

from app.services.opportunity import service as svc

# Fixed, documented thresholds (no learned/tuned parameters).
AGING_DAYS = 90
STALLED_DAYS = 30
HIGH_VALUE = Decimal("100000")
REFERRAL_CONCENTRATION = 5      # >= N open opps from one referral source
ADVISOR_CAPACITY = 20           # > N open opps for one advisor -> capacity warning
_ADVANCED_STAGES = frozenset({"proposal", "waiting_on_client", "negotiation", "implementation"})
_DISCOVERY_STAGES = frozenset({"discovery_scheduled", "discovery_completed"})


def _num(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def _obs(kind, opp, title, summary, priority="medium"):
    return {"id": f"pipeline:{kind}:{opp['id']}", "kind": kind, "opportunity_id": opp["id"],
            "title": title, "summary": summary, "priority": priority}


def pipeline_intelligence(principal, *, today=None) -> dict:
    """Deterministic pipeline observations for the principal's in-scope OPEN pipeline.
    Returns per-opportunity observations plus book-level (advisor/referral/forecast) signals."""
    today = today or date.today()
    open_rows = svc.all_in_scope(principal, statuses=("open",))
    stage_codes = {s["id"]: s["code"] for p in svc.list_pipelines()
                   for s in svc.list_stages(p["id"])}
    last_activity = svc.latest_activity_dates([o["id"] for o in open_rows])

    per_opp = []
    for o in open_rows:
        code = stage_codes.get(o["stage_id"], "")
        age = (today - o["created_at"].date()).days
        if age >= AGING_DAYS:
            per_opp.append(_obs("aging", o, f"Aging opportunity — {o['title']}",
                                f"Open {age} days.", "high" if age >= 180 else "medium"))
        la = last_activity.get(o["id"])
        la_date = la.date() if la else o["created_at"].date()
        if (today - la_date).days >= STALLED_DAYS:
            per_opp.append(_obs("stalled", o, f"Stalled pipeline — {o['title']}",
                                f"No activity in {(today - la_date).days} days."))
        if not (o.get("next_action") or "").strip():
            per_opp.append(_obs("missing_next_action", o, f"Missing next action — {o['title']}",
                                "No next action recorded."))
        if code == "proposal" and o["expected_close_date"] and o["expected_close_date"] < today:
            per_opp.append(_obs("proposal_overdue", o, f"Proposal overdue — {o['title']}",
                                f"Expected close {o['expected_close_date']} has passed.", "high"))
        if code in _ADVANCED_STAGES and o.get("expected_revenue") is None:
            per_opp.append(_obs("missing_discovery", o, f"Missing discovery — {o['title']}",
                                "Advanced stage without expected revenue captured."))

    dormant = [o for o in svc.all_in_scope(principal, statuses=("dormant",))
               if _num(o["expected_revenue"]) >= HIGH_VALUE]
    per_opp += [_obs("high_value_dormant", o, f"High-value dormant — {o['title']}",
                     f"Dormant with expected revenue ${float(_num(o['expected_revenue'])):,.0f}.",
                     "high") for o in dormant]

    # Book-level signals.
    referral_counts = Counter((o.get("referral_source_person_id"), o.get("referral_source_text"))
                              for o in open_rows
                              if o.get("referral_source_person_id") or o.get("referral_source_text"))
    referral_concentration = [{"source": k, "count": n}
                              for k, n in referral_counts.items() if n >= REFERRAL_CONCENTRATION]
    advisor_counts = Counter(o["primary_advisor_id"] for o in open_rows)
    capacity_warnings = [{"advisor_id": a, "open_count": n}
                         for a, n in advisor_counts.items() if n > ADVISOR_CAPACITY]
    month = (today.year, today.month)
    closing_this_month = [o for o in open_rows if o["expected_close_date"]
                          and (o["expected_close_date"].year, o["expected_close_date"].month) == month]

    return {
        "observations": per_opp,
        "referral_concentration": referral_concentration,
        "advisor_capacity_warnings": capacity_warnings,
        "closing_this_month": {
            "count": len(closing_this_month),
            "expected_revenue": float(sum(_num(o["expected_revenue"]) for o in closing_this_month)),
        },
        "advisor_open_counts": dict(advisor_counts),
        "thresholds": {"aging_days": AGING_DAYS, "stalled_days": STALLED_DAYS,
                       "high_value": float(HIGH_VALUE), "advisor_capacity": ADVISOR_CAPACITY},
    }
