"""Runtime generations (Phase D.29) — version history + convergence tracking.

A *generation* is an activated runtime version: a specific immutable snapshot (by ``config_hash``)
that the cluster should converge on. Generations are monotonic (``version`` ties to the snapshot
version) and deduplicated by ``config_hash`` — activating a snapshot whose config is identical to the
current generation is a no-op (reuse), which enforces **"only one refresh operation per runtime
version"**. Convergence is computed from the worker registry: a generation is *converged* once every
active worker's ``runtime_version`` has caught up to it. This layer reads the persisted
``runtime_config_snapshots`` (the single source of truth) — it performs no evaluation.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select, update

from app.db import engine, runtime_generations, runtime_workers

from .coordination_common import now, record_coordination_event

_GENERATION_NAMESPACE = uuid.UUID("6f3a1c2e-9b4d-4e7a-8c1f-2a3b4c5d6e29")


def current_generation() -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(runtime_generations).where(runtime_generations.c.status == "active")
                        .order_by(runtime_generations.c.version.desc()).limit(1)).mappings().first()
        return dict(row) if row else None


def get_generation(version: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(runtime_generations)
                        .where(runtime_generations.c.version == version)).mappings().first()
        return dict(row) if row else None


def list_generations(*, limit=50):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(runtime_generations).order_by(runtime_generations.c.version.desc())
            .limit(min(200, max(1, limit)))).mappings()]


def activate_generation(snapshot: dict, *, trigger="manual", event_id=None, actor_user_id=None) -> dict:
    """Activate a runtime generation for ``snapshot``. Deduplicated by ``config_hash``: if the current
    active generation already has this config_hash, it is returned unchanged (no new version — one
    refresh per runtime version). Otherwise a new generation supersedes the previous one."""
    if snapshot is None:
        raise ValueError("snapshot is required to activate a generation")
    config_hash = snapshot["config_hash"]
    with engine.begin() as c:
        cur = c.execute(select(runtime_generations).where(runtime_generations.c.status == "active")
                        .order_by(runtime_generations.c.version.desc()).limit(1)).mappings().first()
        if cur is not None and cur["config_hash"] == config_hash:
            return dict(cur)   # dedupe — identical effective config, reuse the current generation
        version = int(snapshot["version"])
        # never reuse a version number; if a generation row already exists for this version, bump.
        max_ver = c.scalar(select(func.max(runtime_generations.c.version))) or 0
        if version <= max_ver:
            version = max_ver + 1
        active_workers = c.scalar(select(func.count()).select_from(runtime_workers)
                                  .where(runtime_workers.c.status == "active")) or 0
        if cur is not None:
            c.execute(update(runtime_generations).where(runtime_generations.c.id == cur["id"])
                      .values(status="superseded", updated_at=now()))
        gen_uid = str(uuid.uuid5(_GENERATION_NAMESPACE, f"{version}:{config_hash}"))
        row = c.execute(runtime_generations.insert().values(
            generation_uid=gen_uid, version=version, snapshot_uid=snapshot.get("snapshot_uid"),
            config_hash=config_hash, trigger=trigger, status="active", propagation_status="pending",
            event_id=event_id, worker_count_at_activation=active_workers, converged_worker_count=0,
            activated_at=now(), created_by_user_id=actor_user_id).returning(*runtime_generations.c)).mappings().one()
        row = dict(row)
        record_coordination_event(c, entity_type="generation", entity_id=row["id"],
                                  event_type="snapshot_activated", to_status="active",
                                  actor_user_id=actor_user_id,
                                  payload={"version": version, "trigger": trigger, "config_hash": config_hash})
    return row


def recompute_convergence(*, actor_user_id=None) -> dict:
    """Recompute convergence for the current generation from the worker registry. Marks the
    generation ``converged`` (and records a ``convergence_achieved`` event once) when every active
    worker has caught up to its version."""
    gen = current_generation()
    if gen is None:
        return {"version": None, "converged": True, "active_workers": 0, "converged_workers": 0}
    with engine.begin() as c:
        active = c.scalar(select(func.count()).select_from(runtime_workers)
                          .where(runtime_workers.c.status == "active")) or 0
        converged = c.scalar(select(func.count()).select_from(runtime_workers).where(
            runtime_workers.c.status == "active",
            runtime_workers.c.runtime_version >= gen["version"])) or 0
        fully = active > 0 and converged >= active
        prop = "converged" if fully else ("converging" if converged > 0 else "pending")
        newly = fully and gen["propagation_status"] != "converged"
        c.execute(update(runtime_generations).where(runtime_generations.c.id == gen["id"]).values(
            converged_worker_count=converged, worker_count_at_activation=active,
            propagation_status=prop, converged_at=(now() if fully and gen["converged_at"] is None else gen["converged_at"]),
            updated_at=now()))
        if newly:
            record_coordination_event(c, entity_type="generation", entity_id=gen["id"],
                                      event_type="convergence_achieved", to_status="converged",
                                      actor_user_id=actor_user_id,
                                      payload={"version": gen["version"], "workers": active})
    return {"version": gen["version"], "converged": fully, "active_workers": active,
            "converged_workers": converged, "propagation_status": prop}
