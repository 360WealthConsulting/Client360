# ADR-053 — Enterprise Reporting & Executive Intelligence: A Read-Only Dashboard Composition, Not a Second BI/Analytics Platform

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Firm Leadership / Executive); Reliability / Operations; Security /
Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.48 audit found the platform already has two mature reporting/analytics layers:

* **Analytics Registry** (`app/services/analytics/`, D.15) — the SINGLE metrics registry: a frozen `Metric`
  catalog (`_DEFS` / `METRICS`, ~142 metrics), `compute_metric(principal, key)` (book-scoped via
  `accessible_person_ids`, with the `executive` flag gated by `analytics.executive` + a runtime feature,
  enforced server-side so every consumer inherits the gate), `list_metrics`, predefined executive scorecards
  (`analytics/dashboards.py PREDEFINED` — `firm`, `revenue`, `executive_summary`), and firm-level
  `firm_intelligence(principal)`.
* **Reporting facade** (`app/services/reporting/`, D.21) — the report/dashboard metadata + composition layer
  over Analytics ("consumes the Analytics read-model for every KPI value… never recalculates KPIs, never a
  source of truth"), with the only export producer (`analytics.service.export_metrics`).

There was **no executive-dashboard registry** unifying these + the D.46 Operational Intelligence and D.47
Compliance Intelligence dashboards into named, firm-wide executive views. Building a second analytics engine,
a data warehouse, an ETL layer, a reporting database, or a second metrics registry would violate the "no
second system" invariant and duplicate governed, gated infrastructure.

## Decision
Phase D.48 adds a **governed, read-only executive-dashboard composition layer**
(`app/services/executive_intelligence/`) that provides firm-wide operational visibility with NO new metrics,
NO persistence, and NO mutation:

1. Two declarative **registries** (`registry.py`): `DASHBOARD_REGISTRY` (8 dashboards — owner, audience,
   runtime gate, widget list, required capabilities, navigation, refresh policy, governing services) and
   `WIDGET_REGISTRY` (14 widgets — owner, source, aggregation, refresh, permissions, deep link,
   explainability).
2. Normalized read-models (`model.py`): `WidgetResult` + `Dashboard`, each explainable (explanation + source
   + deep link, a hard emit gate) and reference-only.
3. A **widget compute layer** (`widgets.py`): each widget's value is computed on read by its authoritative
   service — KPI widgets flow through the SINGLE Analytics Registry (`compute_metric`, inheriting the
   `analytics.executive` gate + record scope); firm-level widgets read the authoritative firm reads (work
   queue, workflow, portfolio, opportunity, communications, runtime, Operational Intelligence). Fail-closed.
4. The **executive intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`,
   `executive_summary`, `get_widget`. Every dashboard carries generated timestamp, source inventory,
   governing services, explainable widgets, and deep links. Dashboard-level authorization (a principal must
   hold one of the dashboard's required capabilities; executive dashboards need `analytics.executive`);
   executive widgets self-restrict for non-executives (value withheld, never leaked).
5. **Runtime gates** (`reporting.enabled` + `executive_dashboard.enabled` + `executive_widgets.enabled`),
   **policy composition**, **analytics reuse** (four operational counters registered into the ONE Analytics
   Registry — no second registry), internal **diagnostics** (`observability.audit`), and a read-only
   **governance** checker.

No migration, no new table, no new capability (reuses `analytics.view` + `analytics.executive`), no new
metric, no new outbox contract. Single Alembic head stays `n5s6u7p8v9w0`.

## Alternatives considered
- **A second analytics engine / BI platform / data warehouse / ETL.** Rejected: the Analytics Registry +
  Reporting facade are the authoritative analytics/reporting layers; D.48 composes them. Governance forbids a
  second metrics registry, tables, and copied operational data.
- **A second metrics registry.** Rejected: every KPI value flows through `compute_metric` (the one registry);
  the layer registers only operational counters (about itself) into that same registry — the house style.
- **Persisting composed dashboards.** Rejected: dashboards are a deterministic function of the authoritative
  data at read time; a store would be a reporting warehouse to reconcile. The audit justified no persistence.

## Reasons for the decision
Firm leadership needs one executive view; the authoritative services + the Analytics Registry already
provide every value with the correct scoping and executive gating. A read-only composition gives that view
with full explainability (source + deep link) and inherits the `analytics.executive` gate for free, while
every metric stays owned by the Analytics Registry and every operational fact by its domain service. Deep
links (never inline mutation) route the executive to the authoritative surface.

## Rationale for avoiding a second BI/reporting platform
A second BI platform / warehouse would require ETL, copied operational data, a parallel metrics catalog, and
its own access model — duplicating governed, gated infrastructure and creating reconciliation + drift risk,
with no benefit the composition does not already provide. Composing over the single Analytics Registry keeps
one source of truth for every KPI, one place executive gating is enforced, and zero copied data.

## Consequences

### Positive consequences
- One firm-wide executive-dashboard surface with no second analytics platform, no warehouse, and no
  duplicated metrics.
- Executive gating is inherited from `compute_metric` — non-executives see restricted widgets, never values.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Executive Insights panel + Client 360 / Household 360 Executive sections + AI
  summarize-only, all from one layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- The layer's coverage is bounded by the Analytics Registry + the firm reads; a genuinely new KPI is added to
  the Analytics Registry first, then surfaces here.

## Enforcement
`tests/test_executive_reporting.py` (registries + single ownership; explainable dashboard composition;
dashboard-level authorization — executive dashboards → 404 for non-executives, restricted widgets never leak
values; runtime + policy gates; Client 360 / Household 360 executive-only sections; Advisor Workspace panel;
AI summarize-only; analytics reuse — the 4 counters in the ONE registry; diagnostics; governance; and the
architecture invariants — no Table / no `_DEFS` / no mutation / no audit write / values via `compute_metric`).
`app/services/executive_intelligence/governance.py` enforces the invariants at runtime. Route count, section
registry, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate (portfolio firm metrics, workflow metrics, runtime adoption,
observability health) are exposed only within dashboards whose required capability the principal holds; the
executive KPI widgets additionally self-restrict via `compute_metric`.

## Revisit conditions
Revisit when a new firm KPI is required (add it to the Analytics Registry), when scheduled/exported executive
reports are needed (compose over the existing Reporting facade + `export_metrics`), or if a materialized
executive read-model is ever justified (it would be a governed projection, never a second warehouse).

## References
- `app/services/executive_intelligence/*` (`registry.py`, `model.py`, `service.py`, `widgets.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`)
- `app/routes/executive_intelligence.py`; Client 360 section in
  `app/services/client360/{registry,sections}.py`; Household 360 section in
  `app/services/client360/household.py`; Executive Insights panel in `app/services/workspace/service.py`; AI
  grounding in `app/services/ai_assist/context.py`; analytics counters in
  `app/services/analytics/{sources,metrics}.py`
- Reuses `app/services/analytics/{metrics,dashboards,intelligence,trends}.py`, `app/services/reporting/*`,
  `app/services/work_queue/summary.py`, `app/services/workflow_automation.py`, `app/services/portfolio.py`,
  `app/services/opportunity/reporting.py`, `app/services/communications/service.py`,
  `app/services/runtime/consumption.py`, the D.46 recommendations layer
- `docs/EXECUTIVE_REPORTING.md`, `docs/EXECUTIVE_DASHBOARDS.md`, `docs/DASHBOARD_REGISTRY.md`,
  `docs/REPORTING_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_executive_reporting.py`; relates to ADR-015, ADR-020, ADR-021, ADR-025, ADR-028, ADR-030,
  ADR-046 through ADR-052
