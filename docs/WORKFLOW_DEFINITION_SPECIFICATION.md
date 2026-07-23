# Workflow Definition Specification (Phase D.33)

A workflow definition is a **declarative, data-driven** description of a multi-stage process. The
executable catalog is built from a shared pure-data seed (`app/database/orchestration_seed.py`) so the
registry rows (`orchestration_definitions`) and the executable definitions
(`app/services/orchestration/definitions.py`) cannot drift.

## Fields

| Field | Meaning |
|---|---|
| `code` | Unique definition identifier (e.g. `automation.dispatch`). |
| `category` | The orchestration domain (one of the ten). |
| `name` / `description` | Human-readable identity. |
| `owner` | The domain/team that owns the process. |
| `version` | Definition version (bumped on a semantic change). |
| `status` | `active` (engine-driven) · `in_domain` (authoritative in the owning domain) · `deprecated` · `retired`. |
| `initial_stage` | The stage a launched instance starts in. |
| `stages` | `[{name, kind, entry_actions, exit_actions, terminal}]` — `kind` is a canonical state. |
| `transitions` | `[{from, action, to, policy?}]` — deterministic; at most one target per `(from, action)`; an optional `policy` code is consumed from the Runtime Policy Engine to permit the route. |
| `completion_stages` | The terminal-success stages. |
| `policy_refs` | Policy codes the routing consumes (governed against the policy registry). |
| `runtime_refs` | Runtime feature/config keys the behavior consumes via `RuntimeContext`. |
| `depends_on` | Other definition codes (the dependency graph). |
| `timeout_seconds` / `retry_policy` / `compensation` | Timeout behavior, retry policy, compensation hooks. |

## The canonical states

`pending`, `active`, `waiting`, `completed`, `cancelled`, `failed`, `compensated`. Every stage maps to
one via its `kind`; the instance `status` is always one of the seven. Terminal outcomes are
`completed` / `cancelled` / `compensated`, a `failed` sink, or any stage with no outgoing transition.

## Well-formedness (enforced by governance)

- Every stage is reachable from `initial_stage`.
- Every transition's `from`/`to` is a declared stage (no orphan transitions).
- No unproductive cycle (every stage can reach a terminal outcome — no trap).
- `code` is unique (no duplicate workflow ids).
- Every `policy` / `policy_refs` code exists in the policy registry (no missing policy reference).
- Every `runtime_refs` key exists in the runtime metadata (per-instance bases like `automation.job`
  are exempt) — no missing runtime dependency.
- `owner` is present (valid ownership).
- `completion_stages` are declared, reachable, and non-empty (valid completion paths).

## Example (an active definition)

```
automation.dispatch  (category: automation, owner: automation, status: active)
  initial: pending
  stages: pending → dispatching(active) → running(active) → completed | failed → compensated
  transitions:
    pending    -dispatch->   dispatching
    dispatching-execute->    running
    running    -complete->   completed
    running    -fail->       failed
    failed     -compensate-> compensated
  completion: [completed]
  policy_refs: [automation.job_execution]   runtime_refs: [automation.job]
```

## Running a definition

- **Active** definitions are driven by `app/services/orchestration/execution.py` coordinators, which
  launch an instance, advance it through the stages via `engine.transition` (consuming the policy
  engine for policy-gated routes + `RuntimeContext` for behavior), invoke the caller's executor at the
  execution stage (composition), and record completion/failure/compensation. Behavior-preserving: the
  executor runs exactly once and its return/exception is unchanged.
- **In-domain** definitions are registered + governed but never driven by the engine (`engine.launch`
  refuses them) — the lifecycle stays authoritative in the owning domain.

See `docs/WORKFLOW_AUTHORING_GUIDE.md` to add one, and `docs/WORKFLOW_GOVERNANCE_GUIDE.md` for the
validation model.
