"""Telemetry sources & metrics (Phase D.26) — metadata only; Analytics stays authoritative.

Telemetry sources reference existing run-ledgers (automation_runs, outbox, integration sync,
scheduler, security findings) — they copy no data. Telemetry metrics are definitions: kind, unit,
collection interval, warning/critical thresholds, aggregation, and an optional ``analytics_metric_key``
that *references* an Analytics ``Metric`` (Analytics remains authoritative for business analytics).
``collect_metric`` records a metric's ``last_value``/``last_collected_at`` (a deterministic recording
of a supplied/observed value) — it performs no business computation. Managing requires
``observability.manage``; collection requires ``observability.execute``.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.observability_tables import (
    AGGREGATIONS,
    METRIC_KINDS,
    TELEMETRY_SOURCE_TYPES,
)
from app.db import engine
from app.db import observability_telemetry_metrics as metrics_t
from app.db import observability_telemetry_sources as sources_t

from .common import ObservabilityError, ObservabilityNotFound, now, record_event

# --- telemetry sources -------------------------------------------------------

def list_sources(*, source_type=None, enabled=None):
    with engine.connect() as c:
        stmt = select(sources_t).order_by(sources_t.c.code)
        if source_type:
            stmt = stmt.where(sources_t.c.source_type == source_type)
        if enabled is not None:
            stmt = stmt.where(sources_t.c.enabled.is_(bool(enabled)))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_source(principal, *, code, name, source_type="custom", reference=None, description=None,
                  actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ObservabilityError("code and name are required")
    if source_type not in TELEMETRY_SOURCE_TYPES:
        raise ObservabilityError(f"invalid source_type {source_type!r}")
    with engine.begin() as c:
        if c.scalar(select(sources_t.c.id).where(sources_t.c.code == code)) is not None:
            raise ObservabilityError(f"telemetry source code {code!r} already exists")
        row = c.execute(sources_t.insert().values(
            code=code, name=name.strip(), source_type=source_type, reference=reference, enabled=True,
            description=description, created_by_user_id=actor_user_id).returning(*sources_t.c)).mappings().one()
        return dict(row)


# --- telemetry metrics -------------------------------------------------------

def list_metrics(*, telemetry_source_id=None, enabled=None):
    with engine.connect() as c:
        stmt = select(metrics_t).order_by(metrics_t.c.code)
        if telemetry_source_id is not None:
            stmt = stmt.where(metrics_t.c.telemetry_source_id == telemetry_source_id)
        if enabled is not None:
            stmt = stmt.where(metrics_t.c.enabled.is_(bool(enabled)))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_metric(principal, *, code, name, telemetry_source_id=None, metric_kind="gauge", unit=None,
                  collection_interval_seconds=None, warning_threshold=None, critical_threshold=None,
                  aggregation="last", analytics_metric_key=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ObservabilityError("code and name are required")
    if metric_kind not in METRIC_KINDS:
        raise ObservabilityError(f"invalid metric_kind {metric_kind!r}")
    if aggregation not in AGGREGATIONS:
        raise ObservabilityError(f"invalid aggregation {aggregation!r}")
    with engine.begin() as c:
        if c.scalar(select(metrics_t.c.id).where(metrics_t.c.code == code)) is not None:
            raise ObservabilityError(f"telemetry metric code {code!r} already exists")
        row = c.execute(metrics_t.insert().values(
            code=code, name=name.strip(), telemetry_source_id=telemetry_source_id, metric_kind=metric_kind,
            unit=unit, collection_interval_seconds=collection_interval_seconds,
            warning_threshold=warning_threshold, critical_threshold=critical_threshold,
            aggregation=aggregation, analytics_metric_key=analytics_metric_key, enabled=True,
            created_by_user_id=actor_user_id).returning(*metrics_t.c)).mappings().one()
        return dict(row)


def collect_metric(principal, metric_id: int, value: float, *, actor_user_id=None) -> dict:
    """Record a deterministic observed value for a telemetry metric (metadata only). Returns the row
    plus a ``breach`` verdict derived from the configured thresholds."""
    with engine.begin() as c:
        m = c.execute(select(metrics_t).where(metrics_t.c.id == metric_id)).mappings().first()
        if m is None:
            raise ObservabilityNotFound(str(metric_id))
        m = dict(m)
        row = c.execute(metrics_t.update().where(metrics_t.c.id == metric_id).values(
            last_value=float(value), last_collected_at=now(), updated_at=now())
            .returning(*metrics_t.c)).mappings().one()
        record_event(c, entity_type="telemetry_metric", entity_id=metric_id,
                     event_type="metric_collected", actor_user_id=actor_user_id,
                     payload={"value": float(value)})
        row = dict(row)
    return {**row, "breach": _breach(m, float(value))}


def _breach(metric: dict, value: float) -> str | None:
    crit = metric.get("critical_threshold")
    warn = metric.get("warning_threshold")
    if crit is not None and value >= crit:
        return "critical"
    if warn is not None and value >= warn:
        return "warning"
    return None


def metrics_summary(principal) -> dict:
    with engine.connect() as c:
        defined = c.scalar(select(func.count()).select_from(metrics_t)) or 0
        sources = c.scalar(select(func.count()).select_from(sources_t)
                           .where(sources_t.c.enabled.is_(True))) or 0
    return {"telemetry_metrics": defined, "telemetry_sources": sources}
