"""Automation runner (Phase D.22) — workers, heartbeats, and the single-instance tick.

``run_worker_cycle`` is the deterministic tick the existing APScheduler drives (via ONE new gated
job): it sweeps due schedules into runs, drains runnable runs through ``service.execute_run``, and
records a worker heartbeat. It is single-instance (no distributed lock, no new threads) — the same
model as the outbox dispatcher and notification worker. Nothing here runs unless
``automation_enabled()`` registers the tick or a caller invokes it explicitly.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, select

from app.db import automation_runs as runs_t
from app.db import automation_schedules as schedules_t
from app.db import automation_worker_heartbeats as heartbeats_t
from app.db import automation_workers as workers_t
from app.db import engine

from . import service
from .common import now, system_principal

# Deterministic frequency → seconds (for advancing next_run_at when interval_seconds is unset).
_FREQ_SECONDS = {"hourly": 3600, "daily": 86400, "weekly": 604800, "monthly": 2592000,
                 "quarterly": 7776000}


# --- workers + heartbeats ----------------------------------------------------

def ensure_worker(*, code="scheduler", name="Scheduler worker", worker_type="scheduler") -> dict:
    ts = now()
    with engine.begin() as c:
        row = c.execute(select(workers_t).where(workers_t.c.code == code)).mappings().first()
        if row is None:
            row = c.execute(workers_t.insert().values(
                code=code, name=name, worker_type=worker_type, status="active", started_at=ts,
                last_heartbeat_at=ts).returning(*workers_t.c)).mappings().one()
        else:
            c.execute(workers_t.update().where(workers_t.c.id == row["id"])
                      .values(status="active", last_heartbeat_at=ts, updated_at=ts))
        return dict(row)


def heartbeat(worker_id: int, *, active_runs=0, detail=None) -> None:
    ts = now()
    with engine.begin() as c:
        c.execute(workers_t.update().where(workers_t.c.id == worker_id)
                  .values(last_heartbeat_at=ts, updated_at=ts))
        c.execute(heartbeats_t.insert().values(
            worker_id=worker_id, heartbeat_at=ts, active_runs=int(active_runs), detail=detail))


def list_workers() -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(workers_t).order_by(workers_t.c.code)).mappings()]


def worker_heartbeats(worker_id: int, *, limit=50) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(heartbeats_t).where(heartbeats_t.c.worker_id == worker_id)
            .order_by(heartbeats_t.c.id.desc()).limit(limit)).mappings()]


# --- schedule sweep ----------------------------------------------------------

def due_schedules() -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(schedules_t).where(and_(
            schedules_t.c.active.is_(True), schedules_t.c.frequency != "manual",
            schedules_t.c.next_run_at.is_not(None),
            schedules_t.c.next_run_at <= now())).order_by(schedules_t.c.next_run_at)).mappings()]


def _next_run(schedule: dict, base):
    if schedule.get("interval_seconds"):
        return base + timedelta(seconds=int(schedule["interval_seconds"]))
    secs = _FREQ_SECONDS.get(schedule.get("frequency"))
    return base + timedelta(seconds=secs) if secs else None


def advance_schedule(schedule_id: int) -> None:
    ts = now()
    with engine.begin() as c:
        sch = c.execute(select(schedules_t).where(schedules_t.c.id == schedule_id)).mappings().first()
        if sch is None:
            return
        c.execute(schedules_t.update().where(schedules_t.c.id == schedule_id)
                  .values(last_run_at=ts, next_run_at=_next_run(dict(sch), ts), updated_at=ts))


# --- the tick ----------------------------------------------------------------

def run_worker_cycle(*, worker_code="scheduler", limit=50) -> dict:
    """One deterministic automation tick. Sweeps due schedules into runs, drains runnable runs, and
    heartbeats. Returns a content summary. Failure-isolated per run."""
    worker = ensure_worker(code=worker_code)
    enqueued = 0
    for sch in due_schedules():
        principal = system_principal(sch.get("created_by_user_id"))
        try:
            service.enqueue_run(principal, sch["job_id"], trigger_source="schedule",
                                schedule_id=sch["id"], worker_id=worker["id"],
                                actor_user_id=sch.get("created_by_user_id"))
            advance_schedule(sch["id"])
            enqueued += 1
        except Exception:
            continue

    # Drain runnable runs (pending + backoff elapsed), oldest first.
    with engine.connect() as c:
        runnable = list(c.scalars(select(runs_t.c.id).where(and_(
            runs_t.c.status == "pending",
            (runs_t.c.available_at.is_(None)) | (runs_t.c.available_at <= now())))
            .order_by(runs_t.c.id).limit(limit)))
    executed, succeeded, failed = 0, 0, 0
    for run_id in runnable:
        try:
            result = service.execute_run(run_id, worker_id=worker["id"], worker_code=worker_code)
            executed += 1
            if result.get("status") == "succeeded":
                succeeded += 1
            elif result.get("status") in ("dead", "failed"):
                failed += 1
        except Exception:
            continue

    heartbeat(worker["id"], active_runs=0,
              detail={"enqueued": enqueued, "executed": executed})
    return {"worker": worker_code, "enqueued": enqueued, "executed": executed,
            "succeeded": succeeded, "failed": failed}
