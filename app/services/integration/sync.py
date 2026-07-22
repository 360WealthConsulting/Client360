"""Synchronization profiles, runs & conflicts (Phase D.24) — metadata only.

Integration owns synchronization METADATA (profiles, mapping versions, runs, health, conflicts);
**Automation executes synchronization** and the existing importers/M365 jobs move the data.
``run_sync`` records a run's metadata (status/health/counts) and may reference the existing run
ledgers (``import_jobs``, ``automation_runs``, ``microsoft_accounts``) — it performs no provider I/O
and duplicates no provider logic. Approved ``sync_completed``/``sync_failed`` events publish to the
timeline only for client-anchored runs; firm-level runs record to ``integration_events`` only.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, func, select

from app.database.integration_tables import (
    CONFLICT_RESOLUTIONS,
    CONNECTOR_DIRECTIONS,
    RUN_STATUSES,
)
from app.db import engine
from app.db import integration_connectors as connectors_t
from app.db import integration_sync_conflicts as conflicts_t
from app.db import integration_sync_profiles as profiles_t
from app.db import integration_sync_runs as runs_t

from .common import (
    IntegrationError,
    IntegrationNotFound,
    now,
    publish_timeline,
    record_event,
    write_audit,
)

_FREQ_SECONDS = {"hourly": 3600, "daily": 86400, "weekly": 604800, "monthly": 2592000}


# --- sync profiles -----------------------------------------------------------

def list_sync_profiles(*, connector_id=None, active_only=False):
    with engine.connect() as c:
        stmt = select(profiles_t).order_by(profiles_t.c.code)
        if connector_id is not None:
            stmt = stmt.where(profiles_t.c.connector_id == connector_id)
        if active_only:
            stmt = stmt.where(profiles_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_sync_profile(principal, *, connector_id, code, name, direction="inbound", mapping=None,
                        entity_types=None, transformation=None, retry_policy_id=None,
                        failure_policy_id=None, schedule_frequency=None, next_sync_at=None,
                        actor_user_id=None) -> dict:
    if not (name or "").strip():
        raise IntegrationError("name is required")
    if direction not in CONNECTOR_DIRECTIONS:
        raise IntegrationError(f"invalid direction {direction!r}")
    code = (code or "").strip()
    if not code:
        raise IntegrationError("code is required")
    with engine.begin() as c:
        if c.scalar(select(connectors_t.c.id).where(connectors_t.c.id == connector_id)) is None:
            raise IntegrationError("connector not found")
        if c.scalar(select(profiles_t.c.id).where(profiles_t.c.code == code)) is not None:
            raise IntegrationError(f"sync profile code {code!r} already exists")
        row = c.execute(profiles_t.insert().values(
            connector_id=connector_id, code=code, name=name.strip(), direction=direction,
            mapping=mapping, entity_types=entity_types, transformation=transformation,
            mapping_version=1, retry_policy_id=retry_policy_id, failure_policy_id=failure_policy_id,
            schedule_frequency=schedule_frequency, next_sync_at=next_sync_at, active=True,
            sync_health="unknown", created_by_user_id=actor_user_id).returning(*profiles_t.c)).mappings().one()
        return dict(row)


def update_mapping(principal, sync_profile_id: int, mapping: dict, *, actor_user_id=None) -> dict:
    """Update a sync profile's mapping and **increment its mapping version** deterministically."""
    with engine.begin() as c:
        prof = c.execute(select(profiles_t).where(profiles_t.c.id == sync_profile_id)).mappings().first()
        if prof is None:
            raise IntegrationNotFound(str(sync_profile_id))
        new_version = int(prof["mapping_version"]) + 1
        row = c.execute(profiles_t.update().where(profiles_t.c.id == sync_profile_id).values(
            mapping=mapping, mapping_version=new_version, updated_at=now()).returning(*profiles_t.c)).mappings().one()
        record_event(c, entity_type="sync_profile", entity_id=sync_profile_id,
                     event_type="mapping_updated", actor_user_id=actor_user_id,
                     payload={"mapping_version": new_version})
        return dict(row)


# --- sync runs ---------------------------------------------------------------

def list_sync_runs(*, sync_profile_id=None, status=None, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        conds = []
        if sync_profile_id is not None:
            conds.append(runs_t.c.sync_profile_id == sync_profile_id)
        if status:
            conds.append(runs_t.c.status == status)
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(runs_t)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(runs_t)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(runs_t.c.id.desc()).limit(page_size).offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size}


def get_sync_run(principal, run_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(runs_t).where(runs_t.c.id == run_id)).mappings().first()
        return dict(row) if row else None


