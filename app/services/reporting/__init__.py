"""Enterprise Reporting platform (Phase D.21) — a composition layer.

The top-of-stack Reporting / Business Intelligence / Executive Decision Support layer. It owns only
reporting METADATA — report templates and definitions, dashboards and widgets, scorecards, KPI
groups, saved views, schedules, export profiles, report-run records, and an append-only audit
ledger. It **consumes the Analytics read-model** for every KPI value (composing
``compute_metric`` / ``compose_predefined`` / ``export_metrics`` / trends at render time) and
**never recalculates KPIs, never owns business data, and is never a source of truth**. Point-in-time
capture reuses ``analytics_snapshots`` via the analytics service. It references Workflow (schedule
triggers), Communications (delivery metadata), and Microsoft 365 (document delivery) without owning
them. Approved lifecycle events publish to the shared Activity Timeline only for client-anchored
report runs. Executive gating and record scope are inherited automatically from the Analytics
compute layer.
"""
