# ADR-026 — Enterprise Reporting as a composition layer over Analytics

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Reporting); Analytics Domain Owner (read-model boundary);
Business Operations Owner (Michael Shelton — executive decision-support requirements). Authorized
compliance reviewer: Not yet designated (reporting audit history is regulated recordkeeping).

## Context
The firm needed enterprise reporting / dashboards / BI / executive decision support, but the
authoritative KPI read-model already exists: **Analytics (D.15, ADR-020)** computes every metric
on read (`metrics.compute_metric`/`compute_many`, 28 metrics with executive gating), composes
scoped domain reports, exposes predefined scorecards (`dashboards.compose_predefined`), snapshots
(`analytics_snapshots` via `service.capture_snapshot`), trends, and targets. There was **no**
report-definition / dashboard-definition / saved-view / report-schedule / export-profile store, and
**no** binary exporter (only `service.export_metrics`, a JSON dict). Analytics already owns a minimal
metric-key dashboard builder (`analytics_dashboards`/`analytics_dashboard_widgets`) but it is
analytics-native config, not a cross-domain reporting platform. ADR-020 forbids storing KPI values
as truth and re-implementing business logic outside Analytics.

## Decision
Enterprise Reporting is a **composition layer** (like Annual Review / Business Owner Planning), not
a source domain. It **owns only reporting metadata and is never a source of truth**.
- **Owns (definitions/config only):** `report_templates`, `report_definitions`, `reports` (run
  records), `reporting_dashboards`, `reporting_widgets`, `reporting_scorecards`,
  `reporting_kpi_groups`, `reporting_saved_views`, `reporting_export_profiles`, `report_schedules`,
  and `reporting_events` (an **append-only** polymorphic audit ledger). A definition row says
  "render metrics [a, b] for audience X with viz Y" — it stores **none** of those numbers.
- **Consumes Analytics for every KPI value.** Rendering calls `analytics.metrics.compute_metric` /
  `compute_many`, `analytics.dashboards.compose_predefined`, `analytics.trends.metric_trend`, and
  `analytics.service.export_metrics`. Reporting **never recalculates a KPI and never re-queries a
  source table.** Because it composes `compute_metric`, **executive gating (`analytics.executive`)
  and record scope are inherited automatically** — never re-implemented. Widgets reference
  Analytics by `metric_key` (string), not FK.
- **Reuses `analytics_snapshots`** for point-in-time capture (via `capture_snapshot`) — Reporting
  persists no KPI truth of its own. **Export is metadata only**: export profiles select a format /
  delivery and delegate value production to `export_metrics`; **no binary generator is
  implemented** (there is none in the platform to reuse yet).
- **References, never owns:** Workflow (a `run_report_schedule` action lets a workflow schedule
  reports; schedules may carry a `workflow_instance_id`), Communications (`conversation_id` for
  delivery metadata), Microsoft 365 (an export delivery target), and every consumed domain.
- **Timeline:** approved lifecycle events (`reporting_report_created`,
  `reporting_scheduled_report_generated`) publish to the shared timeline **only** for client-anchored
  report runs (the timeline requires a person/household anchor per ADR-009); firm-level dashboards
  and reports record to the `reporting_events` ledger. **Not** every report execution emits.
- **Security:** the `reporting.view/manage/templates/audit*/admin*` capability family (`*` =
  sensitive) gates a new `/reporting` surface (in-route; matches no middleware RULE, like
  `/analytics`). Record scope is always enforced — KPI values are scoped by the Analytics compute
  layer, report runs enforce their optional client anchor, and saved views are owner-scoped.
- **Composition registration:** `reporting` is added to `composition_layer_modules` (so no
  source-producer may import it), `app/services/reporting/service.py` to `composition_service_modules`,
  and a `composition_layers.reporting` block declares `is_source_of_truth: false`.

