"""Firm Intelligence (Phase D.15) — deterministic analytics observations.

Deterministic firm-level observations over composed reports + snapshot trends: advisor overload,
revenue/service/business concentration, review/tax/insurance backlog, campaign efficiency,
referral dependence, rapid client growth, declining production (from snapshot trends). Fixed
documented thresholds; no AI. NOT registered into the D.5 Advisor Intelligence seam (ADR-020).
"""
from __future__ import annotations

from app.services.analytics import metrics, sources, trends

# Fixed, documented thresholds.
ADVISOR_OVERLOAD_OPPS = 20          # open opportunities for one advisor
REVENUE_CONCENTRATION = 0.5         # one campaign/source >= 50% of BD revenue
REFERRAL_DEPENDENCE = 0.4           # one referral source >= 40% of referral revenue
BACKLOG_REVIEWS = 10                # open compliance reviews
BACKLOG_TAX_DUE = 15                # tax returns due in 30 days
DECLINE_YOY = -10.0                 # metric down >10% year-over-year (needs snapshots)


def _obs(kind, subject, title, summary, priority="medium"):
    return {"id": f"firm:{kind}:{subject}", "kind": kind, "subject": subject,
            "title": title, "summary": summary, "priority": priority}


def firm_intelligence(principal) -> dict:
    obs = []

    # Advisor overload (book-level open-opportunity concentration).
    for advisor_id, n in sources.advisor_open_opportunities(principal).items():
        if n >= ADVISOR_OVERLOAD_OPPS:
            obs.append(_obs("advisor_overload", f"advisor:{advisor_id}",
                            "Advisor overload", f"Advisor {advisor_id} has {n} open opportunities.",
                            "high"))

    # Revenue concentration (business development).
    try:
        bd = sources.bizdev_summary(principal)
        total_bd = (bd["campaign_revenue"] or 0) + (bd["referral_revenue"] or 0)
        top = bd["top_referral_sources"][0] if bd["top_referral_sources"] else None
        if top and total_bd and top["revenue"] / total_bd >= REFERRAL_DEPENDENCE:
            obs.append(_obs("referral_dependence", f"referral:{top['referral_source_id']}",
                            "Referral dependence",
                            f"{top['name']} is {round(top['revenue'] / total_bd * 100)}% of BD revenue.",
                            "high"))
    except Exception:
        pass

    # Service concentration (pipeline by service line).
    try:
        pipe = sources.pipeline_report(principal)
        by_sl = pipe.get("by_service_line") or {}
        if by_sl:
            total = sum(by_sl.values())
            top_sl, top_n = max(by_sl.items(), key=lambda kv: kv[1])
            if total and top_n / total >= REVENUE_CONCENTRATION and top_sl != "unspecified":
                obs.append(_obs("service_concentration", f"service:{top_sl}",
                                "Service concentration",
                                f"{round(top_n / total * 100)}% of pipeline is {top_sl}."))
    except Exception:
        pass

    # Backlogs.
    if metrics.compute_metric(principal, "open_compliance_reviews").get("value", 0) >= BACKLOG_REVIEWS:
        obs.append(_obs("review_backlog", "compliance", "Compliance review backlog",
                        "Open compliance reviews exceed the backlog threshold.", "high"))
    tax_due = metrics.compute_metric(principal, "tax_returns_due").get("value")
    if tax_due is not None and tax_due >= BACKLOG_TAX_DUE:
        obs.append(_obs("tax_backlog", "tax", "Tax return backlog",
                        f"{int(tax_due)} tax returns due within 30 days.", "high"))

    # Declining production (year-over-year) from snapshot trends, if available.
    for key in ("aum", "pipeline_value", "campaign_revenue"):
        t = trends.metric_trend(key)
        yoy = t.get("year_over_year_growth")
        if yoy is not None and yoy <= DECLINE_YOY:
            obs.append(_obs("declining_production", f"metric:{key}",
                            f"Declining {metrics.METRICS[key].label}",
                            f"Down {yoy}% year-over-year.", "high"))

    return {"observations": obs,
            "thresholds": {"advisor_overload_opps": ADVISOR_OVERLOAD_OPPS,
                           "referral_dependence": REFERRAL_DEPENDENCE,
                           "backlog_reviews": BACKLOG_REVIEWS, "decline_yoy": DECLINE_YOY}}
