"""Runtime Policy Engine, Declarative Rules & Centralized Decision Services tests (Phase D.32).

Covers policy execution + the result model, policy explanations, RuntimeContext integration + policy
composition, the policy registry + coverage, governance (passes + detects orphan / missing-definition /
invalid-capability / circular-dependency), the rewired call sites (advisor workspace, workflow routing,
automation, reporting, Microsoft 365, operations), the in-domain (compliance/notification) policies,
snapshot-scoped caching, analytics, and the architecture invariants (the runtime engine remains the
sole evaluator; policies never bypass RBAC). Every migration is behavior-preserving.
"""
import uuid

import pytest
from sqlalchemy import delete, func, select, text

from app.db import engine, runtime_events, runtime_policies, users
from app.services.policy import engine as policy_engine
from app.services.policy import governance, registry
from app.services.policy.result import PolicyResult
from app.services.runtime.cache import RUNTIME_CACHE
from app.services.runtime.context import RuntimeContext


def _tag():
    return uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _reset():
    policy_engine.reset_stats()
    RUNTIME_CACHE.invalidate()
    yield
    policy_engine.reset_stats()
    RUNTIME_CACHE.invalidate()


def _uid():
    with engine.begin() as c:
        t = _tag()
        return c.execute(users.insert().values(
            email=f"pol-{t}@e.test", normalized_email=f"pol-{t}@e.test", display_name="U",
            status="active").returning(users.c.id)).scalar_one()


def _set_flag_rollout(code, rollout):
    with engine.begin() as c:
        c.execute(text("UPDATE configuration_feature_flags SET rollout_percentage=:r WHERE code=:c"),
                  {"r": rollout, "c": code})
    RUNTIME_CACHE.invalidate()


# --- result model + execution ------------------------------------------------

def test_policy_result_model_shape():
    r = policy_engine.evaluate("advisor_workspace.section.work")
    assert isinstance(r, PolicyResult)
    assert r.policy_id == "advisor_workspace.section.work"
    assert isinstance(r.decision, bool) and r.decision is True
    assert r.explanation and isinstance(r.explanation, str)
    assert r.evaluated_features and r.evaluated_features[0][0] == "advisor_workspace.section.work"
    assert "work.read" in r.evaluated_capabilities
    assert r.evaluated_at is not None
    d = r.to_dict()
    assert set(d) >= {"decision", "explanation", "policy_id", "runtime_snapshot_id",
                      "evaluated_features", "evaluated_capabilities", "evaluated_at"}


def test_policy_consumes_runtime_context():
    # the decision is evaluated against the runtime context (snapshot id flows through to the result)
    ctx = RuntimeContext(snapshot_id=4242, snapshot_uid="uid", snapshot_version=9,
                         edition_code=None, license_code=None,
                         active_features={"advisor_workspace.section.work": {"enabled": True}})
    r = policy_engine.evaluate("advisor_workspace.section.work", context=ctx)
    assert r.runtime_snapshot_id == 4242 and r.decision is True


def test_policy_explanation_present_and_bool_truthiness():
    r = policy_engine.evaluate("workflow.review_routing", subject="annual_review")
    assert bool(r) is True and "approved" in r.explanation
    r2 = policy_engine.evaluate("workflow.review_routing", subject="not_a_template")
    assert bool(r2) is False and "not in the approved set" in r2.explanation


def test_engine_never_raises_on_unknown_policy():
    r = policy_engine.evaluate("nonsense.policy", default=True)
    assert r.decision is True and "unknown policy" in r.explanation


# --- registry + coverage -----------------------------------------------------

def test_registry_seeded_and_coverage():
    cov = registry.coverage()
    assert cov["total"] == 13 and cov["active"] == 9 and cov["in_domain"] == 4
    assert cov["adoption_pct"] == 100.0 and cov["coverage_pct"] == 100.0
    assert cov["areas_covered"] == 10


