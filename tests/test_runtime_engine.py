"""Runtime Configuration Engine tests (Phase D.28).

Covers deterministic resolution precedence, feature/rollout/edition/capability evaluation, immutable
snapshot correctness + comparison + staleness, the in-process cache (versioning/invalidation/stats),
startup hydration, per-request immutable context, safety detectors, scheduler + Automation-dispatch
wiring, Analytics consumption, append-only runtime ledger, and the D.27↔D.28 separation invariants
(the engine evaluates but never edits configuration metadata). The env loaders, RBAC, startup
lifecycle, and D.5 golden are untouched.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, text, update

from app.db import (
    configuration_edition_assignments,
    configuration_edition_capabilities,
    configuration_editions,
    configuration_environment_overrides,
    configuration_feature_flags,
    configuration_feature_rollouts,
    configuration_items,
    configuration_preferences,
    configuration_sets,
    engine,
    runtime_config_snapshots,
    runtime_events,
    users,
)
from app.security.models import Principal
from app.services.runtime import (
    editions,
    features,
    resolution,
    safety,
    snapshots,
)
from app.services.runtime import (
    engine as runtime_engine,
)
from app.services.runtime import service as svc
from app.services.runtime.cache import RUNTIME_CACHE
from app.services.runtime.common import audit_history

CAPS = frozenset({"runtime.view", "runtime.manage", "runtime.execute", "runtime.audit",
                  "runtime.admin", "record.read_all"})


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _tag():
    return uuid.uuid4().hex[:8]


def _uid():
    with engine.begin() as c:
        t = _tag()
        return c.execute(users.insert().values(
            email=f"rt-{t}@e.test", normalized_email=f"rt-{t}@e.test", display_name="U",
            status="active").returning(users.c.id)).scalar_one()


def _mk_set(uid, tag):
    with engine.begin() as c:
        return c.execute(configuration_sets.insert().values(
            code=f"set-{tag}", name="Set", status="active", created_by_user_id=uid)
            .returning(configuration_sets.c.id)).scalar_one()


def _mk_item(uid, set_id, code, *, value=None, default=None, status="active", rt_ref=None):
    with engine.begin() as c:
        return c.execute(configuration_items.insert().values(
            set_id=set_id, code=code, name=code, value_type="string", value=value, default_value=default,
            status=status, version=1, runtime_setting_reference=rt_ref, created_by_user_id=uid)
            .returning(configuration_items.c.id)).scalar_one()


def _mk_flag(uid, code, *, status="active", enabled=True, rollout=100, target_orgs=None,
             target_roles=None, meta=None, starts=None, ends=None):
    with engine.begin() as c:
        return c.execute(configuration_feature_flags.insert().values(
            code=code, name=code, status=status, enabled=enabled, rollout_percentage=rollout,
            target_organizations=target_orgs, target_roles=target_roles, flag_metadata=meta,
            activation_starts_at=starts, activation_ends_at=ends, created_by_user_id=uid)
            .returning(configuration_feature_flags.c.id)).scalar_one()


def _mk_edition(uid, code, *, status="active", tier="enterprise"):
    with engine.begin() as c:
        return c.execute(configuration_editions.insert().values(
            code=code, name=code, tier=tier, status=status, created_by_user_id=uid)
            .returning(configuration_editions.c.id)).scalar_one()


def _cleanup(uid):
    RUNTIME_CACHE.invalidate()
    runtime_engine._emergency_overrides.clear()
    with engine.begin() as c:
        for t in (configuration_edition_assignments, configuration_edition_capabilities,
                  configuration_editions, configuration_feature_rollouts, configuration_feature_flags,
                  configuration_environment_overrides, configuration_preferences, configuration_items,
                  configuration_sets):
            c.execute(delete(t).where(t.c.created_by_user_id == uid))
        # runtime_config_snapshots / runtime_events are append-only (trigger-blocked) → left as leftovers.


# --- resolution precedence ---------------------------------------------------

def test_resolution_precedence_deterministic():
    uid = _uid()
    try:
        set_id = _mk_set(uid, _tag())
        code = f"item-{_tag()}"
        item_id = _mk_item(uid, set_id, code, value="ITEM", default="DEFAULT")
        item = {"id": item_id, "code": code, "value": "ITEM", "default_value": "DEFAULT"}

        # 7. default (no value)
        bare = {"id": item_id, "code": code, "value": None, "default_value": "DEFAULT"}
        assert resolution.resolve_item(bare) == ("DEFAULT", "default")
        # 6. item value
        assert resolution.resolve_item(item) == ("ITEM", "item")
        # 5. user preference
        prefs = {("user", None, 7, code): "USER"}
        assert resolution.resolve_item(item, user_id=7, preference_idx=prefs) == ("USER", "user")
        # 4. organization override
        prefs2 = {("organization", 3, None, code): "ORG", ("user", None, 7, code): "USER"}
        assert resolution.resolve_item(item, organization_id=3, user_id=7,
                                       preference_idx=prefs2) == ("ORG", "organization")
        # 3. tenant override
        prefs3 = {**prefs2, ("tenant", None, None, code): "TENANT"}
        assert resolution.resolve_item(item, organization_id=3, user_id=7,
                                       preference_idx=prefs3) == ("TENANT", "tenant")
        # 2. environment override
        ovr = {item_id: {"production": "ENV"}}
        assert resolution.resolve_item(item, environment="production", organization_id=3, user_id=7,
                                       preference_idx=prefs3, override_idx=ovr) == ("ENV", "environment")
        # 1. emergency override (top precedence)
        assert resolution.resolve_item(item, environment="production", organization_id=3, user_id=7,
                                       preference_idx=prefs3, override_idx=ovr,
                                       emergency={code: "EMERGENCY"}) == ("EMERGENCY", "emergency")
    finally:
        _cleanup(uid)


def test_resolve_effective_config_reads_metadata():
    uid = _uid()
    try:
        set_id = _mk_set(uid, _tag())
        code = f"eff-{_tag()}"
        _mk_item(uid, set_id, code, value="V")
        eff = resolution.resolve_effective_config()
        assert eff[code]["value"] == "V" and eff[code]["source"] == "item"
    finally:
        _cleanup(uid)


# --- feature / rollout / edition / capability evaluation ---------------------

def test_feature_evaluation_lifecycle_and_window():
    now = datetime.now(UTC)
    base = {"id": 1, "code": "f", "status": "active", "enabled": True, "rollout_percentage": 100}
    assert features.evaluate_flag(base)["enabled"] is True
    assert features.evaluate_flag({**base, "status": "draft"})["reason"] == "not_active"
    assert features.evaluate_flag({**base, "status": "deprecated"})["reason"] == "deprecated"
    assert features.evaluate_flag({**base, "activation_starts_at": now + timedelta(hours=1)})[
        "reason"] == "before_activation_window"
    assert features.evaluate_flag({**base, "activation_ends_at": now - timedelta(hours=1)})[
        "reason"] == "after_activation_window"


def test_rollout_evaluation_deterministic():
    base = {"id": 1, "code": "roll", "status": "active", "enabled": True}
    assert features.evaluate_flag({**base, "rollout_percentage": 0})["enabled"] is False
    assert features.evaluate_flag({**base, "rollout_percentage": 100})["enabled"] is True
    # deterministic: same key → same verdict
    v1 = features.evaluate_flag({**base, "rollout_percentage": 50}, rollout_key="user-42")
    v2 = features.evaluate_flag({**base, "rollout_percentage": 50}, rollout_key="user-42")
    assert v1 == v2
    # staged rollout overrides the flag percentage
    staged = features.evaluate_flag({**base, "rollout_percentage": 0}, rollout_key="k",
                                    active_rollouts=[{"feature_flag_id": 1, "percentage": 100}])
    assert staged["enabled"] is True


def test_feature_targeting_and_edition_gate():
    base = {"id": 1, "code": "t", "status": "active", "enabled": True, "rollout_percentage": 100}
    # org targeting
    assert features.evaluate_flag({**base, "target_organizations": [5]}, organization_id=9)[
        "reason"] == "org_not_targeted"
    assert features.evaluate_flag({**base, "target_organizations": [5]}, organization_id=5)["enabled"] is True
    # role targeting
    assert features.evaluate_flag({**base, "target_roles": ["administrator"]}, principal_roles={"advisor"})[
        "reason"] == "role_not_targeted"
    assert features.evaluate_flag({**base, "target_roles": ["administrator"]},
                                  principal_roles={"administrator"})["enabled"] is True
    # edition capability gate
    gated = {**base, "flag_metadata": {"required_capability": "runtime.view"}}
    assert features.evaluate_flag(gated, edition_capabilities=set())["reason"] == "edition_capability_missing"
    assert features.evaluate_flag(gated, edition_capabilities={"runtime.view"})["enabled"] is True


def test_edition_and_capability_evaluation():
    uid = _uid()
    try:
        ed_id = _mk_edition(uid, f"ent-{_tag()}")
        with engine.begin() as c:
            c.execute(configuration_edition_capabilities.insert().values(
                edition_id=ed_id, capability_code="runtime.view", included=True, created_by_user_id=uid))
            c.execute(configuration_edition_assignments.insert().values(
                edition_id=ed_id, scope="tenant", status="active", created_by_user_id=uid))
        ed = editions.resolve_edition()
        assert ed is not None and ed["id"] == ed_id
        assert "runtime.view" in editions.edition_capabilities(ed_id)
        # org-scoped assignment wins over tenant (real organization_profiles row for the FK)
        from app.db import organization_profiles, relationship_entities
        with engine.begin() as c:
            reid = c.execute(relationship_entities.insert().values(
                entity_type="organization", name=f"Org {_tag()}", details={}, active=True)
                .returning(relationship_entities.c.id)).scalar_one()
            org_id = c.execute(organization_profiles.insert().values(
                relationship_entity_id=reid, status="active", address_json={})
                .returning(organization_profiles.c.id)).scalar_one()
            ed2 = c.execute(configuration_editions.insert().values(
                code=f"pro-{_tag()}", name="Pro", tier="professional", status="active",
                created_by_user_id=uid).returning(configuration_editions.c.id)).scalar_one()
            c.execute(configuration_edition_assignments.insert().values(
                edition_id=ed2, scope="organization", organization_id=org_id, status="active",
                created_by_user_id=uid))
        assert editions.resolve_edition(organization_id=org_id)["id"] == ed2
        with engine.begin() as c:
            c.execute(delete(configuration_edition_assignments).where(
                configuration_edition_assignments.c.organization_id == org_id))
            c.execute(delete(organization_profiles).where(organization_profiles.c.id == org_id))
            c.execute(delete(relationship_entities).where(relationship_entities.c.id == reid))
    finally:
        _cleanup(uid)


# --- snapshots ---------------------------------------------------------------

def test_snapshot_correctness_and_immutability():
    uid = _uid()
    try:
        set_id = _mk_set(uid, _tag())
        _mk_item(uid, set_id, f"s-{_tag()}", value="V")
        snap = snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        assert snap["config_hash"] and snap["version"] >= 1 and snap["item_count"] >= 1
        got = snapshots.get_snapshot(snap["snapshot_uid"])
        assert got["config_hash"] == snap["config_hash"]
        # immutable: update and delete are trigger-blocked
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(runtime_config_snapshots)
                          .where(runtime_config_snapshots.c.id == snap["id"]).values(scope="manual"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(runtime_config_snapshots)
                          .where(runtime_config_snapshots.c.id == snap["id"]))
    finally:
        _cleanup(uid)


def test_snapshot_comparison_and_staleness():
    uid = _uid()
    try:
        set_id = _mk_set(uid, _tag())
        code = f"cmp-{_tag()}"
        item_id = _mk_item(uid, set_id, code, value="A")
        a = snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        # change the metadata → snapshot a becomes stale, a new snapshot differs
        with engine.begin() as c:
            c.execute(update(configuration_items).where(configuration_items.c.id == item_id).values(value="B"))
        assert snapshots.is_stale(a) is True
        b = snapshots.build_snapshot(scope="refresh", actor_user_id=uid)
        diff = snapshots.compare_snapshots(a["snapshot_uid"], b["snapshot_uid"])
        assert diff["identical"] is False and code in diff["changed_config"]
    finally:
        _cleanup(uid)


# --- cache -------------------------------------------------------------------

def test_cache_versioning_and_invalidation():
    RUNTIME_CACHE.invalidate()
    v0 = RUNTIME_CACHE.version
    RUNTIME_CACHE.set("k", {"x": 1})
    assert RUNTIME_CACHE.get("k") == {"x": 1}          # hit
    assert RUNTIME_CACHE.get("missing") is None         # miss
    stats = RUNTIME_CACHE.stats()
    assert stats["hits"] >= 1 and stats["misses"] >= 1 and stats["hit_ratio"] is not None
    RUNTIME_CACHE.invalidate()
    assert RUNTIME_CACHE.version == v0 + 1 and RUNTIME_CACHE.get("k") is None


# --- startup hydration + request context -------------------------------------

def test_hydrate_builds_snapshot_and_never_raises():
    uid = _uid()
    try:
        set_id = _mk_set(uid, _tag())
        _mk_item(uid, set_id, f"h-{_tag()}", value="V")
        result = runtime_engine.hydrate(actor_user_id=uid)
        assert result["hydrated"] is True
        assert runtime_engine.readiness()["hydrated"] is True
        assert snapshots.current_snapshot() is not None
    finally:
        _cleanup(uid)


def test_request_context_is_immutable_and_resolved():
    uid = _uid()
    try:
        set_id = _mk_set(uid, _tag())
        code = f"ctx-{_tag()}"
        _mk_item(uid, set_id, code, value="CTXVAL")
        _mk_flag(uid, f"ctxflag-{_tag()}", rollout=100)
        snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        ctx = runtime_engine.context_for(_principal(uid))
        assert ctx.resolved is True and ctx.snapshot_version is not None
        assert ctx.config(code) == "CTXVAL"
        # frozen dataclass — cannot mutate
        with pytest.raises(Exception):
            ctx.edition_code = "x"
    finally:
        _cleanup(uid)


def test_emergency_override_top_precedence_and_audit():
    uid = _uid()
    try:
        set_id = _mk_set(uid, _tag())
        code = f"em-{_tag()}"
        _mk_item(uid, set_id, code, value="NORMAL")
        # snapshot the base config so the post-clear effective config (served from the snapshot) has it
        snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        runtime_engine.set_emergency_override(code, "BREAKGLASS", actor_user_id=uid)
        eff = runtime_engine.effective_config(_principal(uid))
        assert eff[code]["value"] == "BREAKGLASS" and eff[code]["source"] == "emergency"
        runtime_engine.clear_emergency_override(code, actor_user_id=uid)
        assert runtime_engine.effective_config(_principal(uid))[code]["value"] == "NORMAL"
    finally:
        _cleanup(uid)


# --- safety ------------------------------------------------------------------

def test_safety_detects_issues():
    uid = _uid()
    try:
        set_id = _mk_set(uid, _tag())
        # invalid configuration: active item with a runtime ref but no value/default
        _mk_item(uid, set_id, f"inv-{_tag()}", value=None, default=None, rt_ref="app.config.automation_enabled")
        # orphan capability
        ed_id = _mk_edition(uid, f"orph-{_tag()}")
        with engine.begin() as c:
            c.execute(configuration_edition_capabilities.insert().values(
                edition_id=ed_id, capability_code="not.a.real.capability", included=True,
                created_by_user_id=uid))
        report = safety.validate()
        types = {i["type"] for i in report["issues"]}
        assert "invalid_configuration" in types and "orphan_capability" in types
        assert report["ok"] is False
    finally:
        _cleanup(uid)


# --- scheduler / automation / analytics --------------------------------------

def test_automation_dispatch_and_scheduler_wiring():
    from app.jobs import scheduler
    from app.services.automation import dispatch
    assert "runtime_refresh" in dispatch.DISPATCH_REGISTRY
    assert hasattr(scheduler, "run_runtime_refresh")
    from app.config import runtime_refresh_enabled, runtime_refresh_interval_seconds
    assert isinstance(runtime_refresh_enabled(), bool)
    assert runtime_refresh_interval_seconds() >= 15


def test_analytics_consumes_runtime_metrics():
    uid = _uid()
    try:
        from app.services.analytics import sources
        from app.services.analytics.metrics import METRICS
        set_id = _mk_set(uid, _tag())
        _mk_item(uid, set_id, f"an-{_tag()}", value="V")
        before = sources.runtime_active_snapshot_count(_principal(uid))
        snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        assert sources.runtime_active_snapshot_count(_principal(uid)) == before + 1
        for key in ("runtime_active_snapshots", "runtime_cache_hit_ratio",
                    "runtime_configuration_resolutions", "runtime_edition_utilization",
                    "runtime_active_features"):
            assert key in METRICS
    finally:
        _cleanup(uid)


def test_overview_metrics_aggregates():
    m = svc.overview_metrics(None)
    for k in ("snapshots", "latest_version", "cache_version", "hydrated", "validation_ok"):
        assert k in m


# --- append-only ledger + separation invariants ------------------------------

def test_runtime_events_append_only():
    uid = _uid()
    try:
        snap = snapshots.build_snapshot(scope="manual", actor_user_id=uid)
        assert any(e["event_type"] in ("snapshot_created", "snapshot_refreshed")
                   for e in audit_history(None, entity_type="snapshot", entity_id=snap["id"]))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(runtime_events).where(runtime_events.c.entity_id == snap["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(runtime_events).where(runtime_events.c.entity_id == snap["id"]))
    finally:
        _cleanup(uid)


def test_engine_never_edits_configuration_metadata():
    # The runtime engine reads D.27 metadata but must never write it. Assert no configuration_* write
    # verbs appear in any runtime service module.
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for name in ("metadata_reader.py", "resolution.py", "features.py", "editions.py", "snapshots.py",
                 "engine.py", "safety.py", "cache.py", "context.py", "common.py", "service.py"):
        src = (root / name).read_text()
        for verb in (".insert()", ".update()", ".delete()"):
            for tbl in ("configuration_items", "configuration_feature_flags", "configuration_editions",
                        "configuration_preferences", "configuration_environment_overrides"):
                assert f"{tbl}{verb}" not in src, f"{name} writes {tbl}"


def test_metadata_domain_does_not_import_runtime():
    import pathlib

    from app.services import configuration
    root = pathlib.Path(configuration.__file__).parent
    for f in root.glob("*.py"):
        assert "app.services.runtime" not in f.read_text(), f"{f.name} imports runtime"


def test_runtime_does_not_import_composition_layers():
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for f in root.glob("*.py"):
        src = f.read_text()
        for layer in ("annual_review", "business_owner", "app.services.reporting"):
            assert f"import {layer}" not in src and f"{layer} import" not in src, f"{f.name}:{layer}"


def test_migration_seeds_capabilities():
    with engine.connect() as c:
        caps = set(c.scalars(text("SELECT code FROM capabilities WHERE code LIKE 'runtime.%'")))
    assert {"runtime.view", "runtime.manage", "runtime.execute", "runtime.audit",
            "runtime.admin"} <= caps


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/runtime") for pattern, _cap in RULES)
    assert not any(pattern.search("/runtime/snapshots/x") for pattern, _cap in RULES)
