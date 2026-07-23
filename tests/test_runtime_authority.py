"""Runtime Authority, Legacy Retirement & Governance tests (Phase D.31).

Covers runtime default activation (seeded D.27 metadata drives the migrated behaviors), legacy
retirement (registry authoritative/retired + compatibility shims), governance validation (passes +
detects missing/orphan definitions and orphan capabilities), the advisor-workspace section gate
(runtime consulted alongside the capability), runtime authority coverage, the compatibility-fallback
counter, analytics authority metrics, and the governance-event ledger. Behavior is preserved: the
seeded runtime values equal the legacy defaults. The runtime engine, RBAC, and D.5 golden are
untouched.
"""
import uuid

from sqlalchemy import delete, func, select, text

from app.db import (
    configuration_edition_capabilities,
    configuration_editions,
    configuration_feature_flags,
    engine,
    runtime_behaviors,
    runtime_events,
    users,
)
from app.services.runtime import behavior, consumption, governance
from app.services.runtime.cache import RUNTIME_CACHE


def _tag():
    return uuid.uuid4().hex[:8]


def _reset_cache():
    RUNTIME_CACHE.invalidate()


# --- runtime default activation (behavior-preserving) ------------------------

def test_runtime_defaults_activated_and_behavior_preserved():
    _reset_cache()
    # the seeded feature flags now DRIVE the behaviors (runtime is authoritative), unchanged values.
    ctx = consumption.runtime_context()
    assert ctx.feature_defined("analytics.executive_metrics")
    assert ctx.feature_enabled("analytics.executive_metrics") is True
    assert ctx.feature_defined("microsoft365.sync") and ctx.feature_enabled("microsoft365.sync") is True
    # seeded config items equal the legacy app.config defaults
    assert consumption.config_value("benefits.new_hire_window_days", default=999) == 30
    assert consumption.config_value("benefits.renewal_warning_days", default=999) == 60
    assert consumption.config_value("microsoft365.sharepoint_site_ids", default="LEGACY") == ""
    # advisor-workspace section flags are seeded + enabled
    for s in ("work", "tasks", "exceptions"):
        assert ctx.feature_enabled(f"advisor_workspace.section.{s}") is True


def test_retired_and_authoritative_registry_state():
    with engine.connect() as c:
        retired = set(c.scalars(select(runtime_behaviors.c.code).where(runtime_behaviors.c.status == "retired")))
        authoritative = set(c.scalars(select(runtime_behaviors.c.code).where(runtime_behaviors.c.authoritative.is_(True))))
        shims = set(c.scalars(select(runtime_behaviors.c.code).where(runtime_behaviors.c.compatibility_shim.is_(True))))
    assert {"analytics.executive_metrics", "microsoft365.sync", "benefits.detector_windows",
            "microsoft365.sharepoint_scope"} <= retired
    assert "advisor_workspace.sections" in authoritative
    assert shims == {"automation.job_dispatch", "reporting.optional_modules"}


def test_coverage_authority_and_adoption():
    cov = behavior.coverage()
    assert cov["adoption_pct"] == 100.0 and cov["authority_pct"] == 71.4
    assert cov["retired"] == 4 and cov["authoritative"] == 5 and cov["compatibility_shims"] == 2


# --- governance --------------------------------------------------------------

def test_governance_passes_with_full_coverage():
    report = governance.validate()
    assert report["ok"] is True and report["issue_count"] == 0
    assert report["coverage"]["coverage_pct"] == 100.0 and report["coverage"]["authoritative"] == 5