def test_dependency_graph_reflects_composition():
    g = registry.dependency_graph()
    assert "advisor_workspace.section.work" in g["advisor_workspace.section.tasks"]
    assert "advisor_workspace.section.work" in g["advisor_workspace.section.exceptions"]
    assert "microsoft365.sync_eligibility" in g["microsoft365.sharepoint_scope"]


# --- governance --------------------------------------------------------------

def test_governance_passes_with_full_coverage():
    report = governance.validate()
    assert report["ok"] is True and report["issue_count"] == 0
    assert report["coverage"]["coverage_pct"] == 100.0
    assert report["coverage"]["definition_coverage_pct"] == 100.0


def test_governance_detects_orphan_policy():
    code = f"bogus.orphan.{_tag()}"
    with engine.begin() as c:
        c.execute(runtime_policies.insert().values(code=code, category="bogus", name="Orphan",
                                                   status="active"))
    try:
        report = governance.validate()
        assert report["ok"] is False
        assert any(f["type"] == "orphan_policy" and f.get("policy") == code for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(delete(runtime_policies).where(runtime_policies.c.code == code))


def test_governance_detects_missing_runtime_definition():
    # delete a seeded authoritative flag → the microsoft365.sync_eligibility policy loses its definition
    with engine.begin() as c:
        c.execute(text("DELETE FROM configuration_feature_flags WHERE code='microsoft365.sync'"))
    try:
        report = governance.validate()
        assert any(f["type"] == "missing_runtime_definition"
                   and f.get("policy") == "microsoft365.sync_eligibility" for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(text("INSERT INTO configuration_feature_flags (code, name, status, enabled, "
                           "rollout_percentage) VALUES ('microsoft365.sync','microsoft365.sync',"
                           "'active',true,100)"))


def test_governance_detects_invalid_capability_reference():
    with engine.begin() as c:
        c.execute(text("UPDATE runtime_policies SET required_capabilities=CAST(:v AS json) "
                       "WHERE code='operations.timeline_publish'"),
                  {"v": '["not.a.real.capability"]'})
    try:
        report = governance.validate()
        assert any(f["type"] == "invalid_capability_reference"
                   and f.get("capability") == "not.a.real.capability" for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE runtime_policies SET required_capabilities=CAST(:v AS json) "
                           "WHERE code='operations.timeline_publish'"), {"v": '["operations.view"]'})


def test_governance_detects_circular_dependency():
    # make section.work depend on section.tasks → a cycle with the existing tasks→work edge
    with engine.begin() as c:
        c.execute(text("UPDATE runtime_policies SET depends_on=CAST(:v AS json) "
                       "WHERE code='advisor_workspace.section.work'"),
                  {"v": '["advisor_workspace.section.tasks"]'})
    try:
        report = governance.validate()
        assert any(f["type"] == "circular_dependency" for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE runtime_policies SET depends_on=CAST('[]' AS json) "
                           "WHERE code='advisor_workspace.section.work'"))


def test_governance_validation_records_event():
    before = _policy_event_count("policy_governance_validated")
    report = governance.record_validation(actor_user_id=None)
    assert "ok" in report
    assert _policy_event_count("policy_governance_validated") == before + 1


# --- rewired call sites (behavior-preserving) --------------------------------

def test_advisor_workspace_section_policy_and_dashboard():
    from app.security.models import Principal
    from app.services.advisor_workspace import get_daily_dashboard
    # default: the section policy permits (seeded feature enabled)
    assert policy_engine.evaluate("advisor_workspace.section.tasks").decision is True
    p = Principal(1, "a@e.test", "A", frozenset({"work.read", "task.read", "exception.read"}))
    dash = get_daily_dashboard(p)
    assert "tasks" in dash and "exceptions" in dash


def test_advisor_workspace_section_runtime_disable():
    try:
        _set_flag_rollout("advisor_workspace.section.tasks", 0)
        assert policy_engine.evaluate("advisor_workspace.section.tasks").decision is False
    finally:
        _set_flag_rollout("advisor_workspace.section.tasks", 100)


