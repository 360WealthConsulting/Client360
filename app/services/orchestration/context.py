"""Immutable workflow orchestration context (Phase D.33).

Every orchestration run is bound to one immutable ``WorkflowContext`` — the definition being run, the
subject, the launch inputs, and the D.28 ``RuntimeContext`` the run is evaluated against (so behavior +
routing are reproducible for replay). The engine consumes the runtime context through this object; it
never evaluates runtime configuration directly (the runtime engine remains the sole evaluator) and
never makes business decisions itself (the policy engine remains the sole decision engine — the context
only carries the runtime snapshot the decisions are made against).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkflowContext:
    definition_code: str
    subject: str | None = None
    runtime_snapshot_id: int | None = None
    inputs: dict = field(default_factory=dict)
    runtime: object = None          # the immutable D.28 RuntimeContext (consumed, never bypassed)
    person_id: int | None = None
    household_id: int | None = None
    actor_user_id: int | None = None

    @classmethod
    def build(cls, definition_code, *, subject=None, inputs=None, runtime=None, person_id=None,
              household_id=None, actor_user_id=None) -> WorkflowContext:
        """Build a context, resolving the runtime context from the runtime consumption API when not
        supplied (the runtime engine remains the sole evaluator)."""
        if runtime is None:
            from app.services.runtime import consumption
            runtime = consumption.runtime_context()
        return cls(definition_code=definition_code, subject=subject,
                   runtime_snapshot_id=getattr(runtime, "snapshot_id", None), inputs=dict(inputs or {}),
                   runtime=runtime, person_id=person_id, household_id=household_id,
                   actor_user_id=actor_user_id)

    def to_dict(self) -> dict:
        return {"definition_code": self.definition_code, "subject": self.subject,
                "runtime_snapshot_id": self.runtime_snapshot_id, "inputs": self.inputs,
                "person_id": self.person_id, "household_id": self.household_id}
