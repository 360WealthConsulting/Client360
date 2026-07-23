# Runtime Policy Architecture (Phase D.32)

The **Runtime Policy Engine** (`app/services/policy/`) centralizes application **business decisions**
— eligibility, routing, gating, visibility — behind one declarative, governed, explainable layer. It
sits above the runtime stack and **consumes the D.28 `RuntimeContext`**; it never evaluates
configuration itself. The runtime engine remains the sole evaluator, D.29 coordination the sole
synchronization mechanism, D.27 the sole metadata owner, and RBAC the sole access authority.

## Decision architecture (layers, unidirectional)

```
   RBAC (require_capability / record_in_scope)         ← access authority (unchanged; never bypassed)
        │
   Runtime Policy Engine  (app/services/policy)        ← the business DECISION (this phase)
        │  consumes
   Runtime Consumption API (app/services/runtime/consumption)
        │  delegates
   Runtime Configuration Engine (D.28)                 ← the sole EVALUATOR (features/config/snapshots)
        │  reads
   Configuration metadata (D.27)                       ← the sole metadata OWNER
```

The policy engine imports the consumption API; **nothing in the runtime engine imports the policy
layer** (verified by the import-direction manifest). A policy composes runtime evaluations +
capabilities-as-information + inputs into a decision — it adds no second evaluator and no second
metadata store.

## Components

| File | Responsibility |
|---|---|
| `result.py` | `PolicyResult` — the immutable result model (decision, explanation, policy id, runtime snapshot id, evaluated features/capabilities, timestamp). |
| `definitions.py` | The declarative policy catalog: each `PolicyDefinition` binds a code to a deterministic, data-driven decision function (a runtime feature/config lookup with a behavior-preserving legacy default). |
| `engine.py` | `evaluate(code, *, context, subject, default)` — deterministic execution, policy composition (dependencies), snapshot-scoped caching, in-process counters. Never raises. |
| `registry.py` | Policy discovery / versioning / lifecycle / ownership / category / dependency graph / deprecation, backed by `runtime_policies`. |
| `governance.py` | Read-only validation of the registry + runtime definitions → a governance report. |

## The result model

Every `policy.evaluate(...)` returns a `PolicyResult`:

- `decision` — the business decision (bool or value)
- `explanation` — deterministic, human-readable "why"
- `policy_id` — the policy code
- `runtime_snapshot_id` — the snapshot the decision was evaluated against (reproducibility)
- `evaluated_features` — `((feature_code, value), …)` consulted via the runtime engine
- `evaluated_capabilities` — the capability codes the decision references (RBAC stays the authority)
- `evaluated_at` — ISO-8601 timestamp
- `cached` / `dependencies` — cache provenance + composed policies

No caller implements custom decision logic — it reads `.decision` (or truth-tests the result).

## Runtime integration

Every evaluation **consumes `RuntimeContext`** (supplied by the caller for request/loop reuse, or built
from the cached snapshot), **respects runtime authority** (the seeded D.31 definitions drive the
decisions), **respects runtime governance**, and **reuses runtime snapshots** (the decision cache is
keyed on the snapshot id + version, so it is free of duplicate evaluation and never masks a live
change — see below).

**Caching that respects runtime authority.** A decision is cached only against an immutable, identified
snapshot: a configuration change yields a new snapshot version → a new cache key. When there is no
snapshot (unhydrated / test), the engine resolves fresh every call — exactly as the runtime engine
does — so a runtime change is reflected immediately.

## Policy composition

A policy may declare `depends_on` other policies. For a boolean decision the engine ANDs the
dependency decisions (and records + merges their evaluations); for a value decision the dependencies
are informational. Example: `advisor_workspace.section.tasks` composes `…section.work` (tasks show only
within the work section); `microsoft365.sharepoint_scope` composes `…sync_eligibility`.

## The ten decision areas

Workflow routing · advisor-workspace visibility · operations visibility · reporting eligibility ·
automation execution · Microsoft integration behavior · notification routing · compliance decisions ·
document behavior · scheduling behavior. Nine policies are **active** (evaluated by the engine — their
call sites are rewired through it); four are **in-domain** (registered + governed, enforcement stays in
the owning domain by documented constraint). See `docs/POLICY_REGISTRY.md`.

## What is NOT in the policy domain (excluded)

Infrastructure configuration, authentication, **authorization enforcement** (RBAC/scope stays at the
call site), cryptography, database connectivity, startup lifecycle, the runtime engine, and runtime
coordination. The policy engine centralizes *business decision logic* only, leaving runtime evaluation,
configuration, and authorization unchanged.
