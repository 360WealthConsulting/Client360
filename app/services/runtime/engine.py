"""The Runtime Configuration Engine facade (Phase D.28).

The single entry point for runtime evaluation. It hydrates once at startup (guarded — a config
failure never blocks boot), serves the cached effective configuration + active features + edition
from an immutable snapshot, refreshes safely on demand / on a schedule (invalidate → rebuild →
fall back to last-known on failure), and builds an immutable per-request context. It reads the D.27
metadata (through the metadata reader) and **never writes it**; it writes only its own runtime
snapshots / ledger.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import func, select

from app.db import engine, runtime_config_snapshots

from . import editions as edition_eval
from . import features as feature_eval
from . import resolution, safety, snapshots
from .cache import RUNTIME_CACHE
from .common import record_event, write_audit
from .context import EMPTY_CONTEXT, RuntimeContext

logger = logging.getLogger("client360.runtime")

_CURRENT_SNAPSHOT_KEY = "current_snapshot"
_EDITION_KEY = "tenant_edition"

_lock = threading.RLock()
_emergency_overrides: dict = {}
_hydrated = {"value": False}


# --- lifecycle ---------------------------------------------------------------

def hydrate(*, actor_user_id=None) -> dict:
    """Startup hydration: warm the cache and build a startup snapshot. Self-guarded — any failure is
    logged and swallowed so it can NEVER prevent safe application startup."""
    try:
        RUNTIME_CACHE.invalidate()
        snap = snapshots.build_snapshot(scope="startup", source="startup-hydration",
                                        actor_user_id=actor_user_id)
        _cache_snapshot(snap)
        RUNTIME_CACHE.mark_warmed()
        _hydrated["value"] = True
        event_id = None
        with engine.begin() as c:
            record_event(c, entity_type="engine", entity_id=0, event_type="runtime_initialized",
                         actor_user_id=actor_user_id, payload={"snapshot_version": snap["version"]})
            event_id = _publish_coordination(c, "runtime.snapshot.created", snap)
        write_audit("runtime.initialized", entity_type="engine", entity_id=0, actor_user_id=actor_user_id)
        _activate_generation(snap, trigger="startup", event_id=event_id, actor_user_id=actor_user_id)
        return {"hydrated": True, "snapshot_version": snap["version"]}
    except Exception:
        logger.exception("runtime config hydration failed; continuing with defaults / last-known")
        return {"hydrated": False, "error": "hydration_failed"}


def refresh(principal=None, *, actor_user_id=None, trigger="manual") -> dict:
    """Safe refresh: invalidate the cache, rebuild the snapshot, publish the coordination events
    through the transactional outbox (cross-process invalidation), and activate a runtime generation
    the cluster converges on. On failure, keep serving the last-known snapshot (never raises)."""
    try:
        RUNTIME_CACHE.invalidate()
        snap = snapshots.build_snapshot(scope="refresh", source="safe-refresh", actor_user_id=actor_user_id)
        _cache_snapshot(snap)
        RUNTIME_CACHE.mark_warmed()
        event_id = None
        with engine.begin() as c:
            record_event(c, entity_type="cache", entity_id=snap["id"], event_type="cache_rebuilt",
                         actor_user_id=actor_user_id, payload={"version": RUNTIME_CACHE.version})
            # D.29: publish coordination events on the EXISTING outbox, atomic with the ledger write.
            _publish_coordination(c, "runtime.cache.rebuilt", snap)
            event_id = _publish_coordination(c, "runtime.snapshot.activated", snap)
        write_audit("runtime.refreshed", entity_type="snapshot", entity_id=snap["id"],
                    actor_user_id=actor_user_id)
        # D.29: activate a runtime generation (deduped by config_hash) + converge the local worker.
        gen = _activate_generation(snap, trigger=trigger, event_id=event_id, actor_user_id=actor_user_id)
        return {"refreshed": True, "snapshot_version": snap["version"],
                "cache_version": RUNTIME_CACHE.version,
                "generation_version": (gen["version"] if gen else None)}
    except Exception:
        logger.exception("runtime refresh failed; serving last-known snapshot")
        return {"refreshed": False, "error": "refresh_failed"}


def _publish_coordination(c, event_type, snap):
    """Publish a runtime coordination event on the transactional outbox (guarded, in-txn)."""
    try:
        from .events import publish_runtime_event
        return publish_runtime_event(c, event_type, {
            "snapshot_uid": snap.get("snapshot_uid"), "version": snap.get("version"),
            "config_hash": snap.get("config_hash")})
    except Exception:
        return None


def _activate_generation(snap, *, trigger, event_id=None, actor_user_id=None):
    """Activate a runtime generation for the snapshot and converge the local worker (guarded)."""
    try:
        from . import coordination, generations
        gen = generations.activate_generation(snap, trigger=trigger, event_id=event_id,
                                               actor_user_id=actor_user_id)
        coordination.converge_worker(gen=gen)
        return gen
    except Exception:
        logger.exception("runtime generation activation failed")
        return None


def warm_up(*, actor_user_id=None) -> dict:
    """Warm the cache from the current persisted snapshot (or build one if none exists)."""
    snap = snapshots.current_snapshot()
    if snap is None:
        return hydrate(actor_user_id=actor_user_id)
    _cache_snapshot(snap)
    RUNTIME_CACHE.mark_warmed()
    return {"warmed": True, "snapshot_version": snap["version"]}


def readiness() -> dict:
    snap = _current_cached_snapshot()
    report = safety.validate()
    return {"hydrated": _hydrated["value"], "snapshot_version": (snap["version"] if snap else None),
            "cache": RUNTIME_CACHE.stats(), "validation_ok": report["ok"],
            "issue_count": report["issue_count"]}


# --- evaluation --------------------------------------------------------------

def effective_config(principal=None, *, environment="production", organization_id=None, user_id=None) -> dict:
    """Resolve the effective configuration deterministically. Tenant-level results come from the cached
    snapshot; org/user-scoped results resolve on top (still deterministic, in-memory)."""
    RUNTIME_CACHE.note_evaluation()
    if organization_id is None and user_id is None and not _emergency_overrides:
        snap = _current_cached_snapshot()
        if snap is not None and environment == "production":
            return snap["effective_config"] or {}
    return resolution.resolve_effective_config(environment=environment, organization_id=organization_id,
                                               user_id=user_id, emergency=dict(_emergency_overrides))


def evaluate_features(principal=None, *, organization_id=None, user_id=None, principal_roles=None) -> dict:
    RUNTIME_CACHE.note_evaluation()
    edition = edition_eval.resolve_edition(organization_id=organization_id)
    caps = edition_eval.edition_capabilities(edition["id"]) if edition else set()
    return feature_eval.evaluate_all(
        organization_id=organization_id, rollout_key=(user_id or organization_id),
        edition_code=(edition["code"] if edition else None), edition_capabilities=caps,
        principal_roles=principal_roles)


def context_for(principal=None, *, environment="production", organization_id=None, user_id=None,
                principal_roles=None) -> RuntimeContext:
    """Build the immutable per-request runtime context. Never raises into the request path."""
    try:
        snap = _current_cached_snapshot()
        edition = edition_eval.resolve_edition(organization_id=organization_id)
        caps = edition_eval.edition_capabilities(edition["id"]) if edition else set()
        features = feature_eval.evaluate_all(
            organization_id=organization_id, rollout_key=(user_id or organization_id),
            edition_code=(edition["code"] if edition else None), edition_capabilities=caps,
            principal_roles=principal_roles)
        cfg = effective_config(principal, environment=environment, organization_id=organization_id,
                               user_id=user_id)
        return RuntimeContext(
            snapshot_id=(snap["id"] if snap else None), snapshot_uid=(snap["snapshot_uid"] if snap else None),
            snapshot_version=(snap["version"] if snap else None),
            edition_code=(edition["code"] if edition else None),
            license_code=(snap["license_code"] if snap else None),
            effective_config=cfg, active_features=features,
            edition_capabilities=frozenset(caps), resolved=True)
    except Exception:
        logger.exception("runtime context build failed; returning empty context")
        return EMPTY_CONTEXT


# --- emergency overrides (top-precedence break-glass; admin-only via routes) --

def set_emergency_override(key: str, value, *, actor_user_id=None) -> dict:
    with _lock:
        _emergency_overrides[key] = value
    RUNTIME_CACHE.invalidate()
    with engine.begin() as c:
        record_event(c, entity_type="engine", entity_id=0, event_type="emergency_override_set",
                     actor_user_id=actor_user_id, payload={"key": key})
    write_audit("runtime.emergency_override_set", entity_type="engine", entity_id=0,
                actor_user_id=actor_user_id, metadata={"key": key})
    return {"key": key, "active": True, "override_count": len(_emergency_overrides)}


def clear_emergency_override(key: str, *, actor_user_id=None) -> dict:
    with _lock:
        _emergency_overrides.pop(key, None)
    RUNTIME_CACHE.invalidate()
    with engine.begin() as c:
        record_event(c, entity_type="engine", entity_id=0, event_type="emergency_override_cleared",
                     actor_user_id=actor_user_id, payload={"key": key})
    return {"key": key, "active": False, "override_count": len(_emergency_overrides)}


def emergency_overrides() -> dict:
    with _lock:
        return dict(_emergency_overrides)


# --- internals ---------------------------------------------------------------

def _cache_snapshot(snap: dict):
    RUNTIME_CACHE.set(_CURRENT_SNAPSHOT_KEY, snap)


def _current_cached_snapshot() -> dict | None:
    snap = RUNTIME_CACHE.get(_CURRENT_SNAPSHOT_KEY)
    if snap is None:
        snap = snapshots.current_snapshot()
        if snap is not None:
            _cache_snapshot(snap)
    return snap


def metrics(principal=None) -> dict:
    with engine.connect() as c:
        snapshot_count = c.scalar(select(func.count()).select_from(runtime_config_snapshots)) or 0
        latest_version = c.scalar(select(func.max(runtime_config_snapshots.c.version))) or 0
    stats = RUNTIME_CACHE.stats()
    return {"snapshots": snapshot_count, "latest_version": latest_version,
            "cache_hit_ratio": stats["hit_ratio"], "cache_version": stats["version"],
            "evaluations": stats["evaluations"], "hydrated": _hydrated["value"]}