def test_governance_detects_missing_definition():
    # delete a seeded authoritative flag → governance flags a missing definition
    with engine.begin() as c:
        c.execute(delete(configuration_feature_flags)
                  .where(configuration_feature_flags.c.code == "microsoft365.sync"))
    try:
        report = governance.validate()
        assert report["ok"] is False
        assert any(f["type"] == "missing_definition" and f.get("definition") == "microsoft365.sync"
                   for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(text("INSERT INTO configuration_feature_flags (code, name, status, enabled, "
                           "rollout_percentage) VALUES ('microsoft365.sync','microsoft365.sync',"
                           "'active',true,100)"))


def test_governance_detects_orphan_capability():
    uid = _mk_uid()
    ed_id = None
    try:
        with engine.begin() as c:
            ed_id = c.execute(configuration_editions.insert().values(
                code=f"ed-{_tag()}", name="E", tier="standard", status="active",
                created_by_user_id=uid).returning(configuration_editions.c.id)).scalar_one()
            c.execute(configuration_edition_capabilities.insert().values(
                edition_id=ed_id, capability_code="not.a.real.capability", included=True,
                created_by_user_id=uid))
        report = governance.validate()
        assert any(f["type"] == "orphan_capability" and f.get("capability_code") == "not.a.real.capability"
                   for f in report["findings"])
    finally:
        with engine.begin() as c:
            if ed_id is not None:
                c.execute(delete(configuration_edition_capabilities)
                          .where(configuration_edition_capabilities.c.edition_id == ed_id))
                c.execute(delete(configuration_editions).where(configuration_editions.c.id == ed_id))


def test_governance_validation_records_event():
    before = _governance_event_count()
    report = governance.record_validation(actor_user_id=None)
    assert "ok" in report
    assert _governance_event_count() == before + 1


# --- advisor-workspace section gate ------------------------------------------

def test_advisor_workspace_section_gate_runtime_consulted():
    _reset_cache()
    # with the seed, the section is enabled (behavior unchanged)
    assert consumption.feature_enabled("advisor_workspace.section.tasks", default=True) is True
    # runtime can now disable the section (authority) — deterministic
    with engine.begin() as c:
        c.execute(text("UPDATE configuration_feature_flags SET rollout_percentage=0 "
                       "WHERE code='advisor_workspace.section.tasks'"))
    try:
        _reset_cache()
        assert consumption.feature_enabled("advisor_workspace.section.tasks", default=True) is False
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE configuration_feature_flags SET rollout_percentage=100 "
                           "WHERE code='advisor_workspace.section.tasks'"))
        _reset_cache()


def test_advisor_workspace_dashboard_runs():
    # behavior-preserving smoke: the dashboard composes without error for a principal.
    from app.security.models import Principal
    from app.services.advisor_workspace import get_daily_dashboard
    p = Principal(1, "a@e.test", "A", frozenset({"work.read", "task.read", "exception.read"}))
    dash = get_daily_dashboard(p)
    assert "tasks" in dash and "exceptions" in dash


# --- compatibility shim counter + analytics ----------------------------------

def test_compatibility_shim_counter_increments_on_fallback():
    before = consumption.adoption_stats().get("compatibility_fallbacks", 0)
    # an undefined feature consulted with shim=True → a compatibility fallback
    consumption.feature_enabled(f"automation.job.undefined-{_tag()}", default=True, shim=True)
    assert consumption.adoption_stats().get("compatibility_fallbacks", 0) == before + 1


def test_analytics_authority_metrics():
    from app.services.analytics import sources
    from app.services.analytics.metrics import METRICS
    for key in ("retired_behavior_count", "runtime_authority_percent", "runtime_definition_coverage",
                "runtime_governance_issues", "compatibility_shim_count", "compatibility_fallbacks"):
        assert key in METRICS
    assert sources.runtime_retired_behavior_count(None) == 4
    assert sources.runtime_authority_pct(None) == 71.4
    assert sources.runtime_definition_coverage_pct(None) == 100.0
    assert sources.runtime_compatibility_shim_count(None) == 2


# --- architecture invariants -------------------------------------------------

def test_governance_never_edits_metadata():
    import pathlib
    src = pathlib.Path(governance.__file__).read_text()
    for verb in (".insert()", ".update()", ".delete()"):
        for tbl in ("configuration_items", "configuration_feature_flags", "configuration_editions",
                    "runtime_behaviors"):
            assert f"{tbl}{verb}" not in src, f"governance writes {tbl}"


def test_migrated_call_sites_are_shim_marked():
    import pathlib
    base = pathlib.Path(governance.__file__).parents[3]
    # Sites still consuming the runtime engine directly keep the compatibility shim.
    for rel in ("app/services/analytics/metrics.py", "app/services/benefits_detectors.py"):
        assert "shim=True" in (base / rel).read_text(), rel
    # (D.32) The automation/reporting/Microsoft-365 decisions were centralized behind the Runtime
    # Policy Engine; the compatibility shim now lives in the policy definitions, which carry it into
    # the consumption API on behalf of those call sites.
    assert "shim=True" in (base / "app/services/policy/definitions.py").read_text()


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/runtime/behavior/governance") for pattern, _cap in RULES)


# --- helpers -----------------------------------------------------------------

def _mk_uid():
    with engine.begin() as c:
        t = _tag()
        return c.execute(users.insert().values(
            email=f"au-{t}@e.test", normalized_email=f"au-{t}@e.test", display_name="U",
            status="active").returning(users.c.id)).scalar_one()


def _governance_event_count():
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(runtime_events).where(
            runtime_events.c.entity_type == "governance",
            runtime_events.c.event_type == "governance_validation_completed")) or 0
