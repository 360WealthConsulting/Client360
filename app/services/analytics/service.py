"""Analytics service (Phase D.15) — snapshot capture + export-ready data models.

Snapshot capture computes a metric's current value and persists it (prospective history — the
only way trends accrue, since most source domains hold current values only). Export produces an
export-ready row model for a set of metrics. Dashboard/target/widget CRUD live in their modules.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select

from app.db import analytics_snapshots, engine
from app.services.analytics import metrics, trends


class SnapshotError(Exception):
    """Snapshot could not be captured (e.g. metric value unavailable)."""


def _now():
    return datetime.now(UTC)


def capture_snapshot(principal, *, metric_key, actor_user_id, dimension_key=None,
                     period_key=None) -> dict:
    """Compute a metric's current value and store it as a prospective snapshot. Idempotent per
    (metric, dimension, period) — recapturing a period overwrites its value."""
    if metric_key not in metrics.METRICS:
        raise SnapshotError(f"unknown metric {metric_key!r}")
    result = metrics.compute_metric(principal, metric_key)
    if result.get("value") is None:
        raise SnapshotError("metric value is unavailable or restricted")
    pk = period_key or trends.period_key(_now().date(), "month")
    with engine.begin() as c:
        existing = c.execute(select(analytics_snapshots.c.id).where(and_(
            analytics_snapshots.c.metric_key == metric_key,
            analytics_snapshots.c.dimension_key.is_(None) if dimension_key is None
            else analytics_snapshots.c.dimension_key == dimension_key,
            analytics_snapshots.c.period_key == pk))).scalar()
        if existing:
            c.execute(analytics_snapshots.update().where(analytics_snapshots.c.id == existing)
                      .values(value=result["value"], captured_at=_now(), captured_by=actor_user_id))
            sid = existing
        else:
            sid = c.execute(analytics_snapshots.insert().values(
                metric_key=metric_key, dimension_key=dimension_key, period_key=pk,
                value=result["value"], captured_at=_now(), captured_by=actor_user_id)
                .returning(analytics_snapshots.c.id)).scalar_one()
        return dict(c.execute(select(analytics_snapshots)
                              .where(analytics_snapshots.c.id == sid)).mappings().one())


def capture_all(principal, *, actor_user_id, period_key=None) -> dict:
    """Capture snapshots for every metric currently available to the principal (skips
    restricted/unavailable). Used by a scheduled or manual firm-snapshot action."""
    captured, skipped = [], []
    for key in metrics.METRICS:
        try:
            capture_snapshot(principal, metric_key=key, actor_user_id=actor_user_id,
                             period_key=period_key)
            captured.append(key)
        except SnapshotError:
            skipped.append(key)
    return {"captured": captured, "skipped": skipped}


def export_metrics(principal, metric_keys=None) -> dict:
    """Export-ready data model: a flat list of metric rows for the given keys (or all). Values
    respect executive gating (restricted metrics carry value None)."""
    keys = list(metric_keys) if metric_keys else list(metrics.METRICS)
    rows = [metrics.compute_metric(principal, k) for k in keys]
    return {"generated_at": _now().isoformat(),
            "columns": ["metric_key", "label", "category", "unit", "value", "restricted"],
            "rows": [{"metric_key": r["key"], "label": r.get("label"), "category": r.get("category"),
                      "unit": r.get("unit"), "value": r.get("value"),
                      "restricted": r.get("restricted", False)} for r in rows]}
