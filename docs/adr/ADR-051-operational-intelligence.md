# ADR-051 — Enterprise Operational Intelligence and Explainable Recommendation Layer: Deterministic Composition, Not Opaque ML

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Advisor Experience); Reliability / Operations; Security / Authorization
(RBAC + record scope); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.46 audit found the platform already has a complete, deterministic, propose-only
recommendation engine and several domain-owned observation sets:

* **`advisor_intelligence.py`** (D.5A–D.5D) — a deterministic `Signal` engine (`get_client_signals`,
  `get_household_signals`, `get_dashboard_signals`, `group_signals`) with full explainability
  (`Explainability`: why / source_service / evidence / deterministic confidence / policy_gate), a route
  (deep link), and immutable `RecommendationMeta`. Producers compose authoritative reads (review overdue,
  open exceptions, overdue tasks, upcoming meetings, portfolio/insurance/beneficiary opportunities, annual
  review recommendations). No ML, no writes, no persistence.
* **Domain observation sets** — `opportunity.intelligence.pipeline_intelligence`,
  `bizdev.intelligence.business_development_intelligence`, `analytics.intelligence.firm_intelligence` — each
  emits `{observations: [{id, kind, title, summary, priority}], …}` with fixed thresholds, deliberately kept
  OUT of the advisor_intelligence producer seam (ADR-018/019/020).
* **Unified work queue** (`work_queue_summary`), **engagement summary** (D.44), **knowledge summary** (D.45).

There is **no Operational Intelligence / recommendation-composition layer** that unifies these into one
governed, prioritized, deduplicated, explainable advisor surface — that is the gap D.46 fills. Introducing a
second recommendation engine, an ML/predictive model, or a black-box score would violate the platform's "no
second system" and "deterministic, explainable" invariants and duplicate a working engine.

## Decision
Phase D.46 adds a **governed, read-only Operational Intelligence composition layer**
(`app/services/recommendations/`) that normalizes the existing authoritative recommendation sources into one
explainable `Recommendation` surface, with NO new store and NO ML:

1. A declarative **recommendation registry** (`registry.py`) — the authoritative catalog of every
   recommendation type (owner service, source services, default severity, category, lifecycle,
   prerequisites, visibility, explanation template, supporting-evidence kind, deep-link target, workflow
   owner, suppression rules) + a deterministic classifier mapping advisor_intelligence Signals onto types.
2. A normalized **Recommendation model** (`model.py`) — id, type, priority, severity, explanation, governing
   rule, evidence, authoritative source, workflow owner, deterministic confidence, generated timestamp, deep
   link, recommended next action. `is_explainable` (why + evidence + deep link) is a hard emit gate.
3. Read-only, fail-closed **adapters** (`adapters/`): `signals` (normalizes advisor_intelligence Signals),
   `observations` (normalizes the pipeline/bizdev/firm observation sets + the work-queue workload rollup),
   `composed` (one thin communication-followup rule over the D.44 engagement summary). None re-derives
   domain logic; none mutates.
4. A **recommendation engine** (`service.py`): `client_recommendations`, `household_recommendations`
   (aggregated + deduplicated + household-prioritized), `workspace_recommendations` (the Operational
   Intelligence panel), `recommendation_summary`, `explain_recommendation`. Explainability enforced
   (non-explainable dropped); dedup + suppression + prioritization; returns `None` out of scope.
5. **Runtime gates** (`recommendations.enabled` + workspace/household/ai flags), **policy composition**
   (`policy.evaluate("recommendations.*")` alongside RBAC — never bypassing either), low-cardinality
   **analytics** (4 metrics), internal **diagnostics** (`observability.audit`), and a read-only
   **governance** checker (forbids ML deps, tables, writes, outbox, audit-writes, duplicate ownership).

No migration, no new table, no new capability (reuses `client.read` + `observability.audit`), no new outbox
contract. Single Alembic head stays `m4p5o6r7t8c9`.

## Alternatives considered
- **A second recommendation engine / rules DSL.** Rejected: `advisor_intelligence` is the deterministic
  recommendation engine; D.46 composes it. Duplicating producers would fork the D.5 golden regression.
