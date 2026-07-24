# Executive Dashboards (Phase D.48)

The executive dashboards are named, firm-wide operational views composed read-only from the authoritative
services + the single Analytics Registry. See [`EXECUTIVE_REPORTING.md`](EXECUTIVE_REPORTING.md) and
[`ADR-053`](adr/ADR-053-executive-reporting.md).

## The dashboards (8)
| Dashboard | Audience | Capability | Widgets |
| --- | --- | --- | --- |
| `executive` | executive | `analytics.executive` | firm AUM, AUM trend, revenue, client growth, operational health, compliance workload, workflow status, communication activity |
| `operations` | operations | `analytics.view` | advisor workload, workflow status, workflow aging, operational health, runtime health |
| `advisor` | advisor | `analytics.view` | advisor workload, opportunity pipeline, review cadence |
| `compliance` | compliance | `analytics.view` | compliance workload, review cadence |
| `client_service` | client service | `analytics.view` | client growth, communication activity, review cadence |
| `revenue` | executive | `analytics.executive` | revenue, opportunity pipeline |
| `pipeline` | advisor | `analytics.view` | opportunity pipeline |
| `workflow` | operations | `analytics.view` | workflow status, workflow aging |

The `executive` dashboard is the firm-health rollup (firm health, operational health, advisor productivity,
compliance status, client service, workflow status, communications, runtime health).

## Composition & authorization
`compose_dashboard(principal, key)`:
1. Gate check (`reporting.enabled` + `executive_dashboard.enabled`).
2. Authorization — the principal must hold one of the dashboard's `required_capabilities`; else `None` (route
   → 404).
3. Policy composition (`policy.evaluate("reporting.dashboard")`).
4. Compose each registered widget (if `executive_widgets.enabled`) via the widget compute layer.
5. Returns `{enabled, dashboard: {key, name, audience, generated_at, widgets, governing_services,
   source_inventory, deep_links, navigation, refresh_policy, widget_count}}`.

`list_dashboards(principal)` returns only the dashboards the principal may open. `executive_summary(principal)`
composes the `executive` dashboard + `firm_intelligence` observations (a non-executive gets a non-leaking
restricted envelope). `get_widget(principal, key)` composes a single widget.

## Widget values & restriction
Each widget's value is computed on read by its authoritative service. KPI widgets flow through
`analytics.metrics.compute_metric` — executive metrics return `restricted: true, value: null` for a
non-executive (value never leaked). Firm-level widgets read the authoritative firm reads and are exposed only
within a dashboard whose capability the principal holds. Every widget is fail-closed (an unavailable source
yields `available: false`, never an exception).

## API
- `GET /executive` (HTML) — renders the requested dashboard (or the first the principal may see).
- `GET /api/v1/executive/dashboards` — the accessible dashboards.
- `GET /api/v1/executive/dashboard/{key}` — compose a dashboard (404 if unauthorized / unknown).
- `GET /api/v1/executive/summary` — the firm executive summary.
- `GET /api/v1/executive/widget/{key}` — compose one widget.
- `GET /api/v1/executive/registry` — the dashboard + widget catalogs.
- `GET /api/v1/executive/metrics` — low-cardinality layer metrics.
- `GET /executive/diagnostics` — internal diagnostics (`observability.audit`).

## References
`app/services/executive_intelligence/{service,widgets}.py`, `app/routes/executive_intelligence.py`,
`tests/test_executive_reporting.py`, ADR-053.
