# Recommendation Governance (Phase D.46)

`app/services/recommendations/governance.py` is a read-only checker that verifies the Operational
Intelligence layer stays a **composition** over the authoritative recommendation sources and never becomes a
second recommendation, workflow, opportunity, CRM, analytics, reporting, or AI engine. It returns
`{ok, issue_count, findings}` and never raises into normal use. See
[`ADR-051`](adr/ADR-051-operational-intelligence.md).

## Invariants enforced
1. **No ML / predictive / black-box scoring.** No module imports `sklearn`, `tensorflow`, `torch`,
   `xgboost`, uses `predict_proba`, or `numpy.random`. Confidence is deterministic (no probabilistic score).
2. **No second store / no writes.** No module defines a table (`Table(` / `define_*_tables`) or writes the
   DB (`insert`/`update`/`delete`).
3. **No second event bus / audit.** No module publishes to the outbox or writes audit events.
4. **No direct projection reads.** No module reads `rm_*` tables directly.
5. **Composes the authoritative sources.** The engine reuses `advisor_intelligence` + the pipeline/bizdev/
   firm observation sets + the work queue + the engagement summary — it does not re-implement a
   recommendation/workflow/opportunity engine.
6. **Explainability enforced.** `Recommendation.is_explainable` (why + evidence + deep link) is present in
   the model AND applied in the engine — non-explainable recommendations are never emitted.
7. **Registry completeness + single ownership.** Every type declares owner/source/explanation/evidence/
   deep-link/workflow-owner; no duplicate recommendation ownership.
8. **Governed gating.** Every gate is a runtime flag in the `GATES` registry; no raw environment fallback.

The checker excludes `governance.py` from its own source scan (it holds the detection string-literals).

## Additional guarantees proven by tests
- **No mutation** — every module is read-only.
- **No policy/runtime bypass** — client/household recommendations compose `policy.evaluate` alongside RBAC;
  an explicit deny is honored; gates are runtime-governed.
- **Every recommendation deep-links** to an authoritative surface and **carries supporting evidence**.
- **No AI-generated recommendations** — AI Assist only summarizes the recommendation contracts this layer
  emits.
- **No unsupported confidence values** — deterministic rule-based only.

## How it runs
`validate_recommendations()` returns `{ok, issue_count, findings}`, surfaced through the internal diagnostics
(`app/services/recommendations/diagnostics.py`) on the `observability.audit` surface (`GET
/recommendations/diagnostics`) and asserted clean by
`tests/test_operational_intelligence.py::test_governance_clean`.

## Diagnostics & analytics
`recommendation_diagnostics()` composes gate snapshot + in-process counters (low-cardinality — no client
identifiers or recommendation evidence) + registry coverage + adapter availability + governance:
recommendations generated, suppressed, stale, missing-evidence, rule failures, adapter failures by source,
counts by category/severity, and average composition latency. Four low-cardinality metrics
(`recommendations_generated`, `recommendations_suppressed`, `recommendation_compositions`,
`recommendation_adapter_failures`) are registered in the platform Analytics registry.

## Observability
Following the platform's established instrumentation pattern (no span/trace API), the layer instruments
recommendation composition, rule execution, suppression, registry lookups, and adapter failures with an
in-process counter module (`stats.py`) and surfaces them via Analytics + diagnostics. It never logs
recommendation evidence containing client-sensitive information.

## References
`app/services/recommendations/governance.py`, `app/services/recommendations/diagnostics.py`,
`app/services/recommendations/stats.py`, `app/services/analytics/{sources,metrics}.py`,
`tests/test_operational_intelligence.py`, ADR-051.
