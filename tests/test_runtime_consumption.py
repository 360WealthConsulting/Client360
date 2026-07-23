"""Runtime Consumption Layer tests (Phase D.30).

Covers the standardized consumption API (RuntimeContext.config/feature_enabled/edition/license/
capabilities), legacy-default fallback vs runtime decision, the behavioral-migration registry +
adoption coverage, and behavior-preserving migration of the real switches: automation dispatch,
reporting optional modules, Microsoft 365 sync enablement, benefits detector windows, and the
analytics executive gate. Plus adoption instrumentation, the behavioral-event ledger, and the
architecture invariants (consumption never bypasses the runtime engine; migrated call sites preserve
behavior by default). The runtime engine, RBAC, and D.5 golden are untouched.
"""
import uuid

import pytest
from sqlalchemy import delete, text, update

from app.db import (
    configuration_editions,
    configuration_feature_flags,
    configuration_items,
    configuration_sets,
    engine,
    runtime_behaviors,
    users,
)
from app.security.models import Principal
from app.services.runtime import behavior, consumption
from app.services.runtime.context import RuntimeContext


def _tag():
    return uuid.uuid4().hex[:8]


def _uid():
    with engine.begin() as c:
        t = _tag()
        return c.execute(users.insert().values(
            email=f"cx-{t}@e.test", normalized_email=f"cx-{t}@e.test", display_name="U",
            status="active").returning(users.c.id)).scalar_one()


def _flag(uid, code, *, status="active", enabled=True, rollout=100):
    with engine.begin() as c:
        return c.execute(configuration_feature_flags.insert().values(
            code=code, name=code, status=status, enabled=enabled, rollout_percentage=rollout,
            created_by_user_id=uid).returning(configuration_feature_flags.c.id)).scalar_one()


def _config_item(uid, code, value):
    with engine.begin() as c:
        set_id = c.execute(configuration_sets.insert().values(
            code=f"set-{_tag()}", name="Set", status="active", created_by_user_id=uid)
            .returning(configuration_sets.c.id)).scalar_one()
        return c.execute(configuration_items.insert().values(
            set_id=set_id, code=code, name=code, value_type="string", value=value, status="active",
            version=1, created_by_user_id=uid).returning(configuration_items.c.id)).scalar_one()


def _cleanup(uid):
    from app.services.runtime.cache import RUNTIME_CACHE
    RUNTIME_CACHE.invalidate()
    with engine.begin() as c:
        c.execute(delete(configuration_feature_flags).where(configuration_feature_flags.c.created_by_user_id == uid))
        c.execute(delete(configuration_items).where(configuration_items.c.created_by_user_id == uid))
        c.execute(delete(configuration_sets).where(configuration_sets.c.created_by_user_id == uid))
        c.execute(text("DELETE FROM configuration_edition_assignments WHERE created_by_user_id = :u"), {"u": uid})
        c.execute(text("DELETE FROM configuration_edition_capabilities WHERE created_by_user_id = :u"), {"u": uid})
        c.execute(delete(configuration_editions).where(configuration_editions.c.created_by_user_id == uid))


# --- RuntimeContext consumption API ------------------------------------------

def test_runtime_context_consumption_api():
    ctx = RuntimeContext(
        snapshot_id=1, snapshot_uid="u", snapshot_version=3, edition_code="enterprise",
        license_code="ent-lic", effective_config={"theme": {"value": "dark", "source": "item"}},
        active_features={"beta": {"enabled": True}, "off": {"enabled": False}},
        edition_capabilities=frozenset({"runtime.view"}), resolved=True)
    assert ctx.config("theme") == "dark" and ctx.config("missing", "d") == "d"
    assert ctx.feature_enabled("beta") is True
    assert ctx.feature_enabled("off") is False
    assert ctx.feature_enabled("undefined", default=True) is True   # legacy default when undefined
    assert ctx.feature_defined("beta") and not ctx.feature_defined("undefined")
    assert ctx.edition() == "enterprise" and ctx.license() == "ent-lic"
    assert ctx.capabilities() == frozenset({"runtime.view"})


def test_consumption_legacy_fallback_vs_runtime_decision():
    uid = _uid()
    try:
        # no runtime feature defined → legacy default returned (legacy fallback)
        assert consumption.feature_enabled(f"nope.{_tag()}", default=True) is True
        assert consumption.feature_enabled(f"nope.{_tag()}", default=False) is False
        # define a runtime feature that evaluates disabled → runtime decision overrides the default
        code = f"cons.{_tag()}"
        _flag(uid, code, rollout=0)   # active+enabled but 0% rollout → evaluates disabled
        assert consumption.feature_enabled(code, default=True) is False
    finally:
        _cleanup(uid)


