# Workflow Authoring Guide (Phase D.33)

How to add or change a workflow orchestration definition. A definition has two halves that governance
keeps in sync: the **pure-data seed** (`app/database/orchestration_seed.py`, mirrored into the registry
by an Alembic migration) and the **executable definition** (built from the seed in
`app/services/orchestration/definitions.py`).

## 1. Decide active vs in-domain

- **active** — the orchestration engine drives the process. Use this when D.33 owns the coordination
  (e.g. it wraps an existing service via a coordinator). Its call sites are rewired through the engine.
- **in_domain** — the lifecycle stays authoritative in the owning domain (a mature/certified/regulatory
  domain state machine). Register it for discovery + governance, but the engine never drives it.

Do **not** put business decisions in a definition — routing consumes the **Runtime Policy Engine** (a
transition's `policy`); behavior consumes **`RuntimeContext`** (`runtime_refs`). The definition
coordinates; it never evaluates configuration or decides business rules.

## 2. Add the seed entry (`app/database/orchestration_seed.py`)

Append a dict (or use the `_lifecycle(...)` helper for a standard canonical lifecycle). Keep the graph
**well-formed** (see `docs/WORKFLOW_GOVERNANCE_GUIDE.md`): every stage reachable from `initial_stage`,
every transition's `from`/`to` declared, every non-terminal stage able to reach a terminal outcome, a
declared+reachable completion stage, a present `owner`, and every `policy`/`runtime_refs` reference
resolvable. Model the lifecycle at the canonical level (the seven states) — for an in-domain machine,
represent the lifecycle abstractly; the full domain map stays authoritative in the domain.

## 3. Seed the registry row (a new Alembic migration)

Add a migration that inserts the new definition into `orchestration_definitions` (mirror
`za0b1c2d3e4f`). Keep a **single Alembic head**. Governance reconciles the registry row against the
in-code definition — an orphan row or an unreachable definition is flagged.

## 4. Wire an active definition's coordinator

For an `active` definition, add a coordinator to `app/services/orchestration/execution.py` (or reuse
`coordinate(...)`) that launches an instance and advances it through the stages, invoking the caller's
executor at the execution stage. Then rewire the call site to go through the engine, **keeping the
capability check** at the call site (RBAC is never bypassed):

```python
from app.services.orchestration import execution as orchestration
return orchestration.coordinate("myarea.process", subject=key,
                                executor=lambda: existing_service_call(...))
```

Behavior-preserving rule: the executor must run exactly once and its return/exception must be
unchanged; all orchestration recording is failure-isolated.

## 5. Validate + test

Run `governance.validate()` — it must report `ok: True` (no unreachable stages / orphan transitions /
traps / duplicate ids / missing references / invalid ownership or completion paths). Add tests: a
`simulation.dry_run` of the happy path, a `replay.replay` of a coordinated instance (deterministic),
the governance state, and the behavior-preserving call-site result. Bump the route-count guard only if
you added routes; keep the manifest + platform architecture in sync.

## Deprecating a definition

`registry.deprecate(code, reason=…)` then, once no call site coordinates it, `registry.retire(code)`.
Governance flags any active definition that still depends on a deprecated/retired definition.