## Alternatives considered
1. **Extend `analytics_dashboards`/`analytics_dashboard_widgets`.** Rejected: those are
   analytics-native metric-key config; reporting composes multiple domains + layouts + schedules +
   exports and would bloat the read-model and blur ownership. Left untouched.
2. **Make Reporting a source domain that persists KPI values.** Rejected: violates ADR-020 (values
   drift; single source of truth). KPI values are computed-on-read from Analytics.
3. **Implement a binary (PDF/Excel/PowerPoint) exporter now.** Rejected for this phase: no exporter
   exists to reuse; export is modeled as metadata (profiles) delegating to `export_metrics`. A real
   exporter is a future phase (and a new ADR if it introduces a rendering engine).
4. **Recompute KPIs inside Reporting for custom report math.** Rejected: duplicates business logic
   and creates a second source of truth (ADR-002/ADR-020). Custom math belongs in Analytics.

## Reasons for the decision
Analytics already single-sources KPI computation with executive gating and scope. The enterprise
value Reporting adds is **definitions, audiences, layouts, schedules, saved views, and export
profiles** — metadata that composes the read-model. A composition layer delivers that without a
second source of truth, inheriting gating/scope for free and preserving every ADR and the D.5
golden.

## Consequences
### Positive consequences
- One authoritative reporting-metadata layer (dashboards, definitions, scorecards, KPI groups,
  saved views, schedules, export profiles, report runs) composing the Analytics read-model.
- Executive gating and record scope are inherited from `compute_metric` — impossible to bypass.
- Analytics, its dashboard tables, and every source domain are untouched; no KPI value is persisted
  as truth (point-in-time reuses `analytics_snapshots`).

### Negative consequences and tradeoffs
- Two dashboard builders now exist: analytics-native (`analytics_dashboards`) and reporting-owned
  (`reporting_dashboards`) — a documented separation (analytics config vs. cross-domain reporting).
- Export is metadata only; generating an actual PDF/Excel/PowerPoint file is not implemented (no
  binary exporter exists to reuse).
- Report scheduling is metadata + on-demand `run_schedule`; there is no background report
  dispatcher (Workflow or the job scheduler may drive it in a future phase).
- Firm-level reporting lifecycle events do not appear on the client timeline (by design — the
  timeline is client-anchored); their history lives in `reporting_events`.

## Enforcement
- `app/database/reporting_tables.py::define_reporting_tables` (registered in
  `app/database/schema.py`; reflected in `app/db.py`). Migration `s9d0e1f2a3b4` (11 tables +
  append-only trigger on `reporting_events` + 5 `reporting.*` capabilities + 10 audience dashboards
  + 4 export profiles). Services `app/services/reporting/{common,render,service,templates,schedules}.py`
  (`render.py` is the sole KPI-composition seam — it imports Analytics, never source tables). Routes
  `app/routes/reporting.py` (in-route `reporting.*` gating; `/reporting` matches no middleware RULE).
  Workflow action `run_report_schedule` in `app/services/workflow_orchestration/actions.py`.
  Analytics, `analytics_dashboards`, and every source domain are untouched; no KPI value is
  persisted as truth. Composition registration in the manifest (`composition_layer_modules`,
  `composition_service_modules`, `composition_layers.reporting`) is enforced by
  `tests/test_platform_architecture.py`. Tests: `tests/test_reporting.py`.

## Exceptions
None currently approved.

## Revisit conditions
Implementing a binary export/rendering engine (PDF/Excel/PowerPoint), a background report
dispatcher, live scheduled delivery through Communications/Microsoft 365, or any KPI computation
inside Reporting would each warrant a new or superseding ADR.

## References
- `app/services/reporting/`, `app/routes/reporting.py`, `app/database/reporting_tables.py`,
  migration `migrations/versions/s9d0e1f2a3b4_reporting_platform.py`
- Consumed read-model: `app/services/analytics/` (ADR-020)
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_reporting.py`; relates to ADR-001, ADR-002, ADR-009, ADR-013, ADR-020
