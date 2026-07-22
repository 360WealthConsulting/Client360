"""Automation configuration catalog (Phase D.22) — policies, queues, windows, job templates.

Firm-level automation configuration gated by ``automation.manage``. Retry policies mirror the
notification ``RetryPolicy`` (max attempts + delays). Failure policies decide what happens when the
retry budget is exhausted. Queues and execution/maintenance windows are deterministic metadata. Job
templates are reusable job scaffolds.
"""
from __future__ import annotations

from sqlalchemy import select

from app.database.automation_tables import (
    FAILURE_ACTIONS,
    JOB_CATEGORIES,
    JOB_TYPES,
    WINDOW_TYPES,
)
from app.db import automation_failure_policies as failure_t
from app.db import automation_job_templates as templates_t
from app.db import automation_queues as queues_t
from app.db import automation_retry_policies as retry_t
from app.db import automation_windows as windows_t
from app.db import engine

from .common import AutomationError


def _create_unique(table, code, values):
    code = (code or "").strip()
    if not code:
        raise AutomationError("code is required")
    with engine.begin() as c:
        if c.scalar(select(table.c.id).where(table.c.code == code)) is not None:
            raise AutomationError(f"code {code!r} already exists")
        return dict(c.execute(table.insert().values(code=code, **values)
                              .returning(*table.c)).mappings().one())


def _list(table, *, active_only=False):
    with engine.connect() as c:
        stmt = select(table).order_by(table.c.code)
        if active_only and "active" in table.c:
            stmt = stmt.where(table.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def _get(table, *, code):
    with engine.connect() as c:
        row = c.execute(select(table).where(table.c.code == code)).mappings().first()
        return dict(row) if row else None


# --- retry policies ----------------------------------------------------------

def list_retry_policies():
    return _list(retry_t)


def get_retry_policy(*, code):
    return _get(retry_t, code=code)


def create_retry_policy(*, code, name, max_attempts=3, retry_delays=None, backoff_base_seconds=30,
                        description=None, actor_user_id=None):
    if not (name or "").strip():
        raise AutomationError("name is required")
    if int(max_attempts) < 1:
        raise AutomationError("max_attempts must be >= 1")
    return _create_unique(retry_t, code, {
        "name": name.strip(), "max_attempts": int(max_attempts), "retry_delays": retry_delays,
        "backoff_base_seconds": int(backoff_base_seconds), "description": description,
        "created_by_user_id": actor_user_id})


# --- failure policies --------------------------------------------------------

def list_failure_policies():
    return _list(failure_t)


def create_failure_policy(*, code, name, on_failure="retry", max_failures=5, alert_channel=None,
                          description=None, actor_user_id=None):
    if not (name or "").strip():
        raise AutomationError("name is required")
    if on_failure not in FAILURE_ACTIONS:
        raise AutomationError(f"invalid on_failure {on_failure!r}")
    return _create_unique(failure_t, code, {
        "name": name.strip(), "on_failure": on_failure, "max_failures": int(max_failures),
        "alert_channel": alert_channel, "description": description, "created_by_user_id": actor_user_id})


# --- queues ------------------------------------------------------------------

def list_queues():
    return _list(queues_t)


def create_queue(*, code, name, max_concurrency=1, description=None, actor_user_id=None):
    if not (name or "").strip():
        raise AutomationError("name is required")
    if int(max_concurrency) < 1:
        raise AutomationError("max_concurrency must be >= 1")
    return _create_unique(queues_t, code, {
        "name": name.strip(), "max_concurrency": int(max_concurrency), "description": description,
        "created_by_user_id": actor_user_id})


# --- execution / maintenance windows -----------------------------------------

def list_windows(*, window_type=None):
    with engine.connect() as c:
        stmt = select(windows_t).order_by(windows_t.c.code)
        if window_type:
            stmt = stmt.where(windows_t.c.window_type == window_type)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_window(*, code, name, window_type="execution", days_of_week=None, start_time=None,
                  end_time=None, description=None, actor_user_id=None):
    if not (name or "").strip():
        raise AutomationError("name is required")
    if window_type not in WINDOW_TYPES:
        raise AutomationError(f"invalid window_type {window_type!r}")
    return _create_unique(windows_t, code, {
        "name": name.strip(), "window_type": window_type, "days_of_week": days_of_week,
        "start_time": start_time, "end_time": end_time, "description": description, "active": True,
        "created_by_user_id": actor_user_id})


# --- job templates -----------------------------------------------------------

def list_templates(*, active_only=False):
    return _list(templates_t, active_only=active_only)


def get_template(*, code):
    return _get(templates_t, code=code)


def create_template(*, code, name, job_type="maintenance", category="general", description=None,
                    default_config=None, retry_policy_id=None, actor_user_id=None):
    if not (name or "").strip():
        raise AutomationError("name is required")
    if job_type not in JOB_TYPES:
        raise AutomationError(f"invalid job_type {job_type!r}")
    if category not in JOB_CATEGORIES:
        raise AutomationError(f"invalid category {category!r}")
    return _create_unique(templates_t, code, {
        "name": name.strip(), "job_type": job_type, "category": category, "description": description,
        "default_config": default_config, "retry_policy_id": retry_policy_id, "active": True,
        "created_by_user_id": actor_user_id})
