"""Enterprise Analytics (Phase D.15).

A deterministic READ-MODEL and operational-intelligence layer. Analytics owns NO business data
and is never a source of truth: it computes KPIs by composing existing principal-scoped domain
reports and running bounded, scope-filtered COUNT/SUM aggregates. It persists only
analytics-specific config (targets/thresholds, dashboards/widgets) and prospective snapshots.
Not AI; not wired into the D.5 Advisor Intelligence seam (ADR-020).
"""
