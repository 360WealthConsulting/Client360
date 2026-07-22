"""Health checks/snapshots, diagnostics & runtime snapshots (Phase D.26) — metadata only.

Health checks are definitions; recording a snapshot updates the check's ``last_status`` and appends a
(regular, not immutable) snapshot row. Diagnostics mirror the same shape. A runtime snapshot REUSES
the existing readiness surface (``app.jobs.scheduler.scheduler_status`` + a DB/migration probe
mirroring ``app/routes/ops.py``) — it records a point-in-time metadata row and never reimplements the
runtime health logic. Sensitive diagnostic detail is server-side (gated by capability in-route).
Running scans requires ``observability.execute``.
"""
from __future__ import annotations

from sqlalchemy import func, select, text

from app.database.observability_tables import (
    DIAGNOSTIC_CATEGORIES,
    DIAGNOSTIC_STATUSES,
    HEALTH_CHECK_TYPES,
    HEALTH_STATUSES,
)
from app.db import engine
from app.db import observability_diagnostic_checks as diag_checks_t
from app.db import observability_diagnostic_results as diag_results_t
from app.db import observability_health_checks as checks_t
from app.db import observability_health_snapshots as snapshots_t
from app.db import observability_runtime_snapshots as runtime_t
from app.db import observability_services as services_t

from .common import ObservabilityError, ObservabilityNotFound, now, record_event

# --- health checks -----------------------------------------------------------

