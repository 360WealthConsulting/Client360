"""Capacity planning, workload & utilization (Phase D.20) — deterministic metadata only.

Capacity plans are persisted per-resource, per-period allocation records (planned / actual /
available minutes, department, role). Workload and utilization are **computed deterministically**
from open operational tasks' time estimates against a resource's declared per-day capacity — there
is **no optimization engine and no AI recommendation**, just plain arithmetic. Managing plans
requires ``operations.templates``; reads require ``operations.view`` (enforced in-route).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import capacity_plans as plans_t
from app.db import engine
from app.db import operational_resources as resources_t
from app.db import operational_tasks as tasks_t

from .common import OperationsError, OperationsNotFound

_OPEN_STATUSES = ("planned", "active", "blocked", "on_hold")


def _now():
    return datetime.now(UTC)


# --- capacity plans ----------------------------------------------------------

def create_capacity_plan(*, resource_id, period_start, period_end, planned_minutes=0,
                         actual_minutes=0, available_minutes=0, department=None, notes=None,
                         actor_user_id=None) -> dict:
    if period_start is None or period_end is None:
        raise OperationsError("period_start and period_end are required")
    if period_end < period_start:
        raise OperationsError("period_end must be on or after period_start")
    with engine.begin() as c:
        if c.scalar(select(resources_t.c.id).where(resources_t.c.id == resource_id)) is None:
            raise OperationsNotFound(f"resource {resource_id}")
        exists = c.scalar(select(plans_t.c.id).where(
            plans_t.c.resource_id == resource_id, plans_t.c.period_start == period_start,
            plans_t.c.period_end == period_end))
        if exists is not None:
            raise OperationsError("a capacity plan already exists for that resource and period")
        row = c.execute(plans_t.insert().values(
            resource_id=resource_id, period_start=period_start, period_end=period_end,
            planned_minutes=int(planned_minutes), actual_minutes=int(actual_minutes),
            available_minutes=int(available_minutes), department=department, notes=notes,
            created_by_user_id=actor_user_id).returning(*plans_t.c)).mappings().one()
        return dict(row)


def update_capacity_plan(plan_id: int, *, planned_minutes=None, actual_minutes=None,
                         available_minutes=None, notes=None) -> dict:
    values: dict = {"updated_at": _now()}
    for key, val in (("planned_minutes", planned_minutes), ("actual_minutes", actual_minutes),
                     ("available_minutes", available_minutes)):
        if val is not None:
            values[key] = int(val)
    if notes is not None:
        values["notes"] = notes
    with engine.begin() as c:
        if c.scalar(select(plans_t.c.id).where(plans_t.c.id == plan_id)) is None:
            raise OperationsNotFound(f"capacity plan {plan_id}")
        row = c.execute(plans_t.update().where(plans_t.c.id == plan_id)
                        .values(**values).returning(*plans_t.c)).mappings().one()
        return dict(row)


def list_capacity_plans(*, resource_id=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(plans_t).order_by(plans_t.c.period_start.desc(), plans_t.c.id.desc())
        if resource_id is not None:
            stmt = stmt.where(plans_t.c.resource_id == resource_id)
        return [dict(r) for r in c.execute(stmt).mappings()]


# --- workload / utilization (computed, deterministic) ------------------------

def resource_workload(resource_id: int) -> dict:
    """Deterministic committed workload from OPEN operational tasks assigned to the resource."""
    with engine.connect() as c:
        res = c.execute(select(resources_t).where(resources_t.c.id == resource_id)).mappings().first()
        if res is None:
            raise OperationsNotFound(f"resource {resource_id}")
        committed = c.scalar(select(func.coalesce(func.sum(tasks_t.c.estimated_minutes), 0)).where(
            tasks_t.c.assigned_resource_id == resource_id,
            tasks_t.c.status.in_(_OPEN_STATUSES))) or 0
        open_count = c.scalar(select(func.count()).select_from(tasks_t).where(
            tasks_t.c.assigned_resource_id == resource_id,
            tasks_t.c.status.in_(_OPEN_STATUSES))) or 0
    return {"resource_id": resource_id, "resource_name": res["name"],
            "committed_minutes": int(committed), "open_task_count": int(open_count)}


def resource_utilization(resource_id: int, *, available_minutes: int | None = None) -> dict:
    """Deterministic utilization = committed / available (available defaults to the resource's
    declared per-day capacity). No optimization; plain arithmetic."""
    workload = resource_workload(resource_id)
    with engine.connect() as c:
        res = c.execute(select(resources_t).where(resources_t.c.id == resource_id)).mappings().first()
    avail = available_minutes if available_minutes is not None else int(res["capacity_minutes_per_day"])
    committed = workload["committed_minutes"]
    utilization = (committed / avail) if avail > 0 else 1.0
    return {**workload, "available_minutes": avail,
            "remaining_minutes": max(avail - committed, 0),
            "utilization_percent": round(utilization * 100, 1),
            "over_capacity": committed > avail}


def capacity_overview(principal, *, department=None) -> dict:
    """Firm-level utilization across active resources (Analytics-style read; no client scope)."""
    with engine.connect() as c:
        stmt = select(resources_t).where(resources_t.c.active.is_(True)).order_by(resources_t.c.code)
        if department:
            stmt = stmt.where(resources_t.c.department == department)
        res_rows = [dict(r) for r in c.execute(stmt).mappings()]
    rows = [resource_utilization(r["id"]) for r in res_rows]
    over = [r for r in rows if r["over_capacity"]]
    return {"resources": rows, "resource_count": len(rows), "over_capacity_count": len(over)}


def over_capacity_count(principal) -> int:
    return capacity_overview(principal)["over_capacity_count"]
