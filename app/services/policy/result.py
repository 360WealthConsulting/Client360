"""The policy result model (Phase D.32).

Every policy evaluation returns one immutable :class:`PolicyResult`. It carries the decision plus a
full, deterministic explanation of how it was reached: the policy identifier, the runtime snapshot the
decision was evaluated against (so the decision is reproducible), the runtime features and capabilities
that were evaluated, and the evaluation timestamp. No caller implements custom decision logic — they
read ``.decision`` (or truth-test the result) and may surface ``.explanation``.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PolicyResult:
    decision: object                       # the business decision (bool | value)
    explanation: str                       # human-readable, deterministic "why"
    policy_id: str                         # the policy code
    runtime_snapshot_id: int | None        # the runtime snapshot the decision was evaluated against
    evaluated_features: tuple = ()         # ((feature_code, value), …) consulted via the runtime engine
    evaluated_capabilities: tuple = ()     # (capability_code, …) the decision references (RBAC stays authority)
    evaluated_at: str | None = None        # ISO-8601 timestamp
    cached: bool = False                   # served from the in-process deterministic policy cache
    dependencies: tuple = field(default_factory=tuple)  # composed policy codes

    def __bool__(self) -> bool:
        return bool(self.decision)

    def to_dict(self) -> dict:
        return {"decision": self.decision, "explanation": self.explanation, "policy_id": self.policy_id,
                "runtime_snapshot_id": self.runtime_snapshot_id,
                "evaluated_features": [list(f) for f in self.evaluated_features],
                "evaluated_capabilities": list(self.evaluated_capabilities),
                "evaluated_at": self.evaluated_at, "cached": self.cached,
                "dependencies": list(self.dependencies)}
