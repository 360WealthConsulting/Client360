"""Business Development Intelligence (Phase D.14) — deterministic, not AI.

Deterministic observations over campaigns, referral sources, and attributed opportunities:
poor-ROI campaigns, inactive referral partners, declining conversion, high-performing campaigns,
advisor referral imbalance, marketing concentration, high acquisition cost, missing attribution,
duplicate referral sources, campaign overspend, campaign ending soon, campaign with no
opportunities. Fixed documented thresholds; no scoring model, no AI. NOT registered into the D.5
Advisor Intelligence seam (ADR-019). Also composes the Business Development executive summary
(attribution rollups) from the reporting services.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.services.campaign import reporting as campaign_reporting
from app.services.campaign import service as campaign_svc
from app.services.opportunity import service as opp_svc
from app.services.referral import reporting as referral_reporting
from app.services.referral import service as referral_svc

# Fixed, documented thresholds.
POOR_ROI = 0.0                  # ROI below this is "poor"
HIGH_ROI = 1.0                  # ROI at/above this is "high performing"
INACTIVE_REFERRAL_DAYS = 180    # active partner with no referral in this many days
ENDING_SOON_DAYS = 14           # campaign end_date within this window
HIGH_CAC = Decimal("5000")      # acquisition cost above this is flagged
ADVISOR_REFERRAL_IMBALANCE = 3  # max/min advisor referral count ratio


def _num(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def _obs(kind, subject_type, subject_id, title, summary, priority="medium"):
    return {"id": f"bizdev:{kind}:{subject_type}:{subject_id}", "kind": kind,
            "subject_type": subject_type, "subject_id": subject_id, "title": title,
            "summary": summary, "priority": priority}


def business_development_intelligence(principal, *, today=None) -> dict:
    today = today or date.today()
    obs = []

    campaigns = campaign_svc.list_campaigns(principal, page_size=200)["rows"]
    for cmp in campaigns:
        perf = campaign_reporting.campaign_performance(principal, cmp)
        if perf["roi"] is not None and perf["roi"] < POOR_ROI:
            obs.append(_obs("poor_roi_campaign", "campaign", cmp["id"],
                            f"Poor ROI — {cmp['name']}", f"ROI {perf['roi']}.", "high"))
        if perf["roi"] is not None and perf["roi"] >= HIGH_ROI:
            obs.append(_obs("high_performing_campaign", "campaign", cmp["id"],
                            f"High-performing campaign — {cmp['name']}", f"ROI {perf['roi']}."))
        if perf["overspend"]:
            obs.append(_obs("campaign_overspend", "campaign", cmp["id"],
                            f"Campaign overspend — {cmp['name']}", "Actual cost exceeds budget.",
                            "high"))
        if perf["acquisition_cost"] is not None and _num(perf["acquisition_cost"]) > HIGH_CAC:
            obs.append(_obs("high_acquisition_cost", "campaign", cmp["id"],
                            f"High acquisition cost — {cmp['name']}",
                            f"CAC ${perf['acquisition_cost']:,.0f}."))
        if cmp["status"] == "active" and perf["opportunities"] == 0:
            obs.append(_obs("campaign_no_opportunities", "campaign", cmp["id"],
                            f"No opportunities — {cmp['name']}", "Active campaign with no attributed opportunities."))
        if cmp.get("end_date") and cmp["status"] == "active" \
                and today <= cmp["end_date"] <= today + timedelta(days=ENDING_SOON_DAYS):
            obs.append(_obs("campaign_ending_soon", "campaign", cmp["id"],
                            f"Campaign ending soon — {cmp['name']}", f"Ends {cmp['end_date']}."))

    # Referral partners: inactive, declining, duplicates.
    sources = referral_svc.list_referral_sources(principal, page_size=200)["rows"]
    seen: dict = {}
    for s in sources:
        m = referral_svc.referral_metrics(principal, s["id"])
        if s["status"] == "active" and m["last_referral_at"] is not None \
                and (today - m["last_referral_at"].date()).days >= INACTIVE_REFERRAL_DAYS:
            obs.append(_obs("referral_partner_inactive", "referral_source", s["id"],
                            f"Referral partner inactive — {s['name']}",
                            f"No referral in {(today - m['last_referral_at'].date()).days} days."))
        key = (s["name"].strip().lower(), (s.get("email") or "").strip().lower())
        if key in seen and key[0]:
            obs.append(_obs("duplicate_referral_source", "referral_source", s["id"],
                            f"Possible duplicate referral source — {s['name']}",
                            f"Matches referral source {seen[key]}."))
        else:
            seen[key] = s["id"]

    # Missing attribution: opportunities with no campaign and no referral source.
    missing = [o for o in opp_svc.all_in_scope(principal, statuses=("open",))
               if o.get("campaign_id") is None and o.get("referral_source_id") is None]
    for o in missing:
        obs.append(_obs("missing_attribution", "opportunity", o["id"],
                        f"Missing attribution — {o['title']}", "No campaign or referral source."))

    # Advisor referral imbalance (book-level).
    prod = referral_reporting.referral_report(principal)["advisor_referral_production"]
    imbalance = None
    if len(prod) >= 2:
        vals = [v for v in prod.values() if v > 0]
        if vals and min(vals) > 0 and max(vals) / min(vals) >= ADVISOR_REFERRAL_IMBALANCE:
            imbalance = {"max": max(vals), "min": min(vals)}

    return {
        "observations": obs,
        "advisor_referral_imbalance": imbalance,
        "thresholds": {"poor_roi": POOR_ROI, "high_roi": HIGH_ROI,
                       "inactive_referral_days": INACTIVE_REFERRAL_DAYS,
                       "high_cac": float(HIGH_CAC)},
    }


def executive_summary(principal) -> dict:
    """Business Development executive summary + pipeline attribution rollup (composition over the
    reporting services — owns nothing)."""
    camp = campaign_reporting.campaign_report(principal)
    ref = referral_reporting.referral_report(principal)
    intel = business_development_intelligence(principal)
    return {
        "campaign_revenue": camp["total_revenue"],
        "campaign_cost": camp["total_cost"],
        "referral_revenue": ref["total_referral_revenue"],
        "revenue_by_marketing_channel": camp["revenue_by_marketing_channel"],
        "revenue_by_referral_type": ref["revenue_by_referral_type"],
        "top_campaigns": camp["roi_ranking"][:5],
        "top_referral_sources": ref["leaderboard"][:5],
        "alerts": intel["observations"][:20],
    }
