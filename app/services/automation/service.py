"""Automation service (Phase D.22) — jobs, schedules, runs, and the execution core.

Owns execution metadata and dispatches jobs to existing services via ``dispatch`` — never
duplicating business logic. Execution is deterministic and single-instance: a run acquires an
execution lock (single-flight), dispatches to the job_type handler with a **system principal**
(firm-wide reads), records run history, and applies the job's retry/failure policy on error
(mirroring the transactional outbox's attempts + backoff + dead-letter model). Authorization to
trigger a run is the human principal's (``automation.execute``); the dispatch executes with system
authority. Approved lifecycle events publish to the timeline only for client-anchored runs.
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select

from app.database.automation_tables import (
    JOB_CATEGORIES,
    JOB_STATUSES,
    JOB_TYPES,
    SCHEDULE_FREQUENCIES,
    SCHEDULE_TYPES,
)
from app.db import automation_execution_locks as locks_t
from app.db import automation_jobs as jobs_t
from app.db import automation_retry_policies as retry_t
from app.db import automation_runs as runs_t
from app.db import automation_schedules as schedules_t
from app.db import engine, people
from app.security.authorization import accessible_person_ids, record_in_scope

from . import dispatch
from .common import (
    AutomationError,
    AutomationNotFound,
    audit_history,
    now,
    publish_timeline,
    record_event,
    system_principal,
)

_RUNNABLE = ("pending", "queued", "failed")


# --- locks (single-flight; single-instance, no distributed lock) -------------

def acquire_lock(c, lock_key: str, *, owner: str, ttl_seconds: int = 300, run_id=None) -> bool:
    from datetime import timedelta
    ts = now()
    existing = c.execute(select(locks_t).where(locks_t.c.lock_key == lock_key)).mappings().first()
    if existing is None:
        c.execute(locks_t.insert().values(lock_key=lock_key, owner=owner, acquired_at=ts,
                                          expires_at=ts + timedelta(seconds=ttl_seconds), run_id=run_id))
        return True
    if existing["expires_at"] is None or existing["expires_at"] < ts:
        c.execute(locks_t.update().where(locks_t.c.lock_key == lock_key).values(
            owner=owner, acquired_at=ts, expires_at=ts + timedelta(seconds=ttl_seconds), run_id=run_id))
        return True
    return False


def release_lock(c, lock_key: str):
    c.execute(locks_t.delete().where(locks_t.c.lock_key == lock_key))


# --- jobs --------------------------------------------------------------------

def list_jobs(principal, *, status=None, category=None, job_type=None, search=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(jobs_t)
        if status:
            stmt = stmt.where(jobs_t.c.status == status)
        if category:
            stmt = stmt.where(jobs_t.c.category == category)
        if job_type:
            stmt = stmt.where(jobs_t.c.job_type == job_type)
        if search:
            stmt = stmt.where(jobs_t.c.name.ilike(f"%{search.strip()}%"))
        return [dict(r) for r in c.execute(stmt.order_by(jobs_t.c.code)).mappings()]


def _load_job(c, job_id: int) -> dict:
    row = c.execute(select(jobs_t).where(jobs_t.c.id == job_id)).mappings().first()
    if row is None:
        raise AutomationNotFound(str(job_id))
    return dict(row)


def get_job(principal, job_id: int) -> dict | None:
    with engine.connect() as c:
        try:
            j = _load_job(c, job_id)
        except AutomationNotFound:
            return None
        j["schedules"] = [dict(r) for r in c.execute(
            select(schedules_t).where(schedules_t.c.job_id == job_id)
            .order_by(schedules_t.c.id)).mappings()]
        j["recent_runs"] = [dict(r) for r in c.execute(
            select(runs_t).where(runs_t.c.job_id == job_id)
            .order_by(runs_t.c.id.desc()).limit(20)).mappings()]
    return j


def create_job(principal, *, code, name, job_type="maintenance", category="general", config=None,
               description=None, retry_policy_id=None, failure_policy_id=None, queue_id=None,
               window_id=None, priority=100, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise AutomationError("code and name are required")
    if job_type not in JOB_TYPES:
        raise AutomationError(f"invalid job_type {job_type!r}")
    if job_type not in dispatch.DISPATCH_REGISTRY:
        raise AutomationError(f"job_type {job_type!r} has no dispatch handler")
    if category not in JOB_CATEGORIES:
        raise AutomationError(f"invalid category {category!r}")
    with engine.begin() as c:
        if c.scalar(select(jobs_t.c.id).where(jobs_t.c.code == code)) is not None:
            raise AutomationError(f"job code {code!r} already exists")
        j = c.execute(jobs_t.insert().values(
            code=code, name=name.strip(), job_type=job_type, category=category, config=config,
            description=description, status="enabled", priority=int(priority),
            retry_policy_id=retry_policy_id, failure_policy_id=failure_policy_id, queue_id=queue_id,
            window_id=window_id, owner_user_id=actor_user_id, created_by_user_id=actor_user_id)
            .returning(*jobs_t.c)).mappings().one()
        j = dict(j)
        record_event(c, entity_type="job", entity_id=j["id"], event_type="job_created",
                     actor_user_id=actor_user_id, payload={"job_type": job_type})
        return j


def set_job_status(principal, job_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in JOB_STATUSES:
        raise AutomationError(f"invalid status {status!r}")
    with engine.begin() as c:
        j = _load_job(c, job_id)
        row = c.execute(jobs_t.update().where(jobs_t.c.id == job_id)
                        .values(status=status, updated_at=now()).returning(*jobs_t.c)).mappings().one()
        record_event(c, entity_type="job", entity_id=job_id, event_type=f"job_{status}",
                     from_status=j["status"], to_status=status, actor_user_id=actor_user_id)
        return dict(row)


# --- schedules ---------------------------------------------------------------

def list_schedules(*, job_id=None, active_only=False) -> list[dict]:
    with engine.connect() as c:
        stmt = select(schedules_t).order_by(schedules_t.c.id.desc())
        if job_id is not None:
            stmt = stmt.where(schedules_t.c.job_id == job_id)
        if active_only:
            stmt = stmt.where(schedules_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_schedule(principal, job_id: int, *, name, schedule_type="interval", frequency="manual",
                    interval_seconds=None, cron_expression=None, next_run_at=None,
                    actor_user_id=None) -> dict:
    if not (name or "").strip():
        raise AutomationError("name is required")
    if schedule_type not in SCHEDULE_TYPES:
        raise AutomationError(f"invalid schedule_type {schedule_type!r}")
    if frequency not in SCHEDULE_FREQUENCIES:
        raise AutomationError(f"invalid frequency {frequency!r}")
    with engine.begin() as c:
        _load_job(c, job_id)
        row = c.execute(schedules_t.insert().values(
            job_id=job_id, name=name.strip(), schedule_type=schedule_type, frequency=frequency,
            interval_seconds=interval_seconds, cron_expression=cron_expression,
            next_run_at=next_run_at, active=True, created_by_user_id=actor_user_id)
            .returning(*schedules_t.c)).mappings().one()
        record_event(c, entity_type="schedule", entity_id=dict(row)["id"],
                     event_type="schedule_created", actor_user_id=actor_user_id)
        return dict(row)


def set_schedule_active(principal, schedule_id: int, active: bool, *, actor_user_id=None) -> dict:
    with engine.begin() as c:
        if c.scalar(select(schedules_t.c.id).where(schedules_t.c.id == schedule_id)) is None:
            raise AutomationNotFound(str(schedule_id))
        row = c.execute(schedules_t.update().where(schedules_t.c.id == schedule_id)
                        .values(active=bool(active), updated_at=now())
                        .returning(*schedules_t.c)).mappings().one()
        return dict(row)


# --- runs --------------------------------------------------------------------

def _run_visible(principal, row: dict) -> bool:
    if principal.can("record.read_all"):
        return True
    if row.get("person_id") and record_in_scope(principal, "person", row["person_id"]):
        return True
    if row.get("household_id") and record_in_scope(principal, "household", row["household_id"]):
        return True
    return not (row.get("person_id") or row.get("household_id"))


def _runs_scope_clause(principal, c):
    if principal.can("record.read_all"):
        return None
    conds = [and_(runs_t.c.person_id.is_(None), runs_t.c.household_id.is_(None))]
    ids = accessible_person_ids(c, principal)
    if ids:
        conds.append(runs_t.c.person_id.in_(tuple(ids)))
        hh = set(c.scalars(select(people.c.household_id).where(
            people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))
        if hh:
            conds.append(runs_t.c.household_id.in_(tuple(hh)))
    return or_(*conds)


def list_runs(principal, *, job_id=None, status=None, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = _runs_scope_clause(principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if job_id is not None:
            conds.append(runs_t.c.job_id == job_id)
        if status:
            conds.append(runs_t.c.status == status)
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(runs_t)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(runs_t)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(runs_t.c.id.desc()).limit(page_size)
            .offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def get_run(principal, run_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(runs_t).where(runs_t.c.id == run_id)).mappings().first()
    if row is None or not _run_visible(principal, dict(row)):
        return None
    return dict(row)


def _retry_policy_for(c, job: dict) -> dict:
    if job.get("retry_policy_id"):
        row = c.execute(select(retry_t).where(retry_t.c.id == job["retry_policy_id"])).mappings().first()
        if row is not None:
            return dict(row)
    return {"max_attempts": 1, "retry_delays": [], "backoff_base_seconds": 30}


def enqueue_run(principal, job_id: int, *, trigger_source="manual", schedule_id=None,
                person_id=None, household_id=None, idempotency_key=None, worker_id=None,
                actor_user_id=None) -> dict:
    if person_id is not None and not record_in_scope(principal, "person", person_id, write=True):
        raise AutomationError("person not in write scope")
    if household_id is not None and not record_in_scope(principal, "household", household_id, write=True):
        raise AutomationError("household not in write scope")
    with engine.begin() as c:
        job = _load_job(c, job_id)
        if idempotency_key:
            existing = c.execute(select(runs_t).where(
                runs_t.c.idempotency_key == idempotency_key)).mappings().first()
            if existing is not None:
                return dict(existing)
        policy = _retry_policy_for(c, job)
        run = c.execute(runs_t.insert().values(
            job_id=job_id, schedule_id=schedule_id, queue_id=job.get("queue_id"), worker_id=worker_id,
            job_type=job["job_type"], status="pending", attempts=0,
            max_attempts=int(policy["max_attempts"]), available_at=now(),
            trigger_source=trigger_source, triggered_by_user_id=actor_user_id,
            idempotency_key=idempotency_key, person_id=person_id, household_id=household_id)
            .returning(*runs_t.c)).mappings().one()
        run = dict(run)
        record_event(c, entity_type="run", entity_id=run["id"], event_type="run_enqueued",
                     to_status="pending", actor_user_id=actor_user_id, payload={"trigger": trigger_source})
        return run


def _retry_delay_seconds(policy: dict, attempt: int) -> int:
    delays = policy.get("retry_delays") or []
    if attempt <= len(delays):
        return int(delays[attempt - 1])
    base = int(policy.get("backoff_base_seconds") or 30)
    return base * (2 ** (attempt - 1))


def execute_run(run_id: int, *, worker_id=None, worker_code="manual") -> dict:
    """Execute one run: acquire the single-flight lock, dispatch to the job_type handler with a
    system principal, and record success/retry/dead-letter per the retry/failure policy. Idempotent
    on terminal runs; deterministic; single-instance (no distributed lock)."""
    from datetime import timedelta
    with engine.begin() as c:
        run = c.execute(select(runs_t).where(runs_t.c.id == run_id)).mappings().first()
        if run is None:
            raise AutomationNotFound(str(run_id))
        run = dict(run)
        if run["status"] not in _RUNNABLE:
            return run                          # already terminal / running
        if run["available_at"] and run["available_at"] > now():
            return run                          # backoff not elapsed
        job = c.execute(select(jobs_t).where(jobs_t.c.id == run["job_id"])).mappings().first()
        if job is None or dict(job)["status"] != "enabled":
            c.execute(runs_t.update().where(runs_t.c.id == run_id)
                      .values(status="cancelled", updated_at=now()))
            record_event(c, entity_type="run", entity_id=run_id, event_type="run_cancelled",
                         to_status="cancelled", payload={"reason": "job not enabled"})
            return {**run, "status": "cancelled"}
        job = dict(job)
        lock_key = f"job:{job['id']}"
        if not acquire_lock(c, lock_key, owner=worker_code, run_id=run_id):
            return run                          # another run holds the lock
        started = now()
        c.execute(runs_t.update().where(runs_t.c.id == run_id).values(
            status="running", attempts=run["attempts"] + 1, started_at=started, worker_id=worker_id,
            updated_at=started))
        record_event(c, entity_type="run", entity_id=run_id, event_type="run_started",
                     from_status=run["status"], to_status="running")
        policy = _retry_policy_for(c, job)
    publish_timeline({**run, "job_type": job["job_type"]}, "run_started")

    principal = system_principal(job.get("created_by_user_id") or run.get("triggered_by_user_id"))
    attempt = run["attempts"] + 1
    try:
        result = dispatch.execute_dispatch(job["job_type"], config=job.get("config"),
                                           principal=principal, actor_user_id=run.get("triggered_by_user_id"))
        outcome, err = "succeeded", None
    except Exception as exc:            # noqa: BLE001 — failure isolation is the point
        result, err, outcome = None, str(exc)[:2000], "failed"

    finished = now()
    duration = int((finished - started).total_seconds() * 1000)
    with engine.begin() as c:
        if outcome == "succeeded":
            c.execute(runs_t.update().where(runs_t.c.id == run_id).values(
                status="succeeded", finished_at=finished, duration_ms=duration, result=result,
                last_error=None, updated_at=finished))
            record_event(c, entity_type="run", entity_id=run_id, event_type="run_succeeded",
                         from_status="running", to_status="succeeded", payload=result)
            final_status = "succeeded"
        elif attempt < run["max_attempts"]:
            delay = _retry_delay_seconds(policy, attempt)
            c.execute(runs_t.update().where(runs_t.c.id == run_id).values(
                status="pending", available_at=finished + timedelta(seconds=delay),
                last_error=err, updated_at=finished))
            record_event(c, entity_type="run", entity_id=run_id, event_type="run_retry_scheduled",
                         from_status="running", to_status="pending",
                         payload={"attempt": attempt, "retry_in_seconds": delay})
            final_status = "pending"
        else:
            # retry budget exhausted -> apply failure policy (default: dead-letter)
            c.execute(runs_t.update().where(runs_t.c.id == run_id).values(
                status="dead", finished_at=finished, duration_ms=duration, last_error=err,
                updated_at=finished))
            record_event(c, entity_type="run", entity_id=run_id, event_type="run_failed",
                         from_status="running", to_status="dead",
                         payload={"attempts": attempt, "error": err})
            final_status = "dead"
        release_lock(c, lock_key)
        updated = dict(c.execute(select(runs_t).where(runs_t.c.id == run_id)).mappings().one())

    if final_status == "succeeded":
        publish_timeline({**updated, "job_type": job["job_type"]}, "run_succeeded")
    elif final_status == "dead":
        publish_timeline({**updated, "job_type": job["job_type"]}, "run_failed")
    return updated


def run_job(principal, job_id: int, *, trigger_source="manual", person_id=None, household_id=None,
            idempotency_key=None, actor_user_id=None) -> dict:
    """Enqueue then immediately execute a job (a 'run now'). Authorization to trigger is the human
    principal's (checked in-route via ``automation.execute``); the dispatch runs with system
    authority."""
    run = enqueue_run(principal, job_id, trigger_source=trigger_source, person_id=person_id,
                      household_id=household_id, idempotency_key=idempotency_key,
                      actor_user_id=actor_user_id)
    return execute_run(run["id"], worker_code=f"manual:{actor_user_id}")


def run_audit(principal, run_id: int) -> list[dict]:
    if get_run(principal, run_id) is None:
        raise AutomationNotFound(str(run_id))
    return audit_history(principal, entity_type="run", entity_id=run_id)


def metrics(principal) -> dict:
    with engine.connect() as c:
        def _jobs(*extra):
            stmt = select(func.count()).select_from(jobs_t)
            return c.scalar(stmt.where(and_(*extra)) if extra else stmt) or 0
        def _runs(*extra):
            stmt = select(func.count()).select_from(runs_t)
            return c.scalar(stmt.where(and_(*extra)) if extra else stmt) or 0
        return {"jobs": _jobs(), "enabled_jobs": _jobs(jobs_t.c.status == "enabled"),
                "runs": _runs(), "running": _runs(runs_t.c.status == "running"),
                "failed": _runs(runs_t.c.status.in_(("failed", "dead"))),
                "succeeded": _runs(runs_t.c.status == "succeeded")}
