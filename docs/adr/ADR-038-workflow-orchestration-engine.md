# ADR-038 — Enterprise Workflow Orchestration Engine: declarative, deterministic, policy-driven process coordination that consumes RuntimeContext + the Policy Engine

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Workflow/Orchestration); Reliability / Operations (governance);
Security / Authorization (RBAC ownership); Business Operations Owner (Michael Shelton — process
requirements). Authorized compliance reviewer: Not yet designated.

## Context
A repository audit (D.33) found workflow orchestration scattered across the platform: the canonical
workflow-template engine (`workflow_automation.py`, active/paused/cancelled/completed + steps +
approvals + SLA + triggers), the automation framework's own run lifecycle (pending/running/succeeded/
dead + retry), and **eleven independent domain state machines** (operations projects/tasks, scheduling
meetings, compliance reviews + reviewer-authority, advisor work, tax-return production + filing,
exceptions, campaigns, documents, communications delivery) — each re-implementing its own transition
map (in three incompatible key shapes) and its own timeline-publish glue. There was no single place to
discover / version / govern / replay / simulate a process, and no shared deterministic state model.

The risk in centralizing is introducing a second decision engine, bypassing `RuntimeContext` or the
policy engine, replacing the mature/certified domain lifecycles, or duplicating domain behavior.

## Decision
Phase D.33 introduces an **Enterprise Workflow Orchestration Engine** (`app/services/orchestration/`) —
a centralized, declarative, deterministic coordination layer. **The runtime engine remains the sole
evaluator; the Runtime Policy Engine remains the sole business-decision engine; D.29 coordination
remains the sole synchronization mechanism; RBAC remains the sole access authority.**

- **Deterministic state management** — a canonical seven-state model (pending / active / waiting /
  completed / cancelled / failed / compensated) with pure transition resolution over a definition's
  stage/transition graph. No module maintains its own orchestration lifecycle independently — every
  engine transition resolves through the state manager.
- **Declarative workflow definitions** — stages, transitions, entry/exit actions, completion /
  cancellation rules, timeout, retry policy, compensation hooks, ownership, versioning — declared as
  data (shared pure-data seed so the registry rows and executable definitions cannot drift).
- **Consumes `RuntimeContext` + the Policy Engine** — workflow **routing** consumes the policy engine
  (a transition may declare a `policy` code the engine evaluates and records); workflow **behavior**
  consumes `RuntimeContext`. The engine never evaluates runtime configuration directly and never makes
  a business decision itself.
- **Coordinates, never duplicates** — the execution service runs an `active` definition by composing
  existing services around a caller-supplied executor; the mature domain lifecycles remain
  authoritative and are registered `in_domain` (a documented exception, mirroring D.32 in-domain
  policies) — governed but never re-implemented.
- **Workflow registry** — discovery, ownership, lifecycle status, versions, dependency graph,
  categories, deprecation tracking (`orchestration_definitions`).
- **Deterministic replay + simulation** (both pure reads that never mutate production state) — replay
  reconstructs an instance's trajectory from the append-only event ledger + the runtime snapshot + the
  recorded policy decisions; simulation dry-runs an action sequence, validates transitions, verifies
  routing policies, and analyzes dependencies.
- **Workflow governance** — unreachable stages, orphan transitions, unproductive circular transitions,
  duplicate ids, missing policy references, missing runtime dependencies, invalid ownership, invalid
  completion paths → a governance report.
- **Behavior-preserving integration** — automation execution is orchestrated through the engine (the
  `automation.dispatch` definition wraps the existing dispatch handler; the handler runs once and its
  result/exception is unchanged); the review-workflow launch is orchestrated through the engine (the
  `workflow.review` definition routes via `workflow.review_routing` then composes `launch_workflow`).
  The automation framework and the workflow-template engine are not replaced. The scheduler gains one
  gated housekeeping tick (dark-launched off) — the scheduler infrastructure is unchanged.
- **Never bypasses RBAC/scope/audit** — the `/orchestration` surface reuses the existing D.17
  `workflow.*` capabilities (no new capabilities, no RBAC changes); every lifecycle event records to
  the shared audit hash-chain; only major events publish to the client timeline.

## Alternatives considered
1. **Rewrite the domain state machines onto the engine.** Rejected: they are mature, tested, and some
   (compliance approval) are regulatory. They are registered `in_domain` — governed, not replaced.
2. **Replace the workflow-template engine (`workflow_automation.py`).** Rejected: it is the canonical
   execution engine; D.33 coordinates above it and registers it `in_domain`.