def run_sync(principal, sync_profile_id: int, *, trigger_source="manual", status="succeeded",
             records_read=0, records_written=0, records_skipped=0, records_failed=0, last_error=None,
             import_jobs_id=None, automation_run_id=None, microsoft_account_id=None, person_id=None,
             household_id=None, actor_user_id=None) -> dict:
    """Record a synchronization RUN (metadata only — no provider I/O). Updates the profile's
    ``last_sync_at`` and ``sync_health`` deterministically."""
    if status not in RUN_STATUSES:
        raise IntegrationError(f"invalid status {status!r}")
    ts = now()
    with engine.begin() as c:
        prof = c.execute(select(profiles_t).where(profiles_t.c.id == sync_profile_id)).mappings().first()
        if prof is None:
            raise IntegrationNotFound(str(sync_profile_id))
        prof = dict(prof)
        run = c.execute(runs_t.insert().values(
            sync_profile_id=sync_profile_id, connector_id=prof["connector_id"], direction=prof["direction"],
            status=status, records_read=records_read, records_written=records_written,
            records_skipped=records_skipped, records_failed=records_failed, import_jobs_id=import_jobs_id,
            automation_run_id=automation_run_id, microsoft_account_id=microsoft_account_id,
            trigger_source=trigger_source, triggered_by_user_id=actor_user_id, person_id=person_id,
            household_id=household_id, started_at=ts, finished_at=ts, last_error=last_error)
            .returning(*runs_t.c)).mappings().one()
        run = dict(run)
        health = "healthy" if status == "succeeded" else ("degraded" if status == "partial" else "failed")
        c.execute(profiles_t.update().where(profiles_t.c.id == sync_profile_id).values(
            last_sync_at=ts, sync_health=health, next_sync_at=_next_sync(prof, ts), updated_at=ts))
        record_event(c, entity_type="sync_run", entity_id=run["id"], event_type=f"sync_{status}",
                     to_status=status, actor_user_id=actor_user_id,
                     payload={"records_written": records_written})
    write_audit(f"integration.sync_{status}", entity_type="sync_run", entity_id=run["id"],
                actor_user_id=actor_user_id, metadata={"profile": prof["code"]})
    publish_timeline(run, "sync_completed" if status == "succeeded" else "sync_failed",
                     title=f"Sync {status}: {prof['name']}")
    return run


def _next_sync(profile: dict, base):
    secs = _FREQ_SECONDS.get(profile.get("schedule_frequency"))
    return base + timedelta(seconds=secs) if secs else None


def run_due_syncs(principal, *, actor_user_id=None) -> dict:
    """Automation entry point: record a run for every due sync profile (metadata only). The actual
    data movement is performed by the existing importers/M365 jobs — Integration records the run."""
    with engine.connect() as c:
        due = list(c.scalars(select(profiles_t.c.id).where(and_(
            profiles_t.c.active.is_(True), profiles_t.c.next_sync_at.is_not(None),
            profiles_t.c.next_sync_at <= now()))))
    recorded = 0
    for pid in due:
        try:
            run_sync(principal, pid, trigger_source="automation", actor_user_id=actor_user_id)
            recorded += 1
        except Exception:
            continue
    return {"due": len(due), "recorded": recorded}


# --- conflicts ---------------------------------------------------------------

def record_conflict(principal, sync_run_id: int, *, entity_type, entity_id=None, field_name=None,
                    source_value=None, target_value=None, actor_user_id=None) -> dict:
    with engine.begin() as c:
        if c.scalar(select(runs_t.c.id).where(runs_t.c.id == sync_run_id)) is None:
            raise IntegrationNotFound(str(sync_run_id))
        row = c.execute(conflicts_t.insert().values(
            sync_run_id=sync_run_id, entity_type=entity_type, entity_id=entity_id, field_name=field_name,
            source_value=source_value, target_value=target_value, resolution="unresolved")
            .returning(*conflicts_t.c)).mappings().one()
        record_event(c, entity_type="sync_run", entity_id=sync_run_id, event_type="conflict_recorded",
                     actor_user_id=actor_user_id, payload={"field": field_name})
        return dict(row)


def resolve_conflict(principal, conflict_id: int, resolution: str, *, actor_user_id=None) -> dict:
    if resolution not in CONFLICT_RESOLUTIONS:
        raise IntegrationError(f"invalid resolution {resolution!r}")
    with engine.begin() as c:
        conf = c.execute(select(conflicts_t).where(conflicts_t.c.id == conflict_id)).mappings().first()
        if conf is None:
            raise IntegrationNotFound(str(conflict_id))
        row = c.execute(conflicts_t.update().where(conflicts_t.c.id == conflict_id).values(
            resolution=resolution, resolved_by_user_id=actor_user_id, resolved_at=now())
            .returning(*conflicts_t.c)).mappings().one()
        return dict(row)


def list_conflicts(*, sync_run_id=None, resolution=None):
    with engine.connect() as c:
        stmt = select(conflicts_t).order_by(conflicts_t.c.id.desc())
        if sync_run_id is not None:
            stmt = stmt.where(conflicts_t.c.sync_run_id == sync_run_id)
        if resolution:
            stmt = stmt.where(conflicts_t.c.resolution == resolution)
        return [dict(r) for r in c.execute(stmt).mappings()]


def metrics(principal) -> dict:
    with engine.connect() as c:
        failed = c.scalar(select(func.count()).select_from(runs_t)
                          .where(runs_t.c.status.in_(("failed", "partial")))) or 0
        unhealthy = c.scalar(select(func.count()).select_from(connectors_t)
                             .where(connectors_t.c.status == "error")) or 0
        unresolved = c.scalar(select(func.count()).select_from(conflicts_t)
                              .where(conflicts_t.c.resolution == "unresolved")) or 0
    return {"sync_failures": failed, "connector_errors": unhealthy, "unresolved_conflicts": unresolved}
