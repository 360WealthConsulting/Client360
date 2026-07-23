"""Workflow simulation (Phase D.33) — dry-run execution without touching production state.

Simulates an orchestration definition in memory: a dry-run walk of a proposed action sequence,
transition validation, policy verification (evaluating the routing policies against the current
``RuntimeContext`` — a read), and dependency analysis. Simulation NEVER launches an instance,
transitions anything, or writes to any table — it is a pure read that reports what WOULD happen.
"""
from __future__ import annotations

from . import state
from .definitions import ORCHESTRATION_DEFINITIONS, get_definition


def dry_run(definition_code, actions, *, subject=None) -> dict:
    """Walk ``actions`` from the initial stage, reporting the resulting trajectory and the first illegal
    step (if any). Consults routing policies (read-only) to report whether each policy-gated step would
    be permitted. Never mutates production state."""
    from . import engine
    engine.note_simulation()
    d = get_definition(definition_code)
    if d is None:
        return {"ok": False, "error": f"unknown definition {definition_code!r}"}
    cur = d.initial_stage
    steps = []
    ok = True
    for action in actions:
        tr = state.transition_for(d, cur, action)
        if tr is None:
            steps.append({"from": cur, "action": action, "legal": False})
            ok = False
            break
        permitted, decision = _verify_policy(tr, subject)
        steps.append({"from": cur, "action": action, "to": tr["to"], "legal": True,
                      "policy": tr.get("policy"), "policy_permitted": permitted, "policy_decision": decision})
        if not permitted:
            ok = False
            break
        cur = tr["to"]
    return {"ok": ok, "definition_code": definition_code, "final_stage": cur, "steps": steps,
            "reached_completion": cur in (d.completion_stages or ()), "modified_production_state": False}


def validate_transitions(definition_code) -> dict:
    """Validate every declared transition of a definition (from/to declared, deterministic)."""
    d = get_definition(definition_code)
    if d is None:
        return {"ok": False, "error": f"unknown definition {definition_code!r}"}
    stage_names = set(d.stage_names)
    issues = []
    seen = set()
    for t in d.transitions:
        key = (t["from"], t["action"])
        if key in seen:
            issues.append({"nondeterministic": f"{t['from']}-{t['action']}"})
        seen.add(key)
        if t["from"] not in stage_names or t["to"] not in stage_names:
            issues.append({"orphan": f"{t['from']}-{t['action']}->{t['to']}"})
    return {"ok": not issues, "definition_code": definition_code, "issues": issues,
            "modified_production_state": False}


def verify_policies(definition_code, *, subject=None) -> dict:
    """Evaluate each routing policy of a definition against the current RuntimeContext (a read) and
    report the decision — a pre-flight check that the policies referenced are resolvable and permit the
    happy path. Never mutates production state."""
    d = get_definition(definition_code)
    if d is None:
        return {"ok": False, "error": f"unknown definition {definition_code!r}"}
    checks = []
    for t in d.transitions:
        if not t.get("policy"):
            continue
        permitted, decision = _verify_policy(t, subject)
        checks.append({"action": t["action"], "policy": t["policy"], "permitted": permitted,
                       "decision": decision})
    return {"ok": True, "definition_code": definition_code, "policy_checks": checks,
            "modified_production_state": False}


def dependency_analysis(definition_code) -> dict:
    """Analyze a definition's dependency graph: its declared dependencies and whether they resolve to a
    known, non-retired definition (no dangling/cyclic dependency)."""
    d = get_definition(definition_code)
    if d is None:
        return {"ok": False, "error": f"unknown definition {definition_code!r}"}
    resolved, dangling = [], []
    for dep in d.depends_on:
        (resolved if dep in ORCHESTRATION_DEFINITIONS else dangling).append(dep)
    cyclic = _has_cycle(definition_code)
    return {"ok": not dangling and not cyclic, "definition_code": definition_code,
            "resolved_dependencies": resolved, "dangling_dependencies": dangling, "cyclic": cyclic,
            "modified_production_state": False}


def _verify_policy(transition, subject):
    if not transition.get("policy"):
        return True, None
    try:
        from app.services.policy import evaluate as policy_evaluate
        res = policy_evaluate(transition["policy"], subject=subject)
        return bool(res.decision), res.to_dict()
    except Exception as exc:
        return False, {"error": str(exc)}


def _has_cycle(start) -> bool:
    seen, stack = set(), [(start, {start})]
    while stack:
        node, path = stack.pop()
        d = get_definition(node)
        if d is None:
            continue
        for dep in d.depends_on:
            if dep in path:
                return True
            if dep not in seen:
                seen.add(dep)
                stack.append((dep, path | {dep}))
    return False