def list_health_checks(*, service_id=None, enabled=None):
    with engine.connect() as c:
        stmt = select(checks_t).order_by(checks_t.c.code)
        if service_id is not None:
            stmt = stmt.where(checks_t.c.service_id == service_id)
        if enabled is not None:
            stmt = stmt.where(checks_t.c.enabled.is_(bool(enabled)))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_health_check(principal, *, code, name, service_id=None, check_type="liveness",
                        target_reference=None, interval_seconds=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ObservabilityError("code and name are required")
    if check_type not in HEALTH_CHECK_TYPES:
        raise ObservabilityError(f"invalid check_type {check_type!r}")
    with engine.begin() as c:
        if c.scalar(select(checks_t.c.id).where(checks_t.c.code == code)) is not None:
            raise ObservabilityError(f"health check code {code!r} already exists")
        row = c.execute(checks_t.insert().values(
            code=code, name=name.strip(), service_id=service_id, check_type=check_type,
            target_reference=target_reference, interval_seconds=interval_seconds, enabled=True,
            last_status="unknown", created_by_user_id=actor_user_id).returning(*checks_t.c)).mappings().one()
        return dict(row)


def record_health_snapshot(principal, health_check_id: int, *, status, latency_ms=None, detail=None,
                           actor_user_id=None) -> dict:
    """Append a health snapshot and update the check's ``last_status``/``last_checked_at``."""
    if status not in HEALTH_STATUSES:
        raise ObservabilityError(f"invalid status {status!r}")
    ts = now()
    with engine.begin() as c:
        chk = c.execute(select(checks_t).where(checks_t.c.id == health_check_id)).mappings().first()
        if chk is None:
            raise ObservabilityNotFound(str(health_check_id))
        chk = dict(chk)
        row = c.execute(snapshots_t.insert().values(
            health_check_id=health_check_id, service_id=chk["service_id"], status=status,
            latency_ms=latency_ms, detail=detail, observed_at=ts).returning(*snapshots_t.c)).mappings().one()
        c.execute(checks_t.update().where(checks_t.c.id == health_check_id).values(
            last_status=status, last_checked_at=ts, updated_at=ts))
        record_event(c, entity_type="health_check", entity_id=health_check_id,
                     event_type=f"health_{status}", from_status=chk["last_status"], to_status=status,
                     actor_user_id=actor_user_id)
        return dict(row)


def list_health_snapshots(*, health_check_id=None, limit=100):
    with engine.connect() as c:
        stmt = select(snapshots_t).order_by(snapshots_t.c.id.desc()).limit(min(500, max(1, limit)))
        if health_check_id is not None:
            stmt = stmt.where(snapshots_t.c.health_check_id == health_check_id)
        return [dict(r) for r in c.execute(stmt).mappings()]


# --- diagnostics -------------------------------------------------------------

def list_diagnostic_checks(*, category=None, enabled=None):
    with engine.connect() as c:
        stmt = select(diag_checks_t).order_by(diag_checks_t.c.code)
        if category:
            stmt = stmt.where(diag_checks_t.c.category == category)
        if enabled is not None:
            stmt = stmt.where(diag_checks_t.c.enabled.is_(bool(enabled)))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_diagnostic_check(principal, *, code, name, category="other", target_reference=None,
                            description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ObservabilityError("code and name are required")
    if category not in DIAGNOSTIC_CATEGORIES:
        raise ObservabilityError(f"invalid category {category!r}")
    with engine.begin() as c:
        if c.scalar(select(diag_checks_t.c.id).where(diag_checks_t.c.code == code)) is not None:
            raise ObservabilityError(f"diagnostic check code {code!r} already exists")
        row = c.execute(diag_checks_t.insert().values(
            code=code, name=name.strip(), category=category, target_reference=target_reference,
            enabled=True, description=description, created_by_user_id=actor_user_id)
            .returning(*diag_checks_t.c)).mappings().one()
        return dict(row)


def record_diagnostic_result(principal, diagnostic_check_id: int, *, status, summary=None, detail=None,
                             actor_user_id=None) -> dict:
    if status not in DIAGNOSTIC_STATUSES:
        raise ObservabilityError(f"invalid status {status!r}")
    with engine.begin() as c:
        chk = c.execute(select(diag_checks_t.c.id).where(diag_checks_t.c.id == diagnostic_check_id)).first()
        if chk is None:
            raise ObservabilityNotFound(str(diagnostic_check_id))
        row = c.execute(diag_results_t.insert().values(
            diagnostic_check_id=diagnostic_check_id, status=status, summary=summary, detail=detail,
            ran_at=now()).returning(*diag_results_t.c)).mappings().one()
        record_event(c, entity_type="diagnostic", entity_id=diagnostic_check_id,
                     event_type=f"diagnostic_{status}", to_status=status, actor_user_id=actor_user_id)
        return dict(row)


def list_diagnostic_results(*, diagnostic_check_id=None, include_detail=False, limit=100):
    with engine.connect() as c:
        stmt = select(diag_results_t).order_by(diag_results_t.c.id.desc()).limit(min(500, max(1, limit)))
        if diagnostic_check_id is not None:
            stmt = stmt.where(diag_results_t.c.diagnostic_check_id == diagnostic_check_id)
        rows = [dict(r) for r in c.execute(stmt).mappings()]
    if not include_detail:
        # Sensitive diagnostic detail stays server-side unless the caller is authorized.
        for r in rows:
            r.pop("detail", None)
    return rows


# --- runtime snapshots (reuse the existing readiness surface) ----------------

def capture_runtime_snapshot(principal, *, environment_profile_id=None, deployment_reference_id=None,
                             actor_user_id=None) -> dict:
    """Capture a point-in-time runtime snapshot by REUSING the existing readiness logic: the DB probe,
    the Alembic head vs expected head, and ``scheduler_status()``. Records metadata only."""
    database_ok = False
    current_head = None
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
            database_ok = True
            current_head = c.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except Exception:
        database_ok = False

    expected_head = _expected_head()
    in_sync = bool(current_head) and (expected_head is None or current_head == expected_head)
    try:
        from app.jobs.scheduler import scheduler_status
        sched = scheduler_status()
    except Exception:
        sched = {"running": False, "job_count": 0}

    with engine.begin() as c:
        row = c.execute(runtime_t.insert().values(
            captured_at=now(), database_ok=database_ok, scheduler_running=bool(sched.get("running")),
            scheduler_job_count=int(sched.get("job_count") or 0), migration_head=current_head,
            migration_in_sync=in_sync, environment_profile_id=environment_profile_id,
            deployment_reference_id=deployment_reference_id,
            summary=("ready" if (database_ok and in_sync) else "not_ready"),
            created_by_user_id=actor_user_id).returning(*runtime_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="runtime_snapshot", entity_id=row["id"],
                     event_type="runtime_captured", actor_user_id=actor_user_id,
                     payload={"summary": row["summary"]})
    return row


def _expected_head():
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config()
        cfg.set_main_option("script_location", "migrations")
        heads = ScriptDirectory.from_config(cfg).get_heads()
        return heads[0] if len(heads) == 1 else "|".join(sorted(heads))
    except Exception:
        return None


def list_runtime_snapshots(*, limit=50):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(runtime_t).order_by(runtime_t.c.id.desc()).limit(min(200, max(1, limit)))).mappings()]


def metrics(principal) -> dict:
    with engine.connect() as c:
        failed_health = c.scalar(select(func.count()).select_from(checks_t)
                                 .where(checks_t.c.last_status.in_(("unhealthy", "degraded")))) or 0
        diag_failures = c.scalar(select(func.count()).select_from(diag_results_t)
                                 .where(diag_results_t.c.status.in_(("fail", "error")))) or 0
    return {"failed_health_checks": failed_health, "diagnostic_failures": diag_failures}


def unused_services_probe(principal) -> int:
    """Small helper used by scans: count services currently degraded/down (firm-level)."""
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(services_t)
                        .where(services_t.c.status.in_(("degraded", "down")))) or 0
