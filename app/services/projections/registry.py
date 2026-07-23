"""Projection registry (Phase D.36) — discovery, ownership, lifecycle, versions, dependencies.

The durable, discoverable catalog of the read-model projections (``projection_definitions``). Supports
discovery (list/get/by-category), ownership + category, lifecycle status (active/deprecated/retired),
schema versioning, the dependency graph between projections, subscribed events, and coverage. This
performs no projection processing — the engine drives that.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.projection_tables import DEFINITION_STATUSES
from app.db import engine, projection_definitions, projection_state

from .common import now
from .definitions import CATEGORIES, PROJECTION_DEFINITIONS


def list_definitions(*, status=None, category=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(projection_definitions).order_by(
            projection_definitions.c.category, projection_definitions.c.projection_id)
        if status:
            stmt = stmt.where(projection_definitions.c.status == status)
        if category:
            stmt = stmt.where(projection_definitions.c.category == category)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_definition(projection_id) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(projection_definitions).where(
            projection_definitions.c.projection_id == projection_id)).mappings().first()
        return dict(row) if row else None


def dependency_graph() -> dict:
    return {d["projection_id"]: list(d.get("depends_on") or []) for d in list_definitions()}


def coverage() -> dict:
    """Projection coverage + health breakdown. Event coverage = domain-event contracts consumed by at
    least one projection ÷ total contracts (the activity feed consumes all events)."""
    with engine.connect() as c:
        rows = list(c.execute(select(projection_definitions.c.status,
                    func.count().label("n")).group_by(projection_definitions.c.status)).mappings())
        cats = set(c.scalars(select(projection_definitions.c.category).where(
            projection_definitions.c.status == "active")))
        health = {r["health"]: r["n"] for r in c.execute(select(
            projection_state.c.health, func.count().label("n")).group_by(projection_state.c.health)).mappings()}
    counts = {r["status"]: r["n"] for r in rows}
    active = counts.get("active", 0)
    total = active + counts.get("deprecated", 0) + counts.get("retired", 0)
    consumed = _consumed_event_types()
    all_contracts = _all_contract_types()
    event_cov = round((len(consumed & all_contracts) / len(all_contracts)) * 100, 1) if all_contracts else 100.0
    return {"total": total, "active": active, "deprecated": counts.get("deprecated", 0),
            "retired": counts.get("retired", 0), "categories": len(CATEGORIES),
            "categories_covered": len(cats & set(CATEGORIES)), "by_health": health,
            "healthy": health.get("healthy", 0), "lagging": health.get("lagging", 0),
            "failed": health.get("failed", 0), "unbuilt": health.get("unbuilt", 0),
            "events_consumed": len(consumed & all_contracts), "event_contracts": len(all_contracts),
            "event_coverage_pct": event_cov,
            "coverage_pct": round((len(cats & set(CATEGORIES)) / len(CATEGORIES)) * 100, 1) if CATEGORIES else 100.0}


def _consumed_event_types() -> set:
    consumed = set()
    for d in PROJECTION_DEFINITIONS.values():
        if d.all_events:
            return _all_contract_types()      # the activity feed consumes everything
        consumed |= set(d.subscribed_events)
    return consumed


def _all_contract_types() -> set:
    try:
        from app.services.events.contracts import EVENT_CONTRACTS
        return set(EVENT_CONTRACTS)
    except Exception:
        return set()


def adoption(principal=None) -> dict:
    from .engine import stats
    cov = coverage()
    return {"registry": cov, "runtime": stats(), "coverage_pct": cov["coverage_pct"],
            "event_coverage_pct": cov["event_coverage_pct"]}


# --- lifecycle ---------------------------------------------------------------

def _set_status(projection_id, status) -> dict:
    if status not in DEFINITION_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with engine.begin() as c:
        row = c.execute(projection_definitions.update().where(
            projection_definitions.c.projection_id == projection_id).values(
                status=status, deprecated_at=(now() if status == "deprecated" else None),
                updated_at=now()).returning(*projection_definitions.c)).mappings().first()
        if row is None:
            raise ValueError(f"unknown projection {projection_id!r}")
        return dict(row)


def deprecate(projection_id) -> dict:
    return _set_status(projection_id, "deprecated")


def retire(projection_id) -> dict:
    return _set_status(projection_id, "retired")


def definitions_index() -> dict:
    return dict(PROJECTION_DEFINITIONS)
