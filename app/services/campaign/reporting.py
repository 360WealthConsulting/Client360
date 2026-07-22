"""Campaign reporting (Phase D.14).

Campaign performance / ROI / conversion / revenue computed from opportunities ATTRIBUTED to a
campaign (scoped to the principal's pipeline via the Opportunity service). No campaign metrics
are stored on the campaign; they are always live. Sensitive money fields (budget/actual cost)
are surfaced only through this reporting surface, gated by ``campaign.report`` at the route.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.campaign import service as campaign_svc
from app.services.opportunity import service as opp_svc


def _num(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def campaign_performance(principal, campaign: dict) -> dict:
    """Per-campaign performance from attributed opportunities."""
    opps = opp_svc.opportunities_for_campaign(principal, campaign["id"])
    won = [o for o in opps if o["status"] == "won"]
    lost = [o for o in opps if o["status"] == "lost"]
    open_ = [o for o in opps if o["status"] == "open"]
    closed = len(won) + len(lost)
    revenue = sum(_num(o["expected_revenue"]) for o in won)
    pipeline = sum(_num(o["expected_revenue"]) for o in open_)
    cost = _num(campaign.get("actual_cost") if campaign.get("actual_cost") is not None
                else campaign.get("budget"))
    roi = (float((revenue - cost) / cost) if cost else None)
    cac = (float(cost / len(won)) if won and cost else None)
    return {
        "campaign_id": campaign["id"], "name": campaign["name"], "status": campaign["status"],
        "opportunities": len(opps), "won": len(won), "lost": len(lost), "open": len(open_),
        "conversion_rate": (round(len(won) / closed, 4) if closed else None),
        "revenue": float(revenue), "pipeline_value": float(pipeline),
        "budget": float(_num(campaign.get("budget"))),
        "actual_cost": float(_num(campaign.get("actual_cost"))),
        "roi": (round(roi, 4) if roi is not None else None),
        "acquisition_cost": (round(cac, 2) if cac is not None else None),
        "overspend": bool(campaign.get("budget") is not None and campaign.get("actual_cost") is not None
                          and _num(campaign["actual_cost"]) > _num(campaign["budget"])),
        "marketing_channel": campaign.get("marketing_channel"),
        "campaign_type": campaign.get("campaign_type"),
    }


def campaign_report(principal) -> dict:
    """Firm/book campaign report: per-campaign performance + revenue by channel/type + ROI
    ranking + overspend flags."""
    campaigns = campaign_svc.list_campaigns(principal, page_size=200)["rows"]
    perf = [campaign_performance(principal, c) for c in campaigns]
    by_channel: dict[str, float] = {}
    by_type: dict[str, float] = {}
    for p in perf:
        by_channel[p["marketing_channel"] or "unspecified"] = \
            by_channel.get(p["marketing_channel"] or "unspecified", 0.0) + p["revenue"]
        by_type[p["campaign_type"] or "unspecified"] = \
            by_type.get(p["campaign_type"] or "unspecified", 0.0) + p["revenue"]
    ranked = sorted((p for p in perf if p["roi"] is not None), key=lambda x: x["roi"], reverse=True)
    return {
        "campaigns": perf,
        "total_revenue": round(sum(p["revenue"] for p in perf), 2),
        "total_cost": round(sum(p["actual_cost"] for p in perf), 2),
        "revenue_by_marketing_channel": {k: round(v, 2) for k, v in by_channel.items()},
        "revenue_by_campaign_type": {k: round(v, 2) for k, v in by_type.items()},
        "roi_ranking": [{"campaign_id": p["campaign_id"], "name": p["name"], "roi": p["roi"]}
                        for p in ranked],
        "overspend": [{"campaign_id": p["campaign_id"], "name": p["name"]}
                      for p in perf if p["overspend"]],
    }