def test_policy_composition_blocks_on_dependency():
    # disabling the work section blocks the tasks section (composition ANDs the dependency)
    try:
        _set_flag_rollout("advisor_workspace.section.work", 0)
        r = policy_engine.evaluate("advisor_workspace.section.tasks")
        assert r.decision is False and "advisor_workspace.section.work" in r.dependencies
    finally:
        _set_flag_rollout("advisor_workspace.section.work", 100)


def test_workflow_review_routing_via_service():
    assert policy_engine.evaluate("workflow.review_routing", subject="annual_review").decision is True
    assert policy_engine.evaluate("workflow.review_routing", subject="insurance_review").decision is True
    assert policy_engine.evaluate("workflow.review_routing", subject="arbitrary").decision is False


def test_automation_job_execution_policy_and_dispatch():
    from app.security.models import Principal
    from app.services.automation import dispatch
    p = Principal(1, "a@e.test", "A", frozenset())
    # default enabled → maintenance job runs
    assert dispatch.execute_dispatch("maintenance", config={}, principal=p, actor_user_id=None).get("maintenance") == "ok"
    # runtime-disable the job type → policy False → dispatch skips
    uid = _uid()
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO configuration_feature_flags (code, name, status, enabled, "
                           "rollout_percentage) VALUES ('automation.job.maintenance',"
                           "'automation.job.maintenance','active',true,0)"))
        RUNTIME_CACHE.invalidate()
        assert policy_engine.evaluate("automation.job_execution", subject="maintenance").decision is False
        res = dispatch.execute_dispatch("maintenance", config={}, principal=p, actor_user_id=None)
        assert res.get("skipped") is True and res.get("reason") == "runtime_disabled"
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM configuration_feature_flags WHERE code='automation.job.maintenance'"))
        _cleanup_user(uid)
        RUNTIME_CACHE.invalidate()


def test_reporting_module_eligibility_policy_and_service():
    from app.db import report_definitions
    uid = _uid()
    def_id = None
    try:
        from app.security.models import Principal
        from app.services.reporting import service as reporting_service
        with engine.begin() as c:
            def_id = c.execute(report_definitions.insert().values(
                name=f"Opt {_tag()}", report_type="operational", category="operations", active=True,
                created_by_user_id=uid).returning(report_definitions.c.id)).scalar_one()
        p = Principal(uid, "a@e.test", "A", frozenset({"reporting.view"}))
        assert any(d["id"] == def_id for d in reporting_service.list_definitions(p))
        with engine.begin() as c:
            c.execute(text("INSERT INTO configuration_feature_flags (code, name, status, enabled, "
                           "rollout_percentage) VALUES (:c,:c,'active',true,0)"),
                      {"c": f"reporting.module.{def_id}"})
        RUNTIME_CACHE.invalidate()
        assert all(d["id"] != def_id for d in reporting_service.list_definitions(p))
    finally:
        with engine.begin() as c:
            if def_id is not None:
                c.execute(delete(report_definitions).where(report_definitions.c.id == def_id))
                c.execute(text("DELETE FROM configuration_feature_flags WHERE code=:c"),
                          {"c": f"reporting.module.{def_id}"})
        _cleanup_user(uid)
        RUNTIME_CACHE.invalidate()


def test_microsoft365_policies():
    assert policy_engine.evaluate("microsoft365.sync_eligibility").decision is True
    # the sharepoint scope returns the runtime/legacy config value (behavior-preserving)
    r = policy_engine.evaluate("microsoft365.sharepoint_scope", default="")
    assert r.decision == "" and "microsoft365.sync_eligibility" in r.dependencies


def test_operations_timeline_publish_policy():
    assert policy_engine.evaluate("operations.timeline_publish", subject="task_completed").decision is True
    assert policy_engine.evaluate("operations.timeline_publish", subject="project_created").decision is True
    assert policy_engine.evaluate("operations.timeline_publish", subject="bogus_kind").decision is False


