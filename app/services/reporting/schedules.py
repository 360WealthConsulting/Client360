"""Report schedules (Phase D.21) — deterministic scheduling metadata.

A report schedule is metadata: it names a report definition/dashboard, an export profile, a
frequency, and optional delivery references (a Communications conversation for delivery, a Workflow
instance that may own the trigger). No background scheduler/dispatcher is implemented here —
Workflow may schedule reports, and the existing job scheduler could drive ``due_schedules`` in a
future phase. ``generate_due`` marks a schedule run (advancing ``last_run_at``) and creates a
report run via the reporting service; it never recalculates KPIs.
"""
from __future__ import annotations

from sqlalchemy import select

from app.database.reporting_tables import SCHEDULE_FREQUENCIES
from app.db import engine
from app.db import report_schedules as schedules_t

from . import service
from .common import ReportingError, ReportingNotFound, now, record_event


def list_schedules(*, active_only: bool = False) -> list[dict]:
    with engine.connect() as c:
        stmt = select(schedules_t).order_by(schedules_t.c.id.desc())
        if active_only:
            stmt = stmt.where(schedules_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_schedule(schedule_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(schedules_t)
                        .where(schedules_t.c.id == schedule_id)).mappings().first()
        return dict(row) if row else None


def create_schedule(principal, *, name, frequency="manual", report_definition_id=None,
                    dashboard_id=None, export_profile_id=None, next_run_at=None, recipients=None,
                    conversation_id=None, workflow_instance_id=None, actor_user_id=None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ReportingError("name is required")
    if frequency not in SCHEDULE_FREQUENCIES:
        raise ReportingError(f"invalid frequency {frequency!r}")
    if report_definition_id is None and dashboard_id is None:
        raise ReportingError("a schedule must target a report definition or a dashboard")
    with engine.begin() as c:
        row = c.execute(schedules_t.insert().values(
            name=name, frequency=frequency, report_definition_id=report_definition_id,
            dashboard_id=dashboard_id, export_profile_id=export_profile_id, next_run_at=next_run_at,
            recipients=recipients, conversation_id=conversation_id,
            workflow_instance_id=workflow_instance_id, active=True,
            created_by_user_id=actor_user_id).returning(*schedules_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="schedule", entity_id=row["id"], event_type="schedule_created",
                     actor_user_id=actor_user_id, payload={"frequency": frequency})
        return row


def set_active(principal, schedule_id: int, active: bool, *, actor_user_id=None) -> dict:
    with engine.begin() as c:
        if c.scalar(select(schedules_t.c.id).where(schedules_t.c.id == schedule_id)) is None:
            raise ReportingNotFound(str(schedule_id))
        row = c.execute(schedules_t.update().where(schedules_t.c.id == schedule_id)
                        .values(active=bool(active), updated_at=now())
                        .returning(*schedules_t.c)).mappings().one()
        return dict(row)


def run_schedule(principal, schedule_id: int, *, actor_user_id=None) -> dict:
    """Generate a report run from a schedule (a Workflow action or an operator may call this).
    Advances ``last_run_at`` and creates a 'generated' report run via the reporting service."""
    with engine.begin() as c:
        sch = c.execute(select(schedules_t).where(schedules_t.c.id == schedule_id)).mappings().first()
        if sch is None:
            raise ReportingNotFound(str(schedule_id))
        sch = dict(sch)
        c.execute(schedules_t.update().where(schedules_t.c.id == schedule_id)
                  .values(last_run_at=now(), updated_at=now()))
        record_event(c, entity_type="schedule", entity_id=schedule_id, event_type="schedule_run",
                     actor_user_id=actor_user_id)
    report = service.create_report(
        principal, name=f"{sch['name']} (scheduled)",
        report_definition_id=sch["report_definition_id"], dashboard_id=sch["dashboard_id"],
        export_profile_id=sch["export_profile_id"], actor_user_id=actor_user_id)
    return service.generate_report(principal, report["id"], actor_user_id=actor_user_id)
