"""The deterministic workflow state manager (Phase D.33).

A single, pure (DB-free) source of truth for the canonical orchestration instance states and the
transition-resolution rule over a declarative definition's stage/transition graph. Determinism: for any
``(stage, action)`` a definition permits at most one target stage, so transition resolution is a pure
function. No module maintains its own orchestration lifecycle independently — every engine transition
resolves through here.
"""
from __future__ import annotations

# The seven canonical instance states (mirrors app/database/orchestration_tables.py::INSTANCE_STATES).
INSTANCE_STATES: frozenset[str] = frozenset(
    {"pending", "active", "waiting", "completed", "cancelled", "failed", "compensated"})

TERMINAL_STATES: frozenset[str] = frozenset({"completed", "cancelled", "compensated"})
FAILURE_STATES: frozenset[str] = frozenset({"failed"})


def stage_kind(definition, stage_name: str) -> str | None:
    """The canonical instance state a definition stage maps to."""
    for s in definition.stages:
        if s["name"] == stage_name:
            return s.get("kind")
    return None


def transition_for(definition, from_stage: str, action: str) -> dict | None:
    """The (single) transition a definition permits for ``(from_stage, action)``, or None."""
    for t in definition.transitions:
        if t["from"] == from_stage and t["action"] == action:
            return t
    return None


def actions_from(definition, from_stage: str) -> list[str]:
    return [t["action"] for t in definition.transitions if t["from"] == from_stage]


def next_stage(definition, from_stage: str, action: str) -> str | None:
    t = transition_for(definition, from_stage, action)
    return t["to"] if t else None


def is_valid_transition(definition, from_stage: str, action: str) -> bool:
    return transition_for(definition, from_stage, action) is not None


def is_terminal_stage(definition, stage_name: str) -> bool:
    """A stage is terminal if it is flagged terminal, is a terminal canonical state, or is a sink (no
    outgoing transitions) — e.g. a ``failed`` stage with no recovery edge is a terminal outcome."""
    for s in definition.stages:
        if s["name"] == stage_name:
            if bool(s.get("terminal")) or s.get("kind") in TERMINAL_STATES:
                return True
            return not any(t["from"] == stage_name for t in definition.transitions)
    return False


def can_reach_terminal(definition, from_stage: str) -> bool:
    """Whether any terminal outcome (completed/cancelled/compensated, or a sink) is reachable."""
    seen, frontier = set(), [from_stage]
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if is_terminal_stage(definition, cur):
            return True
        for t in definition.transitions:
            if t["from"] == cur:
                frontier.append(t["to"])
    return False


def reachable_stages(definition) -> set[str]:
    """Every stage reachable from the initial stage via the transition graph."""
    seen, frontier = set(), [definition.initial_stage]
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for t in definition.transitions:
            if t["from"] == cur:
                frontier.append(t["to"])
    return seen


def can_reach_completion(definition, from_stage: str) -> bool:
    """Whether any completion stage is reachable from ``from_stage``."""
    completion = set(definition.completion_stages or ())
    seen, frontier = set(), [from_stage]
    while frontier:
        cur = frontier.pop()
        if cur in completion:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        for t in definition.transitions:
            if t["from"] == cur:
                frontier.append(t["to"])
    return bool(completion & seen)
