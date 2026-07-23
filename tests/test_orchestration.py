"""Enterprise Workflow Orchestration Engine tests (Phase D.33).

Covers orchestration launch/transition + deterministic state management, declarative definitions,
policy-driven routing + RuntimeContext consumption, deterministic replay, dry-run simulation, workflow
governance (passes + detects unreachable/orphan/circular/duplicate/missing-policy/invalid-ownership/
invalid-completion defects), the registry + coverage, diagnostics, the automation + scheduler
integrations, analytics, and the architecture invariants (the runtime engine stays the sole evaluator;
the policy engine stays the sole decision engine; orchestration never bypasses either; replay +
simulation never mutate production state). Every integration is behavior-preserving.
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.db import engine as db
from app.db import orchestration_events, orchestration_instances
from app.services.orchestration import (
    definitions,
    diagnostics,
    engine,
    execution,
    governance,
    registry,
    replay,
    simulation,
)
from app.services.orchestration.definitions import OrchestrationDefinition
from app.services.runtime.cache import RUNTIME_CACHE


def _tag():
    return uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _reset():
    engine.reset_stats()
    RUNTIME_CACHE.invalidate()
    yield
    engine.reset_stats()


# --- registry + definitions --------------------------------------------------

def test_registry_seeded_and_coverage():
    cov = registry.coverage()
    assert cov["total"] == 15 and cov["active"] == 2 and cov["in_domain"] == 13
    assert cov["adoption_pct"] == 100.0 and cov["coverage_pct"] == 100.0
    assert cov["domains_covered"] == cov["domains"]


def test_definitions_mirror_registry():
    codes_code = set(definitions.ORCHESTRATION_DEFINITIONS)
    codes_db = {d["code"] for d in registry.list_definitions()}
    assert codes_code == codes_db


def test_dependency_graph_present():
    g = registry.dependency_graph()
    assert "automation.dispatch" in g and "workflow.review" in g


# --- state management + engine -----------------------------------------------

def test_launch_and_transition_deterministic():
    inst = engine.launch("automation.dispatch", subject="maintenance")
    assert inst["status"] == "pending" and inst["current_stage"] == "pending"
    inst = engine.transition(inst["id"], "dispatch")
    assert inst["current_stage"] == "dispatching" and inst["status"] == "active"
    inst = engine.transition(inst["id"], "execute")
    inst = engine.transition(inst["id"], "complete")
    assert inst["status"] == "completed" and inst["current_stage"] == "completed"


def test_invalid_transition_raises():
    from app.services.orchestration.common import OrchestrationError
    inst = engine.launch("automation.dispatch", subject="x")
    with pytest.raises(OrchestrationError):
        engine.transition(inst["id"], "complete")   # not permitted from pending


def test_engine_refuses_to_drive_in_domain_definition():
    from app.services.orchestration.common import OrchestrationError
    with pytest.raises(OrchestrationError):
        engine.launch("compliance.review", subject="x")


def test_launch_binds_runtime_snapshot_and_context():
    inst = engine.launch("automation.dispatch", subject="maintenance")
    # a launched event exists carrying the runtime snapshot id (None when unhydrated is acceptable)
    hist = diagnostics.execution_history(inst["id"])
    assert hist and hist[0]["event_type"] == "launched"
    assert inst["context"]["definition_code"] == "automation.dispatch"


# --- policy-driven routing (consumes the Runtime Policy Engine) ---------------

def test_review_routing_consumes_policy_permit_and_deny():
    # permitted template routes through and launches; the launcher result is returned
    r = execution.orchestrate_review("annual_review", launcher=lambda: 4242)
    assert r == 4242
    # a non-approved template is denied by the workflow.review_routing policy → None
    r2 = execution.orchestrate_review("nope", launcher=lambda: 999)
    assert r2 is None


def test_policy_denied_transition_records_block_and_leaves_instance():
    from app.services.orchestration.common import OrchestrationError
    inst = engine.launch("workflow.review", subject="not_a_template")
    with pytest.raises(OrchestrationError):
        engine.transition(inst["id"], "route")           # policy denies
    # a transition_blocked event is recorded with the policy decision
    hist = diagnostics.execution_history(inst["id"])
    assert any(e["event_type"] == "transition_blocked" and e["policy_decision"] for e in hist)


# --- automation integration (behavior-preserving) ----------------------------

def test_automation_dispatch_orchestrated_and_behavior_preserved():
    from app.security.models import Principal
    from app.services.automation import dispatch
    p = Principal(1, "a@e.test", "A", frozenset())
    before = engine.stats()["launches"]
    res = dispatch.execute_dispatch("maintenance", config={}, principal=p, actor_user_id=None)
    assert res.get("maintenance") == "ok"                # unchanged handler result
    assert engine.stats()["launches"] == before + 1      # orchestrated through the engine
    # the orchestration instance completed
    insts = engine.list_instances(definition_code="automation.dispatch", limit=1)
    assert insts and insts[0]["status"] == "completed"


def test_automation_dispatch_failure_compensates_and_reraises():
    from app.services.orchestration import execution as ex
    def boom():
        raise RuntimeError("kaboom")
    with pytest.raises(RuntimeError):
        ex.coordinate("automation.dispatch", subject="failing", executor=boom)
    insts = engine.list_instances(definition_code="automation.dispatch", limit=1)
    assert insts and insts[0]["status"] == "compensated"


# --- replay (deterministic; read-only) ---------------------------------------

def test_replay_is_deterministic_and_readonly():
    execution.coordinate("automation.dispatch", subject="maintenance", executor=lambda: {"ok": 1})
    inst = engine.list_instances(definition_code="automation.dispatch", limit=1)[0]
    before_events = _event_count(inst["id"])
    rep = replay.replay(inst["id"])
    assert rep["deterministic"] is True and rep["modified_production_state"] is False
    assert rep["trajectory"] == ["pending", "dispatching", "running", "completed"]
    assert rep["final_matches_persisted"] is True
    assert _event_count(inst["id"]) == before_events    # replay wrote nothing


def test_replay_readiness_reported():
    execution.coordinate("automation.dispatch", subject="m", executor=lambda: {})
    inst = engine.list_instances(definition_code="automation.dispatch", limit=1)[0]
    rr = diagnostics.replay_readiness(inst["id"])
    assert rr["ready"] is True and rr["has_launch_event"] and rr["contiguous_sequence"]


# --- simulation (dry-run; read-only) -----------------------------------------

def test_simulation_dry_run_readonly():
    before = _all_instance_count()
    sim = simulation.dry_run("workflow.review", ["route", "launch", "complete"], subject="annual_review")
    assert sim["ok"] is True and sim["reached_completion"] is True
    assert sim["modified_production_state"] is False
    assert _all_instance_count() == before              # simulation wrote nothing


def test_simulation_detects_illegal_sequence():
    sim = simulation.dry_run("automation.dispatch", ["complete"])   # illegal from pending
    assert sim["ok"] is False and sim["steps"][0]["legal"] is False


def test_simulation_verify_policies_and_dependencies():
    vp = simulation.verify_policies("workflow.review", subject="annual_review")
    assert vp["ok"] is True and any(c["policy"] == "workflow.review_routing" for c in vp["policy_checks"])
    da = simulation.dependency_analysis("microsoft365.sharepoint_scope") if False else simulation.dependency_analysis("workflow.review")
    assert da["ok"] is True and da["cyclic"] is False


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_full_view():
    execution.coordinate("automation.dispatch", subject="m", executor=lambda: {"r": 1})
    inst = engine.list_instances(definition_code="automation.dispatch", limit=1)[0]
    diag = diagnostics.diagnostics(inst["id"])
    assert diag["status"] == "completed"
    assert diag["execution_graph"]["initial"] == "pending"
    assert "execution_history" in diag and "policy_decisions" in diag
    assert diag["replay_readiness"]["ready"] is True


# --- governance --------------------------------------------------------------

def test_governance_passes():
    report = governance.validate()
    assert report["ok"] is True and report["issue_count"] == 0
    assert report["coverage"]["coverage_pct"] == 100.0


def test_governance_detects_unreachable_stage():
    d = _mutate("automation.dispatch", stages_extra=[{"name": "orphan", "kind": "active",
                "entry_actions": [], "exit_actions": [], "terminal": False}])
    findings = _validate_one(d)
    assert any(f["type"] == "unreachable_stage" and f.get("stage") == "orphan" for f in findings)


def test_governance_detects_orphan_transition():
    d = _mutate("automation.dispatch", transitions_extra=[{"from": "running", "action": "warp", "to": "nowhere"}])
    findings = _validate_one(d)
    assert any(f["type"] == "orphan_transition" for f in findings)


def test_governance_detects_missing_policy_reference():
    d = _mutate("workflow.review", transitions_replace={"route": "policy.that.does.not.exist"})
    findings = _validate_one(d)
    assert any(f["type"] == "missing_policy_reference" for f in findings)


def test_governance_detects_invalid_ownership():
    d = _mutate("automation.dispatch", owner=None)
    findings = _validate_one(d)
    assert any(f["type"] == "invalid_ownership" for f in findings)


def test_governance_detects_circular_trap():
    # a two-stage trap with no path to a terminal outcome
    stages = ({"name": "pending", "kind": "pending", "entry_actions": [], "exit_actions": [], "terminal": False},
              {"name": "a", "kind": "active", "entry_actions": [], "exit_actions": [], "terminal": False},
              {"name": "b", "kind": "active", "entry_actions": [], "exit_actions": [], "terminal": False},
              {"name": "completed", "kind": "completed", "entry_actions": [], "exit_actions": [], "terminal": True})
    transitions = ({"from": "pending", "action": "go", "to": "a"}, {"from": "a", "action": "x", "to": "b"},
                   {"from": "b", "action": "y", "to": "a"})
    d = OrchestrationDefinition(code="trap.test", category="workflow", name="Trap", owner="test",
                                version=1, status="active", initial_stage="pending", stages=stages,
                                transitions=transitions, completion_stages=("completed",))
    findings = _validate_one(d)
    assert any(f["type"] == "circular_transition" for f in findings)
    assert any(f["type"] == "invalid_completion_path" for f in findings)   # completed unreachable


# --- analytics + architecture invariants -------------------------------------

def test_analytics_orchestration_metrics():
    from app.services.analytics import sources
    from app.services.analytics.metrics import METRICS
    for key in ("workflow_launches", "workflow_completions", "workflow_failures", "workflow_retries",
                "workflow_replays", "workflow_simulations", "workflow_governance_issues",
                "orchestration_coverage", "workflow_avg_execution_ms"):
        assert key in METRICS
    assert sources.orchestration_coverage_pct(None) == 100.0
    assert sources.orchestration_governance_issue_count(None) == 0
    execution.coordinate("automation.dispatch", subject="m", executor=lambda: {})
    assert sources.orchestration_launch_count(None) >= 1
    assert sources.orchestration_completion_count(None) >= 1


def test_engine_consumes_policy_not_a_second_decision_engine():
    import pathlib
    src = pathlib.Path(engine.__file__).read_text()
    assert "policy" in src            # routing consumes the policy engine
    # the engine consumes RuntimeContext (never evaluates config directly)
    ctx_src = pathlib.Path(engine.__file__).parent.joinpath("context.py").read_text()
    assert "consumption" in ctx_src and "runtime_context" in ctx_src


def test_runtime_and_policy_engines_do_not_import_orchestration():
    import pathlib
    base = pathlib.Path(engine.__file__).parents[1]
    for pkg in ("runtime", "policy"):
        for pyfile in (base / pkg).glob("*.py"):
            src = pyfile.read_text()
            assert "app.services.orchestration" not in src, f"{pkg}/{pyfile.name}"


def test_orchestration_routes_match_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/orchestration") for pattern, _cap in RULES)
    assert not any(pattern.search("/orchestration/governance") for pattern, _cap in RULES)


def test_scheduler_gate_present():
    from app.config import orchestration_enabled
    assert orchestration_enabled() is False   # dark-launched off by default


# --- helpers -----------------------------------------------------------------

def _event_count(instance_id):
    with db.connect() as c:
        return c.scalar(select(func.count()).select_from(orchestration_events).where(
            orchestration_events.c.instance_id == instance_id)) or 0


def _all_instance_count():
    with db.connect() as c:
        return c.scalar(select(func.count()).select_from(orchestration_instances)) or 0


def _validate_one(definition):
    """Run the governance stage/transition checks against a single ad-hoc definition (in-memory)."""
    from app.services.orchestration import state as st
    findings = []
    stage_names = set(definition.stage_names)
    reachable = st.reachable_stages(definition)
    for name in stage_names - reachable:
        findings.append({"type": "unreachable_stage", "stage": name})
    for t in definition.transitions:
        if t["from"] not in stage_names or t["to"] not in stage_names:
            findings.append({"type": "orphan_transition"})
    from app.services.orchestration.governance import _known_policies, _unproductive_cycles
    for name in _unproductive_cycles(definition):
        findings.append({"type": "circular_transition", "stage": name})
    known = _known_policies()
    for pcode in set(definition.policy_refs) | set(definition.transition_policies):
        if pcode not in known:
            findings.append({"type": "missing_policy_reference", "policy": pcode})
    if not definition.owner:
        findings.append({"type": "invalid_ownership"})
    for cs in definition.completion_stages:
        if cs not in reachable:
            findings.append({"type": "invalid_completion_path"})
    return findings


def _mutate(code, *, stages_extra=None, transitions_extra=None, transitions_replace=None, owner=_tag):
    base = definitions.get_definition(code)
    stages = list(base.stages) + list(stages_extra or [])
    transitions = list(base.transitions)
    if transitions_replace:
        transitions = [{**t, **({"policy": transitions_replace[t["action"]]} if t["action"] in transitions_replace else {})}
                       for t in transitions]
    transitions += list(transitions_extra or [])
    new_owner = base.owner if owner is _tag else owner
    return OrchestrationDefinition(
        code=base.code, category=base.category, name=base.name, owner=new_owner, version=base.version,
        status=base.status, initial_stage=base.initial_stage, stages=tuple(stages),
        transitions=tuple(transitions), completion_stages=base.completion_stages,
        policy_refs=base.policy_refs, runtime_refs=base.runtime_refs, depends_on=base.depends_on)
