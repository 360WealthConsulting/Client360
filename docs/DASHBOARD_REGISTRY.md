# Dashboard & Widget Registry (Phase D.48)

`app/services/executive_intelligence/registry.py` holds the two declarative catalogs of the
executive-intelligence layer — the authoritative catalog of executive dashboards and widgets. See
[`ADR-053`](adr/ADR-053-executive-reporting.md).

## Dashboard registry
`DASHBOARD_REGISTRY` — each `DashboardDef` declares:
- `key`; `owner` (this layer); `audience` (executive | operations | advisor | compliance | client_service);
- `runtime_gate` (the governed flag guarding the dashboard);
- `widgets` (the tuple of widget keys it composes);
- `required_capabilities` (the RBAC caps that may open it — `analytics.executive` for executive/revenue,
  `analytics.view` for operational);
- `navigation` (deep-link destination); `refresh_policy`; `governing_services` (the authoritative services
  composed); `lifecycle`.

## Widget registry
`WIDGET_REGISTRY` — each `WidgetDef` declares:
- `key`; `owner` (authoritative owning service); `source` (the authoritative read the value comes from —
  e.g. `analytics.metrics:aum`, `work_queue.summary`, `workflow_automation.workflow_metrics`);
- `aggregation` (count | sum | rollup | trend | health | distribution); `unit`; `viz`;
- `permission` (the capability required — `analytics.executive` for firm revenue/AUM);
- `deep_link` (the authoritative surface to drill into); `explainability`; `refresh`; `lifecycle`.

### Registered widgets (14)
`firm_aum`, `aum_trend`, `revenue_kpi`, `client_growth` (KPIs via the Analytics Registry);
`advisor_workload`, `workflow_status`, `workflow_aging`, `compliance_workload`, `review_cadence`,
`opportunity_pipeline`, `communication_activity`, `tax_workload`, `operational_health`, `runtime_health`
(firm reads). Three are executive-gated (`firm_aum`, `aum_trend`, `revenue_kpi`).

## Single metrics registry — no second registry
Every KPI widget's `source` points at `analytics.metrics:<key>`, and its value is computed by
`analytics.metrics.compute_metric` — the ONE metrics registry. This layer defines no `Metric`/`_DEFS`; it
only registers four low-cardinality operational counters (about itself) into that same registry. Governance
asserts no second metrics registry and that `compute_metric` is the KPI path.

## Onboarding a dashboard / widget
Add a `DashboardDef` / `WidgetDef` (via the `_d(...)` / `_w(...)` helper) with its owner, source,
capability, deep link, and explainability; for a new widget add its compute function to `widgets._COMPUTE`.
A new KPI is added to the Analytics Registry first, then referenced as `analytics.metrics:<key>`. Governance
verifies completeness + single ownership (no duplicate keys) and that every dashboard widget is registered.

## References
`app/services/executive_intelligence/registry.py`, `app/services/executive_intelligence/widgets.py`,
`app/services/executive_intelligence/governance.py`, `app/services/analytics/metrics.py`,
`tests/test_executive_reporting.py`, ADR-053.
