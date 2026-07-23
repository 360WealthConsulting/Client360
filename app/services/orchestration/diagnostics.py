"""Workflow diagnostics (Phase D.33) — read-only inspection of an orchestration instance.

Provides the execution history, the current stage, the execution graph, the pending actions, the
blocked stages, the recorded policy decisions, the runtime snapshot, and the replay-readiness of a
running (or finished) orchestration instance. Read-only — it never mutates production state.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import engine as db
from app.db import orchestration_events, orchestration_instances

from . import state
from .definitions import get_definition


def _instance(instance_id) -> dict | None:
    with db.connect() as c:
        row = c.execute(select(orchestration_instances).where(
            orchestration_instances.c.id == instance_id)).mappings().first()
        return dict(row) if row else None


def execution_history(instance_id) -> list[dict]:
    """The append-only event ledger for an instance, in order (the replay source)."""
    with db.connect() as c:
        return [dict(r) for r in c.execute(select(orchestration_events).where(
            orchestration_events.c.instance_id == instance_id).order_by(orchestration_events.c.seq)).mappings()]


def execution_graph(definition_code) -> dict:
    """The definition's stage/transition graph (nodes + edges) for visualization."""
    d = get_definition(definition_code)
    if d is None:
        return {}
    return {"nodes": [{"stage": s["name"], "kind": s.get("kind"), "terminal": bool(s.get("terminal"))}
                      for s in d.stages],
            "edges": [{"from": t["from"], "action": t["action"], "to": t["to"], "policy": t.get("policy")}
                      for t in d.transitions],
            "initial": d.initial_stage, "completion": list(d.completion_stages)}


def pending_actions(instance_id) -> list[str]:
    """The actions available from the instance's current stage (empty when terminal)."""
    inst = _instance(instance_id)
    if inst is None:
        return []
    d = get_definition(inst["definition_code"])
    if d is None:
        return []
    return state.actions_from(d, inst.get("current_stage"))


def blocked_stages(instance_id) -> list[dict]:
    """Transitions from the current stage that a routing policy would currently deny."""
    inst = _instance(instance_id)
    if inst is None:
        return []
    d = get_definition(inst["definition_code"])
    if d is None:
        return []
    blocked = []
    for t in d.transitions:
        if t["from"] != inst.get("current_stage") or not t.get("policy"):
            continue
        try:
            from app.services.policy import evaluate as policy_evaluate
            res = policy_evaluate(t["policy"], subject=inst.get("subject"))
            if not res.decision:
                blocked.append({"action": t["action"], "to": t["to"], "policy": t["policy"],
                                "explanation": res.explanation})
        except Exception:
            pass
    return blocked


def policy_decisions(instance_id) -> list[dict]:
    """Every recorded policy decision from the instance's event ledger (deterministic)."""
    return [{"seq": e["seq"], "action": e["action"], "decision": e["policy_decision"]}
            for e in execution_history(instance_id) if e.get("policy_decision") is not None]


def diagnostics(instance_id) -> dict:
    """The full diagnostic view of an instance."""
    inst = _instance(instance_id)
    if inst is None:
        return {}
    d = get_definition(inst["definition_code"])
    history = execution_history(instance_id)
    return {"instance": inst, "current_stage": inst.get("current_stage"), "status": inst["status"],
            "runtime_snapshot": inst.get("runtime_snapshot_id"),
            "execution_history": history, "execution_graph": execution_graph(inst["definition_code"]),
            "pending_actions": pending_actions(instance_id), "blocked_stages": blocked_stages(instance_id),
            "policy_decisions": policy_decisions(instance_id),
            "replay_readiness": replay_readiness(instance_id, history=history, definition=d)}


def replay_readiness(instance_id, *, history=None, definition=None) -> dict:
    """Whether an instance can be deterministically replayed: it needs a runtime snapshot, a launched
    event, and a contiguous event sequence."""
    history = history if history is not None else execution_history(instance_id)
    inst = _instance(instance_id)
    if inst is None:
        return {"ready": False, "reason": "instance not found"}
    seqs = [e["seq"] for e in history]
    contiguous = seqs == list(range(1, len(seqs) + 1))
    has_launch = any(e["event_type"] == "launched" for e in history)
    has_snapshot = inst.get("runtime_snapshot_id") is not None
    known_def = (definition or get_definition(inst["definition_code"])) is not None
    ready = bool(history) and contiguous and has_launch and known_def
    return {"ready": ready, "event_count": len(history), "contiguous_sequence": contiguous,
            "has_launch_event": has_launch, "has_runtime_snapshot": has_snapshot,
            "definition_known": known_def}
