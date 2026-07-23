# ADR-037 — Runtime Policy Engine: centralized, declarative business decisions that consume RuntimeContext (the engine stays the sole evaluator); a governed policy registry; policies never bypass RBAC

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Runtime/Policy); Reliability / Operations (governance);
Security / Authorization (RBAC ownership); Business Operations Owner (Michael Shelton — decision
requirements). Authorized compliance reviewer: Not yet designated.

## Context
Through D.31 the runtime stack made configuration/feature **evaluation** authoritative (D.27 owns
metadata, D.28 evaluates it, D.29 coordinates it, D.30 consumes it, D.31 makes it authoritative +
governs it). But the **business decisions** themselves — eligibility, routing, gating, visibility
("should this job run?", "is this report included?", "is this section shown?", "which review template
may launch?") — remained scattered across the application: inline `consumption.feature_enabled(...)`
calls, hardcoded whitelists (`_REVIEW_TEMPLATES`, the operations timeline-event set), and per-service
state machines. A D.32 repository audit inventoried these across ten decision areas. Each caller
implemented its own decision shape, there was no single place to discover / version / govern the
decisions, and a decision carried no explanation or reproducibility metadata.

The risk in centralizing is introducing a second evaluator, bypassing RBAC, moving a regulatory or
frozen decision into a generic engine, or masking a live runtime change behind a decision cache.

## Decision
Phase D.32 introduces a **Runtime Policy Engine** (`app/services/policy/`) — a centralized, declarative
layer for business decisions. **The runtime engine remains the sole evaluator; D.29 coordination
remains the sole synchronization mechanism; D.27 remains the sole metadata owner; RBAC remains the sole
access authority.**

- **Consumes `RuntimeContext`, never bypasses it.** Policy decision functions delegate to the D.30
  consumption API (which delegates to the D.28 engine). The policy engine composes runtime evaluations,
  capabilities-as-information, and inputs into a business decision — it resolves no configuration itself.
- **Declarative, data-driven policies** (`definitions.py`) bind a code to a deterministic decision
  function (a runtime feature/config lookup with a behavior-preserving legacy default, or a bounded
  whitelist optionally overridable by a runtime feature).
- **A structured, explainable result model** (`PolicyResult`): decision, explanation, policy id,
  runtime snapshot id, evaluated features, evaluated capabilities, timestamp. No caller implements
  custom decision logic.
- **A policy registry** (`runtime_policies`): discovery, versioning, lifecycle status, ownership,
  category, dependency graph, deprecation tracking.
- **Policy governance**: duplicate / unreachable / orphan policies, circular dependencies, missing
  runtime definitions, deprecated references, invalid capability references → a governance report.
- **Behavior-preserving migration.** Nine `active` policies rewire their call sites (advisor-workspace
  sections, review-template routing, automation execution, reporting eligibility, Microsoft 365 sync +
  SharePoint scope, operations timeline-publish); each policy calls the same runtime consumption with
  the same legacy default, so behavior is identical.
- **Documented in-domain exceptions.** Four decision areas are registered + governed but not evaluated
  by the generic engine because enforcement must stay in the owning domain: **compliance decision
  routing** (regulatory approval must stay inside authorized Compliance — an architecture invariant),
  **notification routing** (the certified frozen F5.5 module + the F5.2 provider registry), **document
  behavior** and **scheduling behavior** (deterministic). They mirror D.31's compatibility-shim
  exceptions and are excluded from the migratable denominator.
- **Never bypasses RBAC/scope/audit.** Capability and record-scope enforcement stay at the call site; a
  policy only centralizes the business decision. The `/runtime/policy` surface reuses the existing
  `runtime.*` capabilities — no new capabilities, no RBAC changes.

## Alternatives considered
1. **A rules DSL / external rules engine.** Rejected: Client360 is deterministic and non-AI; a bounded
   set of declarative Python decision functions consuming the runtime engine is simpler, testable, and
   keeps the runtime engine the sole evaluator.
2. **Moving compliance approval into the generic engine.** Rejected: it would violate the architecture
   invariant that regulatory approval stays inside authorized Compliance. Registered `in_domain`.
3. **Modifying the frozen F5.5 notification module to route through the engine.** Rejected: it is a
   certified frozen module. Notification routing is registered `in_domain` for governance only.
4. **Snapshot-scoped decision caching.** Rejected as unsafe: runtime *features* are evaluated live per
   call (not bound to a snapshot version), so caching a decision against a snapshot id would mask a
   live feature change. The cache is instead scoped to a single immutable `RuntimeContext` object
   (per-request), so cross-call decisions always re-evaluate.

## Reasons for the decision
Business decisions must be discoverable, versioned, governed, explainable, and free of duplicated
logic — without introducing a second evaluator, bypassing RBAC, or changing behavior. A declarative
policy engine that consumes `RuntimeContext` and rewires the call sites behavior-preservingly delivers
this while preserving ADR-004/005/009/032/033/034/036.

## Consequences
### Positive consequences
- Ten decision areas are centralized behind one governed registry (100% area coverage, 100% adoption of
  the migratable set, 100% definition coverage, 0 governance issues). Nine call sites now read a policy
  decision instead of embedding logic; behavior is unchanged. The decision architecture is layered and
  unidirectional (RBAC → Policy → Runtime consumption → Runtime engine → metadata); nothing in the
  runtime engine imports the policy layer. Analytics/observability expose policy execution + governance.

### Negative consequences and tradeoffs
- Four decision areas remain **in-domain exceptions** (compliance approval, the frozen notification
  module, deterministic document & scheduling behavior) — registered and governed, but enforced in the
  owning domain by necessity, not centralized.
- The decision cache is per-context (per-request) only; non-request callers re-evaluate each call
  (correct, but no cross-call cache benefit) — an intentional trade for liveness of runtime authority.
- A mis-edited policy row (e.g. an invalid capability or a broken dependency) could misroute a decision;
  policy governance surfaces such issues, but governance is advisory (run on demand), not a hard gate.

## Enforcement
- `app/services/policy/{engine,definitions,registry,governance,result}.py`; migration
  `migrations/versions/z9b0c1d2e3f4_runtime_policy.py` (creates + seeds `runtime_policies`); schema
  `app/database/runtime_policy_tables.py` registered in `schema.py`; `db.py` exposes `runtime_policies`.
  Rewired call sites: `app/services/advisor_workspace.py` (section gating + review routing),
  `app/services/automation/dispatch.py`, `app/services/reporting/service.py`,
  `app/jobs/microsoft_{mail,calendar,document}_sync.py`, `app/services/operations/common.py`. Routes
  `app/routes/policy.py` (`/runtime/policy`, reusing `runtime.*`). Analytics metrics
  (`sources.py`/`metrics.py`). Policy modules registered in `source_producer_modules`. The runtime
  engine, D.29 coordination, RBAC, the certified-frozen notification module, compliance approval,
  infrastructure config, and the D.5 golden are untouched. Tests: `tests/test_runtime_policy.py`;
  manifest / platform-architecture / route-count / ADR-count guards updated.

## Exceptions
The four `in_domain` policies (compliance decision routing, notification routing, document behavior,
scheduling behavior) are an approved, documented policy: enforcement stays in the owning domain. The
two per-instance policies (`automation.job.<type>`, `reporting.module.<id>`) carry the D.31
compatibility shim into the consumption API. `administrator`/`record.read_all` scope bypass remains as
defined by ADR-004.

## Revisit conditions
Adding a rules DSL, persisting policy governance findings, centralizing a currently in-domain decision
(only if the regulatory/frozen/deterministic constraint is lifted), or evaluating decisions outside a
request context with a durable cache would each warrant a new or superseding ADR.

## References
- `app/services/policy/{engine,definitions,registry,governance,result}.py`, `app/routes/policy.py`,
  `app/database/runtime_policy_tables.py`, migration
  `migrations/versions/z9b0c1d2e3f4_runtime_policy.py`, `docs/RUNTIME_POLICY_ARCHITECTURE.md`,
  `docs/POLICY_REGISTRY.md`, `docs/POLICY_GOVERNANCE.md`, `docs/POLICY_AUTHORING_GUIDE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_runtime_policy.py`; relates to ADR-004, ADR-005, ADR-009, ADR-032, ADR-033, ADR-034,
  ADR-036
