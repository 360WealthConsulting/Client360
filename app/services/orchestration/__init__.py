"""Enterprise Workflow Orchestration Engine (Phase D.33) — centralized process coordination.

D.33 centralizes workflow ORCHESTRATION (the coordination of multi-stage processes) behind a single
declarative engine so no workflow implements orchestration independently. The engine **consumes the
D.28 ``RuntimeContext``** for behavior and **consumes the D.32 Runtime Policy Engine** for routing — it
never evaluates runtime configuration directly (the runtime engine remains the sole evaluator) and
never makes business decisions itself (the policy engine remains the sole decision engine). It
coordinates existing services and never duplicates domain behavior; the mature domain lifecycles remain
authoritative and are registered ``in_domain``. It never bypasses RBAC/scope/audit.

Public surface:
- ``orchestration.engine`` — launch / transition an instance (deterministic state management).
- ``orchestration.execution`` — the high-level coordinators for the active definitions.
- ``orchestration.registry`` — discovery / versioning / lifecycle / dependency graph.
- ``orchestration.governance`` — validate the registry + definitions.
- ``orchestration.diagnostics`` / ``.replay`` / ``.simulation`` — inspection, deterministic replay,
  dry-run simulation (all read-only; never mutate production state).
"""
from __future__ import annotations

from . import diagnostics, engine, execution, governance, registry, replay, simulation  # noqa: F401
from .context import WorkflowContext  # noqa: F401

__all__ = ["WorkflowContext", "diagnostics", "engine", "execution", "governance", "registry",
           "replay", "simulation"]