def test_consumption_config_value_fallback_and_runtime():
    uid = _uid()
    try:
        key = f"cfg.{_tag()}"
        assert consumption.config_value(key, default="LEGACY") == "LEGACY"
        _config_item(uid, key, "RUNTIME")
        assert consumption.config_value(key, default="LEGACY") == "RUNTIME"
    finally:
        _cleanup(uid)


def test_edition_capability_projection():
    uid = _uid()
    try:
        with engine.begin() as c:
            ed = c.execute(configuration_editions.insert().values(
                code=f"ed-{_tag()}", name="Ent", tier="enterprise", status="active",
                created_by_user_id=uid).returning(configuration_editions.c.id)).scalar_one()
            c.execute(text("INSERT INTO configuration_edition_capabilities "
                           "(edition_id, capability_code, included, created_by_user_id) "
                           "VALUES (:e, 'runtime.view', true, :u)"), {"e": ed, "u": uid})
            c.execute(text("INSERT INTO configuration_edition_assignments "
                           "(edition_id, scope, status, created_by_user_id) "
                           "VALUES (:e, 'tenant', 'active', :u)"), {"e": ed, "u": uid})
        caps = consumption.capabilities()
        assert "runtime.view" in caps
        assert consumption.edition() is not None
    finally:
        _cleanup(uid)


# --- behavior registry + adoption --------------------------------------------

def test_behavior_registry_seed_and_coverage():
    behaviors = {b["code"]: b for b in behavior.list_behaviors()}
    assert "automation.job_dispatch" in behaviors and behaviors["automation.job_dispatch"]["status"] == "migrated"
    assert "operations.workspace" in behaviors and behaviors["operations.workspace"]["status"] == "deterministic"
    cov = behavior.coverage()
    # After D.31: 4 retired + 3 migrated (automation/reporting shims + advisor) + 4 deterministic.
    assert cov["migrated"] == 3 and cov["retired"] == 4 and cov["deterministic"] == 4
    assert cov["adoption_pct"] == 100.0


def test_behavior_mark_migrated_records_event():
    # mark a behavior migrated; it records a behavioral event to the runtime_events ledger.
    # (Use a deterministic behavior as a throwaway; restore it after so the suite state is stable.)
    row = behavior.mark_migrated("operations.workspace", actor_user_id=None)
    assert row["status"] == "migrated"
    with engine.connect() as c:
        n = c.scalar(text("SELECT count(*) FROM runtime_events WHERE entity_type='behavior' "
                          "AND event_type='runtime_behavior_adopted'"))
    assert n >= 1
    # restore for idempotency of the suite
    with engine.begin() as c:
        c.execute(update(runtime_behaviors).where(runtime_behaviors.c.code == "operations.workspace")
                  .values(status="deterministic", migrated_at=None))


def test_adoption_stats_counts_lookups():
    consumption.feature_enabled(f"x.{_tag()}", default=True)   # a legacy fallback
    stats = consumption.adoption_stats()
    assert stats["feature_lookups"] >= 1 and "runtime_adoption_pct" in stats
    ad = behavior.adoption()
    assert "registry" in ad and "consumption" in ad and ad["adoption_pct"] == 100.0


# --- migrated call sites (behavior-preserving) -------------------------------

def test_automation_dispatch_gate_default_enabled_and_runtime_disable():
    from app.services.automation import dispatch
    p = Principal(1, "a@e.test", "A", frozenset())
    # default: no runtime flag → maintenance job runs (behavior unchanged)
    res = dispatch.execute_dispatch("maintenance", config={}, principal=p, actor_user_id=None)
    assert res.get("maintenance") == "ok"
    uid = _uid()
    try:
        _flag(uid, "automation.job.maintenance", rollout=0)   # runtime-disable the job type
        res2 = dispatch.execute_dispatch("maintenance", config={}, principal=p, actor_user_id=None)
        assert res2.get("skipped") is True and res2.get("reason") == "runtime_disabled"
    finally:
        _cleanup(uid)


