"""Distributed runtime cluster facade (Phase D.29) — coordinated refresh, sweep, diagnostics.

Orchestrates cluster-safe operations over the D.28 runtime engine using the transactional outbox as
the sole coordination bus. A coordinated refresh publishes ``runtime.refresh.requested``, performs the
engine refresh (which rebuilds the snapshot, publishes ``runtime.snapshot.activated``, and activates a
generation), then publishes ``runtime.refresh.completed`` — so every worker converges on the same
persisted runtime generation. The runtime engine remains the sole evaluator; this layer coordinates
*which version* each worker runs and never edits configuration metadata or evaluates configuration.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.config import runtime_worker_id
from app.db import engine, runtime_coordination_events, runtime_generations, runtime_workers

from . import coordination, generations
from .coordination_common import now, record_coordination_event, write_audit
from .events import publish_runtime_event


def initialize_cluster(*, actor_user_id=None) -> dict:
    """Register this worker and record a one-time cluster-initialized event. Guarded — never raises
    into startup."""
    try:
        first = False
        with engine.connect() as c:
            first = (c.scalar(select(func.count()).select_from(runtime_workers)) or 0) == 0
        worker = coordination.register_worker(actor_user_id=actor_user_id)
        if first:
            with engine.begin() as c:
                record_coordination_event(c, entity_type="cluster", entity_id=0,
                                          event_type="cluster_initialized", worker_uid=runtime_worker_id(),
                                          actor_user_id=actor_user_id)
            write_audit("runtime.cluster_initialized", entity_type="cluster", entity_id=0,
                        actor_user_id=actor_user_id)
        return {"initialized": True, "worker_uid": worker["worker_uid"] if worker else None}
    except Exception:
        return {"initialized": False}


def coordinated_refresh(principal=None, *, trigger="manual", actor_user_id=None) -> dict:
    """Perform a cluster-coordinated refresh: request → refresh (engine, publishes activation) →
    completed. Deduplicated by config_hash at the generation layer (one refresh per runtime version).
    Never raises."""
    from . import engine as runtime_engine
    with engine.begin() as c:
        record_coordination_event(c, entity_type="refresh", entity_id=0, event_type="refresh_requested",
                                  worker_uid=runtime_worker_id(), actor_user_id=actor_user_id,
                                  payload={"trigger": trigger})
        publish_runtime_event(c, "runtime.refresh.requested", {"trigger": trigger})
    result = runtime_engine.refresh(principal, actor_user_id=actor_user_id, trigger=trigger)
    gen = generations.current_generation()
    with engine.begin() as c:
        record_coordination_event(c, entity_type="refresh", entity_id=(gen["id"] if gen else 0),
                                  event_type="refresh_completed", to_status="completed",
                                  worker_uid=runtime_worker_id(), actor_user_id=actor_user_id,
                                  payload={"version": (gen["version"] if gen else None)})
        publish_runtime_event(c, "runtime.refresh.completed",
                             {"version": (gen["version"] if gen else None)})
    write_audit("runtime.coordinated_refresh", entity_type="generation",
                entity_id=(gen["id"] if gen else 0), actor_user_id=actor_user_id)
    return {**result, "trigger": trigger, "generation_version": (gen["version"] if gen else None),
            "cluster": coordination.cluster_state()}


def emergency_synchronization(principal=None, *, actor_user_id=None) -> dict:
    """Force an emergency cluster-wide synchronization (a coordinated refresh + convergence recompute)
    and record it. Requires runtime.admin in-route."""
    result = coordinated_refresh(principal, trigger="emergency", actor_user_id=actor_user_id)
    with engine.begin() as c:
        record_coordination_event(c, entity_type="cluster", entity_id=0,
                                  event_type="emergency_synchronization", worker_uid=runtime_worker_id(),
                                  actor_user_id=actor_user_id)
    write_audit("runtime.emergency_synchronization", entity_type="cluster", entity_id=0,
                actor_user_id=actor_user_id)
    return {"emergency": True, **result}


def coordination_sweep(principal=None, *, actor_user_id=None) -> dict:
    """A coordination sweep (scheduler / Automation entry): expire stale workers, converge the local
    worker, and recompute convergence. Records metadata only; never edits configuration metadata."""
    expired = coordination.expire_stale_workers(actor_user_id=actor_user_id)
    local = coordination.converge_worker()
    conv = generations.recompute_convergence(actor_user_id=actor_user_id)
    return {"expired": expired["expired"], "local": local.get("action"),
            "convergence": conv}


# --- reads -------------------------------------------------------------------

def overview_metrics(principal=None) -> dict:
    state = coordination.cluster_state()
    gen = generations.current_generation()
    with engine.connect() as c:
        generation_count = c.scalar(select(func.count()).select_from(runtime_generations)) or 0
        event_count = c.scalar(select(func.count()).select_from(runtime_coordination_events)) or 0
    return {**state, "current_generation": (gen["version"] if gen else None),
            "propagation_status": (gen["propagation_status"] if gen else None),
            "generation_count": generation_count, "coordination_events": event_count}


def diagnostics(principal=None) -> dict:
    """Cluster diagnostics: convergence, stale workers, cache drift, propagation latency, failures."""
    state = coordination.cluster_state()
    conv = coordination.convergence()
    gen = generations.current_generation()
    propagation_latency = None
    if gen and gen.get("converged_at") and gen.get("activated_at"):
        propagation_latency = (gen["converged_at"] - gen["activated_at"]).total_seconds()
    # cache drift: active workers whose cache is behind the current generation version
    drift = [w["worker_uid"] for w in coordination.list_workers(status="active")
             if gen and int(w["runtime_version"]) < int(gen["version"])]
    return {"cluster": state, "convergence": conv,
            "current_generation": (gen["version"] if gen else None),
            "propagation_status": (gen["propagation_status"] if gen else None),
            "propagation_latency_seconds": propagation_latency, "cache_drift_workers": drift,
            "stale_worker_count": state["stale_workers"], "checked_at": now().isoformat()}


def event_history(principal=None, *, entity_type=None, event_type=None, limit=100):
    from .coordination_common import coordination_history
    return coordination_history(principal, entity_type=entity_type, event_type=event_type, limit=limit)
