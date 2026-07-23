"""Workflow replay (Phase D.33) — deterministic reconstruction of an orchestration run.

Deterministically replays a recorded orchestration instance from its append-only event ledger, the
runtime snapshot the run was bound to, and the recorded policy decisions — reproducing the exact stage
sequence and outcome WITHOUT re-invoking any domain service and WITHOUT modifying production state (it
never launches, transitions, or writes anything; it recomputes the trajectory in memory and checks it
against the definition's state machine). Replay is a pure read.
"""
from __future__ import annotations

from . import diagnostics, state
from .common import OrchestrationError
from .definitions import get_definition


def replay(instance_id) -> dict:
    """Replay an instance from its recorded events. Returns the reconstructed stage trajectory, the
    recorded policy decisions applied at each routed step, the final stage/status, and whether the
    replay is deterministic (every recorded transition is a legal transition of the definition and the
    reconstructed final stage matches the persisted one). Never mutates production state."""
    from . import engine
    engine.note_replay()
    history = diagnostics.execution_history(instance_id)
    inst = diagnostics._instance(instance_id)
    if inst is None:
        raise OrchestrationError(f"orchestration instance {instance_id} not found")
    d = get_definition(inst["definition_code"])
    if d is None:
        raise OrchestrationError(f"unknown definition {inst['definition_code']!r}")

    trajectory = []
    cur = None
    deterministic = True
    applied_decisions = []
    for e in history:
        if e["event_type"] == "launched":
            cur = e["to_stage"] or d.initial_stage
            trajectory.append(cur)
            continue
        if e["event_type"] == "transition_blocked":
            applied_decisions.append({"seq": e["seq"], "action": e["action"], "blocked": True,
                                      "decision": e.get("policy_decision")})
            continue
        action = e.get("action")
        expected = state.next_stage(d, cur, action) if action else None
        if e.get("policy_decision") is not None:
            applied_decisions.append({"seq": e["seq"], "action": action,
                                      "decision": e["policy_decision"]})
        # a recorded transition must be a legal transition of the state machine from the current stage
        if expected is None or expected != e.get("to_stage"):
            deterministic = False
        cur = e.get("to_stage") or cur
        trajectory.append(cur)

    final_matches = cur == inst.get("current_stage")
    return {"instance_id": instance_id, "definition_code": inst["definition_code"],
            "runtime_snapshot_id": inst.get("runtime_snapshot_id"), "trajectory": trajectory,
            "final_stage": cur, "persisted_stage": inst.get("current_stage"),
            "final_matches_persisted": final_matches, "applied_policy_decisions": applied_decisions,
            "deterministic": deterministic and final_matches, "event_count": len(history),
            "modified_production_state": False}
