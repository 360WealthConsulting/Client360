"""Distributed Runtime Coordination tests (Phase D.29).

Covers worker registration + heartbeat, runtime generation activation + config_hash dedupe (one
refresh per version), coordinated refresh + outbox event publication, cross-process cache
invalidation, pull-based convergence + replay-protection idempotency, stale-worker cleanup,
scheduler/Automation-dispatch wiring, Analytics consumption, the append-only coordination ledger, and
the separation invariants (the outbox is the sole coordination bus; the runtime engine remains the
sole evaluator; coordination never edits configuration metadata). The runtime engine, outbox, RBAC,
and D.5 golden are untouched.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text, update

from app.db import (
    configuration_items,
    configuration_sets,
    engine,
    runtime_coordination_events,
    runtime_workers,
    users,
)
from app.services.runtime import cluster, coordination, generations, snapshots
from app.services.runtime.cache import RUNTIME_CACHE

try:
    from app.platform.outbox import outbox_events
except Exception:
    outbox_events = None


def _tag():
    return uuid.uuid4().hex[:8]


def _uid():
    with engine.begin() as c:
        t = _tag()
        return c.execute(users.insert().values(
            email=f"rc-{t}@e.test", normalized_email=f"rc-{t}@e.test", display_name="U",
            status="active").returning(users.c.id)).scalar_one()


def _mk_config(uid):
    """Create one active config item so snapshots/generations are non-trivial. Returns the item id."""
    with engine.begin() as c:
        set_id = c.execute(configuration_sets.insert().values(
            code=f"set-{_tag()}", name="Set", status="active", created_by_user_id=uid)
            .returning(configuration_sets.c.id)).scalar_one()
        return c.execute(configuration_items.insert().values(
            set_id=set_id, code=f"item-{_tag()}", name="Item", value_type="string", value="V",
            status="active", version=1, created_by_user_id=uid)
            .returning(configuration_items.c.id)).scalar_one()


def _insert_worker(worker_uid, *, status="active", runtime_version=0, heartbeat_age_seconds=0):
    with engine.begin() as c:
        hb = datetime.now(UTC) - timedelta(seconds=heartbeat_age_seconds)
        return c.execute(runtime_workers.insert().values(
            worker_uid=worker_uid, status=status, health_status="healthy",
            runtime_version=runtime_version, cache_version=0, last_heartbeat_at=hb,
            registered_at=hb).returning(runtime_workers.c.id)).scalar_one()


def _cleanup(uid, worker_uids=()):
    RUNTIME_CACHE.invalidate()
    with engine.begin() as c:
        for wid in worker_uids:
            c.execute(delete(runtime_workers).where(runtime_workers.c.worker_uid == wid))
        c.execute(delete(configuration_items).where(configuration_items.c.created_by_user_id == uid))
        c.execute(delete(configuration_sets).where(configuration_sets.c.created_by_user_id == uid))
        # runtime_generations / runtime_config_snapshots / runtime_coordination_events accumulate
        # (append-only or version-monotonic) → left as leftovers.


# --- worker registration + heartbeat -----------------------------------------

def test_worker_registration_and_heartbeat():
    uid = _uid()
    wuid = f"worker-{_tag()}"
    try:
        _mk_config(uid)
        w = coordination.register_worker(worker_uid=wuid, actor_user_id=uid)
        assert w["worker_uid"] == wuid and w["status"] == "active"
        assert any(w2["worker_uid"] == wuid for w2 in coordination.list_workers())
        # a worker_joined coordination event was recorded
        with engine.connect() as c:
            n = c.scalar(select(func.count()).select_from(runtime_coordination_events).where(
                runtime_coordination_events.c.worker_uid == wuid,
                runtime_coordination_events.c.event_type == "worker_joined"))
        assert n >= 1
        # heartbeat updates last_heartbeat_at and logs a heartbeat row (not a coordination event)
        coordination.heartbeat(worker_uid=wuid)
        with engine.connect() as c:
            hb = c.scalar(text("SELECT count(*) FROM runtime_worker_heartbeats WHERE worker_id="
                               "(SELECT id FROM runtime_workers WHERE worker_uid=:w)"), {"w": wuid})
        assert hb >= 1 and coordination.get_worker(wuid)["last_heartbeat_at"] is not None
    finally:
        _cleanup(uid, [wuid])


# --- generation activation + dedupe ------------------------------------------

def test_generation_activation_and_config_hash_dedupe():
    uid = _uid()
    try:
        _mk_config(uid)
        snap = snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        gen1 = generations.activate_generation(snap, trigger="manual", actor_user_id=uid)
        assert gen1["status"] == "active" and gen1["config_hash"] == snap["config_hash"]
        # activating the SAME config_hash again is a no-op (one refresh per runtime version)
        snap_same = snapshots.build_snapshot(scope="manual", actor_user_id=uid)  # same metadata → same hash
        gen2 = generations.activate_generation(snap_same, trigger="manual", actor_user_id=uid)
        assert gen2["id"] == gen1["id"]
        assert snap_same["config_hash"] == snap["config_hash"]
    finally:
        _cleanup(uid)


def test_generation_supersede_on_config_change():
    uid = _uid()
    try:
        item_id = _mk_config(uid)
        snap_a = snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        gen_a = generations.activate_generation(snap_a, trigger="manual", actor_user_id=uid)
        with engine.begin() as c:
            c.execute(update(configuration_items).where(configuration_items.c.id == item_id).values(value="CHANGED"))
        snap_b = snapshots.build_snapshot(scope="refresh", actor_user_id=uid)
        gen_b = generations.activate_generation(snap_b, trigger="metadata_change", actor_user_id=uid)
        assert gen_b["version"] > gen_a["version"] and gen_b["status"] == "active"
        assert generations.get_generation(gen_a["version"])["status"] == "superseded"
    finally:
        _cleanup(uid)


# --- coordinated refresh + outbox publication --------------------------------

def test_coordinated_refresh_publishes_to_outbox_and_activates_generation():
    uid = _uid()
    try:
        _mk_config(uid)
        result = cluster.coordinated_refresh(None, trigger="manual", actor_user_id=uid)
        assert result["refreshed"] is True and result["generation_version"] is not None
        # coordination events recorded
        with engine.connect() as c:
            reqs = c.scalar(select(func.count()).select_from(runtime_coordination_events)
                            .where(runtime_coordination_events.c.event_type == "refresh_requested"))
            comps = c.scalar(select(func.count()).select_from(runtime_coordination_events)
                             .where(runtime_coordination_events.c.event_type == "refresh_completed"))
        assert reqs >= 1 and comps >= 1
        # the transactional outbox carries the runtime coordination events (sole bus)
        if outbox_events is not None:
            with engine.connect() as c:
                n = c.scalar(select(func.count()).select_from(outbox_events)
                             .where(outbox_events.c.name.like("runtime.%")))
            assert n >= 1
    finally:
        _cleanup(uid)


# --- convergence + version propagation + cache invalidation ------------------

def test_converge_worker_pulls_current_generation():
    uid = _uid()
    wuid = f"behind-{_tag()}"
    try:
        _mk_config(uid)
        snap = snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        gen = generations.activate_generation(snap, trigger="manual", actor_user_id=uid)
        _insert_worker(wuid, runtime_version=0)   # behind the current generation
        pre_cache_version = RUNTIME_CACHE.version
        res = coordination.converge_worker(worker_uid=wuid)
        assert res["converged"] is True and res["version"] == gen["version"]
        # the worker's runtime_version advanced to the generation, and the local cache was invalidated
        assert coordination.get_worker(wuid)["runtime_version"] == gen["version"]
        assert RUNTIME_CACHE.version > pre_cache_version
    finally:
        _cleanup(uid, [wuid])


def test_convergence_is_idempotent_replay_protection():
    uid = _uid()
    wuid = f"conv-{_tag()}"
    try:
        _mk_config(uid)
        snap = snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        gen = generations.activate_generation(snap, trigger="manual", actor_user_id=uid)
        _insert_worker(wuid, runtime_version=gen["version"])   # already converged
        res = coordination.converge_worker(worker_uid=wuid)
        assert res["action"] == "already_converged"
        # replaying the outbox consumer for an already-converged worker is a harmless no-op
        from app.services.runtime import events as runtime_events_mod
        runtime_events_mod.on_runtime_event({"name": "runtime.snapshot.activated", "payload": {}})
    finally:
        _cleanup(uid, [wuid])


def test_cluster_convergence_recompute():
    uid = _uid()
    a, b = f"a-{_tag()}", f"b-{_tag()}"
    try:
        _mk_config(uid)
        snap = snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        gen = generations.activate_generation(snap, trigger="manual", actor_user_id=uid)
        _insert_worker(a, runtime_version=gen["version"])   # converged
        _insert_worker(b, runtime_version=0)                # behind
        state = coordination.cluster_state()
        assert state["active_workers"] >= 2 and state["convergence_pct"] < 100.0
        # converge b, then the cluster is fully converged (for these two workers)
        coordination.converge_worker(worker_uid=b)
        conv = coordination.convergence()
        assert b not in conv["stale_behind"] and a not in conv["stale_behind"]
    finally:
        _cleanup(uid, [a, b])


# --- stale-worker cleanup ----------------------------------------------------

def test_stale_worker_cleanup():
    uid = _uid()
    wuid = f"stale-{_tag()}"
    try:
        _insert_worker(wuid, status="active", heartbeat_age_seconds=99999)
        res = coordination.expire_stale_workers(ttl_seconds=60, actor_user_id=uid)
        assert wuid in res["worker_uids"]
        assert coordination.get_worker(wuid)["status"] == "stale"
        with engine.connect() as c:
            n = c.scalar(select(func.count()).select_from(runtime_coordination_events).where(
                runtime_coordination_events.c.worker_uid == wuid,
                runtime_coordination_events.c.event_type == "worker_removed"))
        assert n >= 1
    finally:
        _cleanup(uid, [wuid])


# --- scheduler / automation / analytics --------------------------------------

def test_scheduler_and_automation_wiring():
    from app.jobs import scheduler
    from app.services.automation import dispatch
    assert "runtime_coordination" in dispatch.DISPATCH_REGISTRY
    assert hasattr(scheduler, "run_runtime_heartbeat")
    assert hasattr(scheduler, "run_runtime_stale_cleanup")
    from app.config import (
        runtime_coordination_enabled,
        runtime_heartbeat_interval_seconds,
        runtime_worker_id,
    )
    assert isinstance(runtime_coordination_enabled(), bool)
    assert runtime_heartbeat_interval_seconds() >= 10
    assert ":" in runtime_worker_id() or runtime_worker_id()


def test_outbox_is_the_sole_coordination_bus():
    # The runtime event bus publishes/subscribes only through app.platform.outbox — no second system.
    import pathlib

    from app.services.runtime import events as runtime_events_mod
    src = pathlib.Path(runtime_events_mod.__file__).read_text()
    assert "app.platform.outbox" in src and "app.platform.events" in src
    # register_runtime_consumers subscribes each runtime.* type
    assert len(runtime_events_mod.RUNTIME_EVENT_TYPES) == 8


def test_register_runtime_consumers_subscribes():
    from app.platform import outbox
    from app.services.runtime.events import RUNTIME_EVENT_TYPES, register_runtime_consumers
    outbox.clear_subscribers()
    register_runtime_consumers()
    for et in RUNTIME_EVENT_TYPES:
        assert et in outbox._subscribers and outbox._subscribers[et]
    outbox.clear_subscribers()


def test_analytics_consumes_cluster_metrics():
    uid = _uid()
    wuid = f"an-{_tag()}"
    try:
        from app.services.analytics import sources
        from app.services.analytics.metrics import METRICS
        before = sources.runtime_active_worker_count(None)
        _insert_worker(wuid, status="active")
        assert sources.runtime_active_worker_count(None) == before + 1
        for key in ("runtime_active_workers", "runtime_cluster_convergence", "runtime_stale_workers",
                    "runtime_generations"):
            assert key in METRICS
    finally:
        _cleanup(uid, [wuid])


def test_overview_and_diagnostics():
    m = cluster.overview_metrics(None)
    for k in ("current_version", "active_workers", "convergence_pct", "generation_count"):
        assert k in m
    d = cluster.diagnostics(None)
    for k in ("cluster", "convergence", "stale_worker_count"):
        assert k in d


# --- append-only ledger + separation invariants ------------------------------

def test_coordination_events_append_only():
    uid = _uid()
    wuid = f"ap-{_tag()}"
    try:
        _mk_config(uid)
        coordination.register_worker(worker_uid=wuid, actor_user_id=uid)
        with engine.connect() as c:
            eid = c.scalar(select(runtime_coordination_events.c.id).where(
                runtime_coordination_events.c.worker_uid == wuid).limit(1))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(runtime_coordination_events)
                          .where(runtime_coordination_events.c.id == eid).values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(runtime_coordination_events)
                          .where(runtime_coordination_events.c.id == eid))
    finally:
        _cleanup(uid, [wuid])


def test_coordination_never_edits_configuration_metadata():
    import pathlib

    from app.services.runtime import cluster as cluster_mod
    root = pathlib.Path(cluster_mod.__file__).parent
    for name in ("coordination.py", "generations.py", "events.py", "cluster.py", "coordination_common.py"):
        src = (root / name).read_text()
        for verb in (".insert()", ".update()", ".delete()"):
            for tbl in ("configuration_items", "configuration_feature_flags", "configuration_editions",
                        "configuration_preferences", "configuration_environment_overrides"):
                assert f"{tbl}{verb}" not in src, f"{name} writes {tbl}"


def test_coordination_does_not_import_composition_layers():
    import pathlib

    from app.services.runtime import cluster as cluster_mod
    root = pathlib.Path(cluster_mod.__file__).parent
    for name in ("coordination.py", "generations.py", "events.py", "cluster.py", "coordination_common.py"):
        src = (root / name).read_text()
        for layer in ("annual_review", "business_owner", "app.services.reporting"):
            assert f"import {layer}" not in src and f"{layer} import" not in src, f"{name}:{layer}"


def test_migration_widens_job_types_and_route_prefix():
    from app.database.automation_tables import JOB_TYPES
    assert "runtime_coordination" in JOB_TYPES
    from app.security.middleware import RULES
    assert not any(pattern.search("/runtime/cluster") for pattern, _cap in RULES)
    assert not any(pattern.search("/runtime/cluster/workers") for pattern, _cap in RULES)
