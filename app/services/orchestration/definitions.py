"""Declarative workflow definitions (Phase D.33) — the centralized orchestration catalog.

Builds the executable ``OrchestrationDefinition`` objects from the shared pure-data seed
(``app/database/orchestration_seed.py``) — the same data the migration seeds into the registry, so the
executable definitions and the registry rows cannot drift. Definitions are **data-driven**: stages,
transitions, entry/exit actions, completion rules, timeout/retry/compensation, ownership and versioning
are all declared as data. Routing is delegated (a transition may declare a ``policy`` code consumed
from the Runtime Policy Engine); runtime behavior is delegated (``runtime_refs`` consumed via
``RuntimeContext``). No orchestration logic is embedded in a caller — the engine drives every
``active`` definition.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.database.orchestration_seed import ORCHESTRATION_DEFINITIONS_SEED, ORCHESTRATION_DOMAINS


@dataclass(frozen=True)
class OrchestrationDefinition:
    code: str
    category: str
    name: str
    owner: str
    version: int
    status: str                       # active | in_domain | deprecated | retired
    initial_stage: str
    stages: tuple
    transitions: tuple
    completion_stages: tuple
    policy_refs: tuple = ()
    runtime_refs: tuple = ()
    depends_on: tuple = ()
    timeout_seconds: int | None = None
    retry_policy: dict = field(default_factory=dict)
    compensation: dict = field(default_factory=dict)
    description: str = ""

    @property
    def stage_names(self) -> tuple:
        return tuple(s["name"] for s in self.stages)

    @property
    def transition_policies(self) -> tuple:
        return tuple(t["policy"] for t in self.transitions if t.get("policy"))


def _build(d: dict) -> OrchestrationDefinition:
    return OrchestrationDefinition(
        code=d["code"], category=d["category"], name=d["name"], owner=d.get("owner"),
        version=d.get("version", 1), status=d.get("status", "active"),
        initial_stage=d["initial_stage"], stages=tuple(d["stages"]), transitions=tuple(d["transitions"]),
        completion_stages=tuple(d.get("completion_stages") or ()), policy_refs=tuple(d.get("policy_refs") or ()),
        runtime_refs=tuple(d.get("runtime_refs") or ()), depends_on=tuple(d.get("depends_on") or ()),
        timeout_seconds=d.get("timeout_seconds"), retry_policy=dict(d.get("retry_policy") or {}),
        compensation=dict(d.get("compensation") or {}), description=d.get("description", ""))


ORCHESTRATION_DEFINITIONS: dict[str, OrchestrationDefinition] = {
    d["code"]: _build(d) for d in ORCHESTRATION_DEFINITIONS_SEED}

DOMAINS = tuple(ORCHESTRATION_DOMAINS)


def get_definition(code: str) -> OrchestrationDefinition | None:
    return ORCHESTRATION_DEFINITIONS.get(code)


def active_definitions() -> list[OrchestrationDefinition]:
    return [d for d in ORCHESTRATION_DEFINITIONS.values() if d.status == "active"]
