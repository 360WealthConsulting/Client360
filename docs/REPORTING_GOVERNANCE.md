# Executive Reporting Governance (Phase D.48)

`app/services/executive_intelligence/governance.py` is a read-only checker that verifies the
executive-intelligence layer stays a **composition** over the authoritative operational services + the
SINGLE Analytics Registry and never becomes a second analytics engine, data warehouse, BI platform,
reporting database, or metrics system. It returns `{ok, issue_count, findings}` and never raises into normal
use. See [`ADR-053`](adr/ADR-053-executive-reporting.md).

## Invariants enforced
1. **No second store / no reporting warehouse.** No module defines a table (`Table(` / `define_*_tables`) or
   writes the DB (`insert`/`update`/`delete`) — no ETL, no copied operational data.
2. **No second metrics registry.** No module defines a `Metric` catalog / `_DEFS`. Widget KPIs flow through
   `analytics.metrics.compute_metric` (the one registry).
3. **No second event bus / audit.** No module publishes to the outbox or writes audit events.
4. **No direct projection reads.** No module reads `rm_*` tables directly.
5. **Composes the authoritative reads.** The engine reuses `analytics.metrics`, work queue, workflow,
   portfolio, opportunity, communications, runtime, and Operational Intelligence.
6. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is present in the model
   AND applied in the widget compute layer — a non-explainable widget is never emitted.
7. **Registry completeness + single ownership.** Every dashboard + widget is fully declared; every dashboard
   widget is registered; every widget names an authoritative owner + source; no duplicate keys.
8. **Governed gating.** Every gate is a runtime flag in the `GATES` registry; no raw environment fallback.

The checker excludes `governance.py` from its own source scan (it holds the detection string-literals).

## Additional guarantees proven by tests
- **No duplicated metrics** — the four registered readers are operational counters about the layer itself,
  not business metrics; all KPI values come from `compute_metric`.
- **No mutation, no policy bypass, no runtime bypass** — architecture-invariant + gate tests.
- **Every widget has an authoritative owner; every dashboard deep-links** to authoritative workflows.
- **Executive values never leak** — a non-executive gets restricted widgets + a non-leaking executive
  summary; executive dashboards return 404.

## How it runs
`validate_executive_reporting()` returns `{ok, issue_count, findings}`, surfaced through the internal
diagnostics (`app/services/executive_intelligence/diagnostics.py`) on the `observability.audit` surface
(`GET /executive/diagnostics`) and asserted clean by
`tests/test_executive_reporting.py::test_governance_clean`.

## Diagnostics & analytics reuse
`reporting_diagnostics()` composes gate snapshot + in-process counters (low-cardinality — no metric values,
client identifiers, or business data) + registry coverage + widget compute coverage + governance: dashboards
composed, widgets composed, aggregation failures, authorization failures, restricted widgets, widget
failures by key, average composition latency. Four low-cardinality metrics
(`executive_dashboards_composed`, `executive_widgets_composed`, `executive_widget_failures`,
`executive_authorization_failures`) are registered into the SINGLE Analytics Registry.

## Observability
Following the platform's established instrumentation pattern (no span/trace API), the layer instruments
dashboard composition, widget composition, registry lookups, runtime gates, and diagnostics with an
in-process counter module (`stats.py`). It never logs client-sensitive information or metric values.

## References
`app/services/executive_intelligence/governance.py`, `app/services/executive_intelligence/diagnostics.py`,
`app/services/executive_intelligence/stats.py`, `app/services/analytics/{sources,metrics}.py`,
`tests/test_executive_reporting.py`, ADR-053.