def test_reporting_optional_module_gate():
    uid = _uid()
    from app.db import report_definitions
    def_id = None
    try:
        from app.services.reporting import service as reporting_service
        with engine.begin() as c:
            def_id = c.execute(report_definitions.insert().values(
                name=f"Opt {_tag()}", report_type="operational", category="operations", active=True,
                created_by_user_id=uid).returning(report_definitions.c.id)).scalar_one()
        p = Principal(uid, "a@e.test", "A", frozenset({"reporting.view"}))
        # default: included (no runtime flag)
        assert any(d["id"] == def_id for d in reporting_service.list_definitions(p))
        # runtime-disable this optional report module → excluded
        _flag(uid, f"reporting.module.{def_id}", rollout=0)
        assert all(d["id"] != def_id for d in reporting_service.list_definitions(p))
    finally:
        if def_id is not None:
            with engine.begin() as c:
                c.execute(delete(report_definitions).where(report_definitions.c.id == def_id))
        _cleanup(uid)


def test_microsoft365_sync_gate_skips_when_runtime_disabled():
    from app.jobs import microsoft_mail_sync
    from app.services.runtime.cache import RUNTIME_CACHE
    try:
        # microsoft365.sync is now seeded (D.31 authoritative) — disable it at the source, restore after.
        with engine.begin() as c:
            c.execute(text("UPDATE configuration_feature_flags SET rollout_percentage=0 "
                           "WHERE code='microsoft365.sync'"))
        RUNTIME_CACHE.invalidate()
        result = microsoft_mail_sync.sync_recent_mail()
        assert result.get("skipped") is True and result.get("reason") == "runtime_disabled"
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE configuration_feature_flags SET rollout_percentage=100 "
                           "WHERE code='microsoft365.sync'"))
        RUNTIME_CACHE.invalidate()


def test_benefits_detector_window_consumes_runtime_config():
    uid = _uid()
    try:
        from app.services import benefits_detectors
        # a non-seeded window key → legacy default preserved when no runtime config
        key = f"test_window_{_tag()}"
        assert benefits_detectors._cfg_days(key, 30) == 30
        # runtime config item overrides deterministically
        _config_item(uid, f"benefits.{key}", "45")
        assert benefits_detectors._cfg_days(key, 30) == 45
    finally:
        _cleanup(uid)


def test_analytics_metrics_still_computes_by_default():
    # behavior-preserving: with no runtime flag, executive gating is unchanged (capability-driven).
    from app.services.analytics.metrics import compute_metric
    p = Principal(1, "a@e.test", "A", frozenset())
    out = compute_metric(p, "runtime_active_snapshots")   # non-executive metric → computes
    assert out["key"] == "runtime_active_snapshots" and "value" in out


# --- analytics / architecture invariants -------------------------------------

def test_analytics_exposes_adoption_metrics():
    from app.services.analytics.metrics import METRICS
    for key in ("runtime_feature_consumption", "runtime_config_lookups", "legacy_fallback_count",
                "behavior_adoption_percent", "migrated_behavior_count"):
        assert key in METRICS


def test_consumption_goes_through_engine_not_a_second_evaluator():
    import pathlib
    src = pathlib.Path(consumption.__file__).read_text()
    # consumption delegates to the engine (sole evaluator); it does not import composition layers.
    assert "engine" in src
    for layer in ("annual_review", "business_owner", "app.services.reporting"):
        assert f"import {layer}" not in src and f"{layer} import" not in src


def test_migrated_modules_default_preserve_behavior():
    # every migrated call site consults feature_enabled/config_value with an explicit legacy default.
    import pathlib
    checks = {
        "app/services/automation/dispatch.py": "default=True",
        "app/services/reporting/service.py": "default=True",
        "app/jobs/microsoft_mail_sync.py": "default=True",
        "app/jobs/microsoft_document_sync.py": "default=",
        "app/services/analytics/metrics.py": "default=True",
    }
    base = pathlib.Path(consumption.__file__).parents[3]
    for rel, needle in checks.items():
        src = (base / rel).read_text()
        assert "consumption" in src and needle in src, rel


def test_migration_head_and_route_prefix():
    from app.security.middleware import RULES
    assert not any(pattern.search("/runtime/behavior") for pattern, _cap in RULES)
    assert not any(pattern.search("/runtime/behavior/adoption") for pattern, _cap in RULES)


@pytest.fixture(autouse=True)
def _reset_cache():
    from app.services.runtime.cache import RUNTIME_CACHE
    RUNTIME_CACHE.invalidate()
    yield
