"""Distributed runtime worker coordination (Phase D.29) — registry, heartbeat, convergence.

Each worker process registers in ``runtime_workers`` (keyed by a stable per-process ``worker_uid``),
heartbeats on a cadence, and **converges** onto the current runtime generation (the persisted source
of truth). Convergence is pull-based and idempotent: a worker whose ``runtime_version`` is behind the
current generation invalidates its in-process cache, warms from the current snapshot, and records its
new version. This is robust to any coordination-event delivery gap — a worker always converges via
the persisted generation. Stale workers (no heartbeat within the TTL) are expired automatically. This
layer never edits configuration metadata and never evaluates configuration.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, update

from app.config import runtime_worker_id, runtime_worker_ttl_seconds
from app.db import engine, runtime_worker_heartbeats, runtime_workers

from . import generations
from .cache import RUNTIME_CACHE
from .coordination_common import now, record_coordination_event, write_audit


def _worker_uid() -> str:
    return runtime_worker_id()


def _hostpid(worker_uid: str):
    host, _, pid = worker_uid.partition(":")
    try:
        return host, int(pid)
    except (ValueError, TypeError):
        return worker_uid, None


# --- registration / heartbeat ------------------------------------------------

def register_worker(*, worker_uid=None, actor_user_id=None) -> dict:
    """Register (or re-activate) this worker and converge it onto the current runtime generation.
    Idempotent — safe to call at every startup."""
    worker_uid = worker_uid or _worker_uid()
    host, pid = _hostpid(worker_uid)
    gen = generations.current_generation()
    ts = now()
    with engine.begin() as c:
        existing = c.execute(select(runtime_workers).where(
            runtime_workers.c.worker_uid == worker_uid)).mappings().first()
        cache_version = RUNTIME_CACHE.version
        values = dict(hostname=host, pid=pid, status="active", health_status="healthy",
                      cache_version=cache_version, last_heartbeat_at=ts, updated_at=ts)
        if existing is None:
            values.update(worker_uid=worker_uid, registered_at=ts, runtime_version=0)
            row = c.execute(runtime_workers.insert().values(**values)
                            .returning(*runtime_workers.c)).mappings().one()
        else:
            row = c.execute(runtime_workers.update().where(runtime_workers.c.id == existing["id"])
                            .values(**values).returning(*runtime_workers.c)).mappings().one()
        row = dict(row)
        record_coordination_event(c, entity_type="worker", entity_id=row["id"],
                                  event_type="worker_joined", to_status="active", worker_uid=worker_uid,
                                  actor_user_id=actor_user_id)
    write_audit("runtime.worker_joined", entity_type="worker", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"worker_uid": worker_uid})
    converge_worker(worker_uid=worker_uid, gen=gen)
    return get_worker(worker_uid)


def heartbeat(*, worker_uid=None, health_status="healthy", detail=None) -> dict:
    """Record a heartbeat for this worker and converge if behind. Heartbeats are NOT recorded as
    coordination events (only major lifecycle events are)."""
    worker_uid = worker_uid or _worker_uid()
    ts = now()
    with engine.begin() as c:
        w = c.execute(select(runtime_workers).where(
            runtime_workers.c.worker_uid == worker_uid)).mappings().first()
        if w is None:
            # a heartbeat for an unregistered worker registers it lazily (outside this txn)
            pass
        else:
            c.execute(runtime_workers.update().where(runtime_workers.c.id == w["id"]).values(
                last_heartbeat_at=ts, status="active", health_status=health_status,
                cache_version=RUNTIME_CACHE.version, updated_at=ts))
            c.execute(runtime_worker_heartbeats.insert().values(
                worker_id=w["id"], heartbeat_at=ts, runtime_version=w["runtime_version"],
                cache_version=RUNTIME_CACHE.version, snapshot_version=w["snapshot_version"],
                health_status=health_status, detail=detail))
    if w is None:
        return register_worker(worker_uid=worker_uid)
    converge_worker(worker_uid=worker_uid)
    return get_worker(worker_uid)


# --- convergence (pull-based, idempotent) ------------------------------------

def converge_worker(*, worker_uid=None, gen=None) -> dict:
    """Converge this worker onto the current runtime generation if it is behind. Idempotent — a
    worker already at the current version is a no-op (this is the replay-protection at the domain
    level: reprocessing a stale coordination event when already converged does nothing)."""
    worker_uid = worker_uid or _worker_uid()
    gen = gen if gen is not None else generations.current_generation()
    if gen is None:
        return {"worker_uid": worker_uid, "converged": True, "version": None, "action": "no_generation"}
    with engine.connect() as c:
        w = c.execute(select(runtime_workers).where(
            runtime_workers.c.worker_uid == worker_uid)).mappings().first()
    if w is None:
        return {"worker_uid": worker_uid, "converged": False, "action": "not_registered"}
    if int(w["runtime_version"]) >= int(gen["version"]):
        return {"worker_uid": worker_uid, "converged": True, "version": w["runtime_version"],
                "action": "already_converged"}

    # behind → invalidate the local cache, warm from the current snapshot, record the new version.
    action = "converged"
    try:
        from . import engine as runtime_engine
        RUNTIME_CACHE.invalidate()
        runtime_engine.warm_up(actor_user_id=None)
    except Exception:
        action = "converge_warmup_failed"
    snap_version = None
    try:
        from . import snapshots
        cur_snap = snapshots.current_snapshot()
        snap_version = cur_snap["version"] if cur_snap else None
    except Exception:
        pass
    with engine.begin() as c:
        c.execute(update(runtime_workers).where(runtime_workers.c.id == w["id"]).values(
            runtime_version=gen["version"], snapshot_version=snap_version,
            cache_version=RUNTIME_CACHE.version, updated_at=now()))
    generations.recompute_convergence()
    return {"worker_uid": worker_uid, "converged": True, "version": gen["version"], "action": action}


# --- stale-worker expiry -----------------------------------------------------

def expire_stale_workers(*, ttl_seconds=None, actor_user_id=None) -> dict:
    """Mark workers with no heartbeat within the TTL as ``stale``/``stopped`` and record a
    ``worker_removed`` event for each. Workers automatically expire when inactive."""
    ttl = ttl_seconds if ttl_seconds is not None else runtime_worker_ttl_seconds()
    cutoff = now() - timedelta(seconds=ttl)
    expired = []
    with engine.begin() as c:
        rows = c.execute(select(runtime_workers).where(
            runtime_workers.c.status == "active",
            runtime_workers.c.last_heartbeat_at.is_not(None),
            runtime_workers.c.last_heartbeat_at < cutoff)).mappings().all()
        for w in rows:
            c.execute(update(runtime_workers).where(runtime_workers.c.id == w["id"]).values(
                status="stale", health_status="unreachable", updated_at=now()))
            record_coordination_event(c, entity_type="worker", entity_id=w["id"],
                                      event_type="worker_removed", from_status="active", to_status="stale",
                                      worker_uid=w["worker_uid"], actor_user_id=actor_user_id)
            expired.append(w["worker_uid"])
    if expired:
        generations.recompute_convergence(actor_user_id=actor_user_id)
    return {"expired": len(expired), "worker_uids": expired}


# --- reads / cluster state ---------------------------------------------------

def get_worker(worker_uid: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(runtime_workers).where(
            runtime_workers.c.worker_uid == worker_uid)).mappings().first()
        return dict(row) if row else None


def list_workers(*, status=None):
    with engine.connect() as c:
        stmt = select(runtime_workers).order_by(runtime_workers.c.worker_uid)
        if status:
            stmt = stmt.where(runtime_workers.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def cluster_state() -> dict:
    gen = generations.current_generation()
    version = gen["version"] if gen else None
    with engine.connect() as c:
        total = c.scalar(select(func.count()).select_from(runtime_workers)) or 0
        active = c.scalar(select(func.count()).select_from(runtime_workers)
                          .where(runtime_workers.c.status == "active")) or 0
        stale = c.scalar(select(func.count()).select_from(runtime_workers)
                         .where(runtime_workers.c.status.in_(("stale", "stopped")))) or 0
        converged = 0
        if version is not None:
            converged = c.scalar(select(func.count()).select_from(runtime_workers).where(
                runtime_workers.c.status == "active",
                runtime_workers.c.runtime_version >= version)) or 0
    pct = round((converged / active) * 100, 1) if active else 100.0
    return {"current_version": version, "total_workers": total, "active_workers": active,
            "stale_workers": stale, "converged_workers": converged, "convergence_pct": pct,
            "converged": (active == 0 or converged >= active)}


def convergence() -> dict:
    gen = generations.current_generation()
    version = gen["version"] if gen else None
    workers = [{"worker_uid": w["worker_uid"], "status": w["status"],
                "runtime_version": w["runtime_version"],
                "behind": (version is not None and w["status"] == "active"
                           and int(w["runtime_version"]) < int(version))}
               for w in list_workers()]
    return {"current_version": version, "workers": workers,
            "stale_behind": [w["worker_uid"] for w in workers if w["behind"]]}
