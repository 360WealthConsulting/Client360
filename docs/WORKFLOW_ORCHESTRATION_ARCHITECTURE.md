# Workflow Orchestration Architecture (Phase D.33)

The **Enterprise Workflow Orchestration Engine** (`app/services/orchestration/`) centralizes workflow
**orchestration** — the coordination of multi-stage processes — behind one declarative, deterministic
engine. It **consumes the D.28 `RuntimeContext`** for behavior and **consumes the D.32 Runtime Policy
Engine** for routing; it never evaluates runtime configuration directly (the runtime engine stays the
sole evaluator) and never makes a business decision itself (the policy engine stays the sole decision
engine). It coordinates existing services and never duplicates domain behavior.

## Decision / coordination architecture (layers, unidirectional)

```
   RBAC (require_capability / record_in_scope)         ← access authority (unchanged; never bypassed)
        │
   Workflow Orchestration Engine (app/services/orchestration)   ← process COORDINATION (this phase)
        │  consumes (routing)            consumes (behavior)
   Runtime Policy Engine (D.32)  ──────  Runtime consumption / RuntimeContext (D.30/D.28)
        │  the sole DECISION engine        │  delegates
        │                              Runtime Configuration Engine (D.28)  ← the sole EVALUATOR
        │  coordinates (composition, never duplication)
   Existing domain services (workflow-template engine, automation, compliance, operations, …)
```

Nothing in the runtime or policy engines imports the orchestration layer (verified by the import
manifest). A definition composes runtime evaluations (via policy) + existing services — it adds no
second decision engine and no second evaluator.

## Components

| File | Responsibility |
|---|---|
| `state.py` | The deterministic state manager — the seven canonical states + pure transition resolution over a definition's stage/transition graph. |
| `context.py` | `WorkflowContext` — the immutable per-run context binding the definition, subject, inputs, and the runtime snapshot. |
| `definitions.py` | The executable declarative catalog (built from the shared pure-data seed). |
| `engine.py` | Launch / transition an instance; consumes the policy engine for routing, records events + the runtime snapshot, publishes major lifecycle events. |
| `execution.py` | The high-level coordinators for the `active` definitions (compose existing services around an executor; behavior-preserving). |
| `registry.py` | Discovery / versioning / lifecycle / ownership / dependency graph / coverage. |
| `governance.py` | Read-only validation of the registry + definitions → a governance report. |
| `diagnostics.py` | Execution history, current stage, execution graph, pending actions, blocked stages, policy decisions, runtime snapshot, replay readiness. |
| `replay.py` | Deterministic replay from the event ledger (pure read; never mutates state). |
| `simulation.py` | Dry-run execution, transition validation, policy verification, dependency analysis (pure read). |

## The canonical state model

`pending → active → (waiting ↔ active) → completed | cancelled | failed → compensated`. Every
orchestration instance's `status` is one of the seven canonical states; a definition's stages map to
these kinds. Transition resolution is a pure function: for any `(stage, action)` a definition permits
at most one target.

## Persistence

- `orchestration_definitions` — the discoverable registry (category, version, status, owner, the
  stage/transition graph, policy/runtime references, dependency graph).
- `orchestration_instances` — running instances (deterministic state, current stage, the bound runtime
  snapshot, the client anchor).
- `orchestration_events` — the append-only ledger (one row per lifecycle event) enabling deterministic
  replay (records the transition, the policy decision, the runtime snapshot).

## Active vs in-domain

- **active** (2) — the engine drives the workflow; its call sites are coordinated through it:
  `automation.dispatch` (automation execution), `workflow.review` (review-workflow launch).
- **in_domain** (13) — the mature domain lifecycles (the workflow-template engine, compliance approval
  + reviewer authority, operations project/task, advisor work, scheduling meeting, tax return,
  exception, campaign, document, communications delivery, the frozen notification dispatcher) — the
  lifecycle stays authoritative in the owning domain (regulatory / certified / deterministic),
  registered + governed but never re-implemented (documented exceptions, mirroring D.32 in-domain
  policies, excluded from the migratable denominator).

## Integrations

- **Policy** — a transition may declare a `policy` code; the engine evaluates it via the policy engine
  and records the decision. `workflow.review` routes through `workflow.review_routing`.
- **Automation** — `automation.dispatch.execute_dispatch` is orchestrated through the engine (the
  handler runs once; result/exception unchanged). The automation framework is not replaced.
- **Scheduler** — one gated `orchestration-tick` housekeeping job (dark-launched off). The scheduler
  infrastructure is unchanged.
- **Timeline** — only major lifecycle events (launched / stage completed / approval granted / cancelled
  / compensated / completed / failed) publish, and only for client-anchored instances.
- **Analytics / Observability** — in-process counters (launches, completions, failures, retries,
  replays, simulations, avg execution ms) + governance issue count / coverage; routine transition
  evaluations are never logged individually.

## What is NOT in the orchestration domain (excluded)

The runtime engine, the policy engine, runtime governance, runtime coordination, RBAC, authentication,
authorization, infrastructure configuration, and the scheduler infrastructure remain authoritative and
unchanged. D.33 centralizes workflow *orchestration*, not workflow *business rules* (those stay in the
policy engine) and not workflow *evaluation* (that stays in the runtime engine).
