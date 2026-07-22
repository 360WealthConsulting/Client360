"""Analytics trend engine (Phase D.15) — deterministic period math.

Trends come from two deterministic sources: (1) accumulated ``analytics_snapshots`` (the general
mechanism, since most source domains hold current values only — no fabricated history), and (2)
timestamped source facts that genuinely have history (e.g. opportunity close dates). Supports
day/week/month/quarter/year bucketing, rolling/moving averages, and growth (MoM/QoQ/YoY).
"""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from sqlalchemy import select

from app.db import analytics_snapshots, engine


def period_key(d, granularity: str) -> str:
    """Deterministic period bucket key for a date/datetime."""
    if granularity == "year":
        return f"{d.year}"
    if granularity == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if granularity == "week":
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if granularity == "day":
        return d.strftime("%Y-%m-%d")
    return f"{d.year}-{d.month:02d}"    # month (default)


def growth_pct(current, prior):
    """Percent growth from prior to current; None if prior is missing/zero."""
    if prior in (None, 0, 0.0):
        return None
    return round((float(current) - float(prior)) / abs(float(prior)) * 100, 2)


def moving_average(values, window: int):
    """Rolling moving average of a numeric series (deterministic; None where window not full)."""
    out = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
        else:
            chunk = values[i + 1 - window:i + 1]
            out.append(round(sum(chunk) / window, 2))
    return out


def snapshot_series(metric_key: str, *, dimension_key=None, limit=24) -> list[dict]:
    """Ordered snapshot series (oldest→newest) for a metric/dimension from analytics_snapshots."""
    with engine.connect() as c:
        rows = c.execute(
            select(analytics_snapshots.c.period_key, analytics_snapshots.c.value)
            .where(analytics_snapshots.c.metric_key == metric_key,
                   analytics_snapshots.c.dimension_key.is_(None) if dimension_key is None
                   else analytics_snapshots.c.dimension_key == dimension_key)
            .order_by(analytics_snapshots.c.period_key.desc()).limit(limit)).all()
    series = [{"period": pk, "value": float(v)} for pk, v in reversed(rows)]
    return series


def metric_trend(metric_key: str, *, dimension_key=None, limit=24, ma_window=3) -> dict:
    """Trend for a metric from its snapshot history: series, moving average, and
    period-over-period + year-over-year growth."""
    series = snapshot_series(metric_key, dimension_key=dimension_key, limit=limit)
    values = [s["value"] for s in series]
    ma = moving_average(values, ma_window) if len(values) >= ma_window else [None] * len(values)
    pop = growth_pct(values[-1], values[-2]) if len(values) >= 2 else None
    yoy = growth_pct(values[-1], values[-13]) if len(values) >= 13 else None
    return {"metric_key": metric_key, "dimension_key": dimension_key, "series": series,
            "moving_average": ma, "period_over_period_growth": pop, "year_over_year_growth": yoy,
            "points": len(series)}


def opportunity_revenue_trend(principal, *, granularity="month") -> dict:
    """Won-opportunity revenue bucketed by close period — a deterministic trend from real
    timestamps (closed_at), scoped to the principal's pipeline. No snapshots required."""
    from app.services.opportunity import service as opp_svc
    rows = opp_svc.all_in_scope(principal, statuses=("won",))
    buckets: OrderedDict[str, float] = OrderedDict()
    for o in sorted((r for r in rows if r.get("closed_at")), key=lambda r: r["closed_at"]):
        key = period_key(o["closed_at"].date(), granularity)
        rev = float(o["expected_revenue"]) if isinstance(o["expected_revenue"], Decimal) \
            else (o["expected_revenue"] or 0)
        buckets[key] = buckets.get(key, 0.0) + rev
    series = [{"period": k, "value": round(v, 2)} for k, v in buckets.items()]
    values = [s["value"] for s in series]
    return {"granularity": granularity, "series": series,
            "period_over_period_growth": growth_pct(values[-1], values[-2]) if len(values) >= 2 else None}
