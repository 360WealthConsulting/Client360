"""Analytics metric registry (Phase D.15) — deterministic KPI definitions.

Each metric declares presentation metadata (label/category/unit/viz) and a deterministic compute
function that composes the source-reading layer. Executive metrics (firm-wide / revenue) require
``analytics.executive`` and are withheld (value None, restricted True) otherwise — server-side. No
AI; same inputs always yield the same output.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.analytics import sources


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    category: str          # revenue | pipeline | clients | production | operations | compliance | activity
    unit: str              # currency | percent | count | number
    viz: str               # card | gauge | trendline | leaderboard | ...
    executive: bool
    compute: object        # callable(principal) -> float|int|None


def _f(v):
    return float(v) if isinstance(v, Decimal) else v


def _num(v):
    return _f(v) if v is not None else 0


# --- deterministic compute helpers (guarded domain reads return None) --------

def _pipeline(principal):
    return sources.pipeline_report(principal)


def _safe(fn):
    def wrapped(principal):
        try:
            return fn(principal)
        except Exception:
            return None
    return wrapped


_DEFS = (
    # Revenue / AUM (executive).
    Metric("aum", "AUM", "revenue", "currency", "card", True,
           lambda p: _f(sources.book_aum(p))),
    Metric("forecast_revenue", "Forecast Revenue", "revenue", "currency", "card", True,
           lambda p: sources.forecast_report(p)["weighted_forecast_total"]),
    Metric("campaign_revenue", "Campaign Revenue", "revenue", "currency", "card", True,
           lambda p: sources.bizdev_summary(p)["campaign_revenue"]),
    Metric("referral_revenue", "Referral Revenue", "revenue", "currency", "card", True,
           lambda p: sources.bizdev_summary(p)["referral_revenue"]),
    Metric("total_bd_revenue", "Business Development Revenue", "revenue", "currency", "card", True,
           lambda p: (lambda s: s["campaign_revenue"] + s["referral_revenue"])(sources.bizdev_summary(p))),
    # Pipeline.
    Metric("pipeline_value", "Pipeline Value", "pipeline", "currency", "card", False,
           lambda p: _pipeline(p)["open_value"]),
    Metric("open_opportunities", "Open Opportunities", "pipeline", "count", "card", False,
           lambda p: _pipeline(p)["counts"]["open"]),
    Metric("won_opportunities", "Won Opportunities", "pipeline", "count", "card", False,
           lambda p: _pipeline(p)["counts"]["won"]),
    Metric("pipeline_conversion", "Pipeline Conversion", "pipeline", "percent", "gauge", False,
           lambda p: (lambda w: round(w * 100, 1) if w is not None else None)(_pipeline(p)["win_rate"])),
    # Business development.
    Metric("active_campaigns", "Active Campaigns", "operations", "count", "card", False,
           sources.active_campaign_count),
    Metric("active_referral_sources", "Active Referral Sources", "operations", "count", "card", False,
           sources.active_referral_source_count),
    # Clients / growth.
    Metric("client_count", "Clients", "clients", "count", "card", False, sources.client_count),
    Metric("household_count", "Households", "clients", "count", "card", False, sources.household_count),
    Metric("organization_count", "Organizations", "clients", "count", "card", True,
           sources.organization_count),
    # Advisor production / capacity / work.
    Metric("open_work", "Open Advisor Work", "production", "count", "card", False,
           sources.open_work_total),
    Metric("open_tasks", "Open Tasks", "production", "count", "card", False, sources.open_task_count),
    # Compliance.
    Metric("open_compliance_reviews", "Open Compliance Reviews", "compliance", "count", "card", False,
           sources.open_compliance_total),
    # Reviews / plans.
    Metric("annual_reviews", "Annual Review Sessions", "operations", "count", "card", False,
           sources.annual_review_count),
    Metric("annual_reviews_completed", "Completed Annual Reviews", "operations", "count", "card", False,
           lambda p: sources.annual_review_count(p, completed_only=True)),
    Metric("business_plans", "Business Plans", "operations", "count", "card", True,
           sources.business_plan_count),
    # Activity.
    Metric("timeline_activity", "Timeline Activity", "activity", "count", "card", False,
           sources.timeline_activity_count),
    Metric("document_count", "Documents", "operations", "count", "card", False,
           sources.document_count),
    # Tax / insurance (guarded — scoped; return None if unavailable to the principal).
    Metric("tax_engagements", "Tax Engagements", "operations", "count", "card", False,
           _safe(lambda p: sources.tax_dashboard(p)["metrics"]["engagements"])),
    Metric("tax_returns_due", "Tax Returns Due (30d)", "operations", "count", "card", False,
           _safe(lambda p: sources.tax_dashboard(p)["metrics"]["due_30_days"])),
    Metric("insurance_cases", "Insurance Cases", "operations", "count", "card", False,
           _safe(lambda p: sources.insurance_dashboard(p)["sections"]["pipeline"]["case_count"])),
)

METRICS: dict[str, Metric] = {m.key: m for m in _DEFS}


def list_metrics(principal=None):
    """Metric catalog (metadata only). Executive metrics are flagged; the compute step enforces
    the capability."""
    return [{"key": m.key, "label": m.label, "category": m.category, "unit": m.unit,
             "viz": m.viz, "executive": m.executive} for m in _DEFS]


def compute_metric(principal, metric_key: str) -> dict:
    m = METRICS.get(metric_key)
    if m is None:
        return {"key": metric_key, "value": None, "error": "unknown metric"}
    if m.executive and not principal.can("analytics.executive"):
        return {"key": m.key, "label": m.label, "unit": m.unit, "category": m.category,
                "viz": m.viz, "value": None, "restricted": True}
    value = m.compute(principal)
    return {"key": m.key, "label": m.label, "unit": m.unit, "category": m.category,
            "viz": m.viz, "value": (_num(value) if value is not None else None),
            "available": value is not None}


def compute_many(principal, metric_keys) -> list[dict]:
    return [compute_metric(principal, k) for k in metric_keys]