3. **Replace the automation framework.** Rejected: D.33 orchestrates automation execution by composing
   the existing dispatch (behavior-preserving), never replacing it.
4. **A durable BPMN/DSL workflow product.** Rejected: Client360 is deterministic and non-AI; a bounded
   declarative stage/transition model consuming the runtime + policy engines is simpler and testable.
5. **Snapshot the domain maps into the registry with full cyclic fidelity.** Rejected: the
   orchestration definitions model each lifecycle at the canonical, well-formed (acyclic-toward-a-
   terminal) level so governance can meaningfully detect unreachable stages / traps / bad completion
   paths; the domain map itself stays authoritative in-domain.

## Reasons for the decision
Processes must be discoverable, versioned, governed, replayable, simulable, and free of duplicated
orchestration logic — without a second decision engine, without bypassing the runtime or policy engines,
and without changing behavior or replacing mature/regulatory lifecycles. A declarative, deterministic
engine that consumes `RuntimeContext` + the policy engine and coordinates existing services delivers
this while preserving ADR-004/005/016/033/036/037.

## Consequences
### Positive consequences
- 15 workflow definitions (2 `active` + 13 `in_domain`) cover the ten orchestration domains (100%
  coverage; 100% adoption of the migratable set; 0 governance issues). Automation execution and the
  review-workflow launch are coordinated through the engine (behavior unchanged). Deterministic replay
  + dry-run simulation are available for every definition/instance; both are pure reads.
- The decision/coordination architecture is layered and unidirectional: RBAC → Orchestration → Policy →
  Runtime consumption → Runtime engine → metadata; nothing in the runtime or policy engines imports the
  orchestration layer.

### Negative consequences and tradeoffs
- Thirteen domain lifecycles remain `in_domain` (authoritative in their owning domain) — registered and
  governed but not centralized, by necessity (mature/certified/regulatory/deterministic).
- Every automation dispatch now writes an orchestration instance + events (additive rows); recording is
  failure-isolated so it never affects the dispatch outcome.
- The scheduler tick is a housekeeping hook (the active definitions complete synchronously today), not a
  durable async runner.

## Enforcement
- `app/services/orchestration/{engine,execution,state,context,definitions,registry,governance,
  diagnostics,replay,simulation,common}.py`; the pure-data seed `app/database/orchestration_seed.py`;
  migration `migrations/versions/za0b1c2d3e4f_orchestration.py` (creates + seeds
  `orchestration_definitions` / `orchestration_instances` / `orchestration_events`); schema
  `app/database/orchestration_tables.py` registered in `schema.py`; `db.py` exposes the tables. Rewired
  call sites: `app/services/automation/dispatch.py` (execute_dispatch coordinated through the engine)
  and `app/services/advisor_workspace.py` (review launch orchestrated). Scheduler: `app/config.py`
  (`orchestration_enabled`/interval) + `app/jobs/scheduler.py` (gated `orchestration-tick`). Routes
  `app/routes/orchestration.py` (`/orchestration`, reusing `workflow.*`). Analytics metrics
  (`sources.py`/`metrics.py`). Orchestration modules registered in `source_producer_modules`. The
  runtime engine, the policy engine, D.29 coordination, RBAC, the workflow-template engine, the
  automation framework, the mature domain lifecycles, and infrastructure config are untouched. Tests:
  `tests/test_orchestration.py`; manifest / platform-architecture / route-count / ADR-count guards
  updated.

## Exceptions
The thirteen `in_domain` definitions are an approved, documented policy: the lifecycle stays
authoritative in the owning domain (regulatory approval / the certified workflow-template engine /
deterministic domain state machines). `administrator`/`record.read_all` scope bypass remains as defined
by ADR-004.

## Revisit conditions
Adding a durable async workflow runner, replacing a domain state machine with an engine-driven
definition (only if its constraint is lifted), persisting governance findings, or introducing a
workflow DSL would each warrant a new or superseding ADR.

## References
- `app/services/orchestration/*`, `app/routes/orchestration.py`, `app/database/orchestration_tables.py`,
  `app/database/orchestration_seed.py`, migration
  `migrations/versions/za0b1c2d3e4f_orchestration.py`, `docs/WORKFLOW_ORCHESTRATION_ARCHITECTURE.md`,
  `docs/WORKFLOW_DEFINITION_SPECIFICATION.md`, `docs/WORKFLOW_GOVERNANCE_GUIDE.md`,
  `docs/WORKFLOW_AUTHORING_GUIDE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_orchestration.py`; relates to ADR-004, ADR-005, ADR-016, ADR-033, ADR-036, ADR-037
