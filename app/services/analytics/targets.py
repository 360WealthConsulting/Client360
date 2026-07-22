"""Analytics targets, thresholds & variance (Phase D.15).

Executive targets/thresholds per metric/dimension/period (analytics-owned config). Variance
compares a live metric value to its target and classifies status by the metric's direction
(higher_is_better / lower_is_better) against warning/critical thresholds — deterministic.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select

from app.db import analytics_targets, engine
from app.services.analytics import metrics


class TargetError(Exception):
    """Invalid target definition."""


def _now():
    return datetime.now(UTC)


def set_target(principal, *, metric_key, actor_user_id, dimension_key=None, period="all",
               target_value=None, threshold_warning=None, threshold_critical=None,
               direction="higher_is_better", notes=None) -> dict:
    if metric_key not in metrics.METRICS:
        raise TargetError(f"unknown metric {metric_key!r}")
    if direction not in ("higher_is_better", "lower_is_better"):
        raise TargetError("invalid direction")
    with engine.begin() as c:
        existing = c.execute(select(analytics_targets).where(and_(
            analytics_targets.c.metric_key == metric_key,
            analytics_targets.c.dimension_key.is_(None) if dimension_key is None
            else analytics_targets.c.dimension_key == dimension_key,
            analytics_targets.c.period == period))).mappings().first()
        values = dict(target_value=target_value, threshold_warning=threshold_warning,
                      threshold_critical=threshold_critical, direction=direction,
                      notes=notes or "", updated_by=actor_user_id, updated_at=_now())
        if existing:
            c.execute(analytics_targets.update()
                      .where(analytics_targets.c.id == existing["id"]).values(**values))
            tid = existing["id"]
        else:
            tid = c.execute(analytics_targets.insert().values(
                metric_key=metric_key, dimension_key=dimension_key, period=period,
                created_by=actor_user_id, created_at=_now(), **values)
                .returning(analytics_targets.c.id)).scalar_one()
        return dict(c.execute(select(analytics_targets)
                              .where(analytics_targets.c.id == tid)).mappings().one())


def list_targets(principal) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(analytics_targets)
                                           .order_by(analytics_targets.c.metric_key)).mappings()]


def get_target(metric_key, *, dimension_key=None, period="all") -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(analytics_targets).where(and_(
            analytics_targets.c.metric_key == metric_key,
            analytics_targets.c.dimension_key.is_(None) if dimension_key is None
            else analytics_targets.c.dimension_key == dimension_key,
            analytics_targets.c.period == period))).mappings().first()
    return dict(row) if row else None


def variance(principal, metric_key, *, dimension_key=None, period="all") -> dict | None:
    """Compare the live metric value to its target; classify status deterministically."""
    target = get_target(metric_key, dimension_key=dimension_key, period=period)
    if target is None:
        return None
    metric = metrics.compute_metric(principal, metric_key)
    value = metric.get("value")
    result = {"metric_key": metric_key, "value": value,
              "target_value": (float(target["target_value"]) if target["target_value"] is not None else None),
              "direction": target["direction"], "status": "unknown", "variance": None}
    if value is None or target["target_value"] is None:
        return result
    tv = float(target["target_value"])
    result["variance"] = round(value - tv, 2)
    warn = float(target["threshold_warning"]) if target["threshold_warning"] is not None else None
    crit = float(target["threshold_critical"]) if target["threshold_critical"] is not None else None
    higher = target["direction"] == "higher_is_better"
    if crit is not None and ((value <= crit) if higher else (value >= crit)):
        result["status"] = "critical"
    elif warn is not None and ((value <= warn) if higher else (value >= warn)):
        result["status"] = "warning"
    elif (value >= tv) if higher else (value <= tv):
        result["status"] = "on_track"
    else:
        result["status"] = "below_target"
    return result