def test_in_domain_policies_registered_but_not_evaluated():
    assert policy_engine.evaluate("compliance.decision_routing").decision is None
    assert policy_engine.evaluate("notification.routing").decision is None
    with engine.connect() as c:
        in_domain = set(c.scalars(select(runtime_policies.c.code).where(
            runtime_policies.c.in_domain.is_(True))))
    assert {"compliance.decision_routing", "notification.routing", "document.behavior",
            "scheduling.behavior"} == in_domain


# --- caching + lifecycle -----------------------------------------------------

def test_per_context_cache_hits():
    # repeated decisions against the SAME immutable context object are served from the cache
    ctx = RuntimeContext(snapshot_id=77, snapshot_uid="u", snapshot_version=3,
                         edition_code=None, license_code=None,
                         active_features={"advisor_workspace.section.work": {"enabled": True}})
    policy_engine.reset_stats()
    a = policy_engine.evaluate("advisor_workspace.section.work", context=ctx)
    b = policy_engine.evaluate("advisor_workspace.section.work", context=ctx)
    assert a.cached is False and b.cached is True and b.decision == a.decision
    assert policy_engine.evaluation_stats()["cache_hits"] >= 1


def test_no_cache_across_separate_contexts():
    # separate (internally-built) contexts never share a cache entry, so a live runtime change is
    # reflected immediately (runtime authority) — cross-call decisions always re-evaluate
    policy_engine.reset_stats()
    policy_engine.evaluate("advisor_workspace.section.work")
    policy_engine.evaluate("advisor_workspace.section.work")
    assert policy_engine.evaluation_stats()["cache_hits"] == 0


def test_policy_lifecycle_deprecate_records_event():
    before = _policy_event_count("policy_deprecated")
    try:
        row = registry.deprecate("document.behavior", reason="test", actor_user_id=None)
        assert row["status"] == "deprecated"
        assert _policy_event_count("policy_deprecated") == before + 1
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE runtime_policies SET status='in_domain', deprecated_at=NULL, "
                           "deprecated_reason=NULL WHERE code='document.behavior'"))


# --- analytics + architecture invariants -------------------------------------

def test_analytics_policy_metrics():
    from app.services.analytics import sources
    from app.services.analytics.metrics import METRICS
    for key in ("policy_evaluations", "policy_cache_hits", "policy_governance_issues",
                "policy_coverage", "policy_adoption_percent", "policy_execution_latency"):
        assert key in METRICS
    assert sources.policy_coverage_pct(None) == 100.0
    assert sources.policy_adoption_pct(None) == 100.0
    assert sources.policy_governance_issue_count(None) == 0
    policy_engine.evaluate("advisor_workspace.section.work")
    assert sources.policy_evaluation_count(None) >= 1


def test_policy_engine_consumes_runtime_not_a_second_evaluator():
    import pathlib
    src = pathlib.Path(policy_engine.__file__).read_text()
    # the engine consumes the runtime consumption API (the runtime engine remains the sole evaluator)
    assert "consumption" in src
    # it does not import composition layers
    for layer in ("annual_review", "business_owner", "app.services.reporting"):
        assert f"import {layer}" not in src


def test_runtime_engine_does_not_import_policy_layer():
    import pathlib
    runtime_dir = pathlib.Path(policy_engine.__file__).parents[1] / "runtime"
    for pyfile in runtime_dir.glob("*.py"):
        src = pyfile.read_text()
        assert "app.services.policy" not in src and "from ..policy" not in src, pyfile.name


def test_policy_routes_match_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/runtime/policy") for pattern, _cap in RULES)
    assert not any(pattern.search("/runtime/policy/governance") for pattern, _cap in RULES)


# --- helpers -----------------------------------------------------------------

def _cleanup_user(uid):
    with engine.begin() as c:
        c.execute(delete(users).where(users.c.id == uid))


def _policy_event_count(event_type):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(runtime_events).where(
            runtime_events.c.entity_type == "policy",
            runtime_events.c.event_type == event_type)) or 0