- **Machine-learning / predictive scoring.** Rejected: opaque, non-explainable, non-deterministic, and a new
  infrastructure dependency; governance forbids ML imports. Confidence stays deterministic and rule-based
  (1.0 for operational; source-supplied otherwise).
- **Persisting recommendations.** Rejected: recommendations are a deterministic function of the authoritative
  data at read time; a store would be a second operational database to reconcile. The audit justified no new
  persistence.
- **Autonomous action.** Rejected: recommendations never mutate; every one deep-links to the authoritative
  workflow where a human acts.

## Reasons for the decision
Advisors and compliance must be able to answer "why was this recommendation made?" for every item. A
deterministic composition over authoritative facts gives a verifiable answer — the governing rule, the
authoritative source, and the exact evidence — with a stable, reproducible result. An ML model would trade
that explainability + reproducibility for opacity, add training/serving infrastructure, and create a
compliance burden (model risk, drift, bias) with no offsetting benefit for a rule-expressible domain. The
platform's existing D.5 engine already proved the deterministic, propose-only pattern; D.46 extends it.

## Consequences

### Positive consequences
- One governed, explainable, prioritized recommendation surface with no second engine, no ML, no new store.
- Every recommendation is explainable (why + governing rule + authoritative source + evidence) and
  deep-links to the owning workflow; non-explainable items are never emitted.
- Zero schema change: no migration, table, capability, or outbox contract.
- Advisor Workspace panel + Client 360 / Household 360 sections + AI summarize-only, all from one layer.

### Negative consequences and tradeoffs
- Recommendations are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- The layer inherits its coverage from the authoritative producers; a genuinely new recommendation kind must
  be added to its authoritative owner (per ADR-018/019/020) and then surfaces here automatically.

## Enforcement
`tests/test_operational_intelligence.py` (registry completeness + single ownership; deterministic
classification; generation from authoritative signals; every recommendation explainable + deep-linked;
deterministic confidence; non-explainable dropped; explanation endpoint; suppression; dedup; household
aggregation; scope → None; runtime + policy gates; workspace panel; Client 360 / Household 360 integration;
AI summarize-only; analytics; diagnostics; governance; architecture invariants — no ML / no Table / no
mutation / no second engine). `app/services/recommendations/governance.py` enforces the invariants at
runtime. Route count, section registry, and migration head are guarded by `tests/test_platform_architecture.py`
+ `tests/test_client360_workspace.py` + `docs/platform_architecture_manifest.yaml`.

## Exceptions
The `composed` adapter adds one thin communication-followup recommendation over the D.44 engagement summary —
the single genuinely-new composed rule (all other recommendations are normalizations of existing
authoritative signals/observations). It composes an authoritative scoped read and mutates nothing.

## Revisit conditions
Revisit when a recommendation kind is requested that no authoritative producer emits (add it to its owning
domain first), when recommendation persistence is genuinely justified (e.g. an acknowledgement/audit trail),
or if a deterministic scoring refinement is needed (kept explainable and reproducible — never ML).

## References
- `app/services/recommendations/*` (`registry.py`, `model.py`, `service.py`, `gate.py`, `stats.py`,
  `metrics.py`, `diagnostics.py`, `governance.py`, `adapters/signals.py`, `adapters/observations.py`,
  `adapters/composed.py`)
- `app/routes/recommendations.py`; workspace panel in `app/services/workspace/service.py`; Client 360
  section in `app/services/client360/{registry,sections}.py`; Household 360 section in
  `app/services/client360/household.py`; AI grounding in `app/services/ai_assist/context.py`; analytics in
  `app/services/analytics/{sources,metrics}.py`
- Reuses `app/services/advisor_intelligence.py`, `app/services/opportunity/intelligence.py`,
  `app/services/bizdev/intelligence.py`, `app/services/analytics/intelligence.py`,
  `app/services/work_queue/summary.py`, the D.44 engagement summary
- `docs/OPERATIONAL_INTELLIGENCE.md`, `docs/RECOMMENDATION_ENGINE.md`, `docs/RECOMMENDATION_REGISTRY.md`,
  `docs/RECOMMENDATION_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_operational_intelligence.py`; relates to ADR-004, ADR-005, ADR-018, ADR-019, ADR-020, ADR-028,
  ADR-030, ADR-039, ADR-044 through ADR-050
