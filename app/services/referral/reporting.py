"""Referral reporting (Phase D.14).

Referral leaderboard / conversion / revenue / LTV / average close time / revenue by referral
type / advisor referral production — all computed from attributed opportunities (scoped to the
principal's pipeline). Nothing is stored.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from app.services.referral import service as referral_svc


def _num(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def referral_report(principal) -> dict:
    """Leaderboard + conversion + revenue by referral type + advisor referral production."""
    sources = referral_svc.list_referral_sources(principal, page_size=200)["rows"]
    leaderboard = []
    revenue_by_type: dict[str, float] = defaultdict(float)
    advisor_production: dict[int, float] = defaultdict(float)
    advisor_won: Counter = Counter()
    advisor_total: Counter = Counter()
    for s in sources:
        m = referral_svc.referral_metrics(principal, s["id"])
        leaderboard.append({
            "referral_source_id": s["id"], "name": s["name"], "source_type": s["source_type"],
            "status": s["status"], "won": m["won_referrals"], "total": m["total_referrals"],
            "conversion_rate": m["conversion_rate"], "revenue": m["actual_revenue"],
            "lifetime_value": m["lifetime_value"],
            "average_close_time_days": m["average_close_time_days"]})
        revenue_by_type[s["source_type"]] += m["actual_revenue"]
        adv = s.get("primary_advisor_id")
        if adv is not None:
            advisor_production[adv] += m["actual_revenue"]
            advisor_won[adv] += m["won_referrals"]
            advisor_total[adv] += m["total_referrals"]
    leaderboard.sort(key=lambda x: (x["revenue"], x["won"]), reverse=True)
    advisor_conversion = {a: (round(advisor_won[a] / advisor_total[a], 4) if advisor_total[a] else None)
                          for a in advisor_total}
    return {
        "leaderboard": leaderboard,
        "revenue_by_referral_type": {k: round(v, 2) for k, v in revenue_by_type.items()},
        "advisor_referral_production": {k: round(v, 2) for k, v in advisor_production.items()},
        "advisor_referral_conversion": advisor_conversion,
        "total_referral_revenue": round(sum(x["revenue"] for x in leaderboard), 2),
    }
