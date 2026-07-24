# Enterprise Reporting & Executive Intelligence (Phase D.48)

The Executive Reporting layer provides firm-wide operational visibility by composing the platform's
authoritative operational services and the **single Analytics Registry** into named executive dashboards. It
is a governed, **read-only composition** — **not** another analytics engine, data warehouse, BI platform,
reporting database, ETL layer, or metrics system — and it never mutates. See
[`ADR-053`](adr/ADR-053-executive-reporting.md).

## Where it lives
`app/services/executive_intelligence/` — `registry.py`, `model.py`, `service.py`, `widgets.py`, `gate.py`,
`stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`. Routes: `app/routes/executive_intelligence.py`.

## Composition, not duplication
| What | Authoritative owner | How the layer uses it |
| --- | --- | --- |
| Every KPI value | **Analytics Registry** (`analytics.metrics`, the single metrics registry) | `compute_metric(principal, key)` — inherits record scope + the `analytics.executive` gate |
| AUM trend | Analytics trends | `metric_trend` |
| Firm intelligence | Analytics firm intelligence | `firm_intelligence(principal)` |
| Advisor workload | Unified Work Queue | `work_queue_summary` |
| Workflow status/aging | Workflow automation | `workflow_metrics` |
| Review cadence | Portfolio | `accounts_due_for_review` |
| Opportunity pipeline | Opportunity | `pipeline_report` |
| Communication activity | Communications | `metrics` |
| Operational health | Operational Intelligence (D.46) | `workspace_recommendations` |
| Runtime health | Runtime + Observability | `adoption_stats` + health metrics |

The layer defines **no new metrics** — it registers only four low-cardinality operational counters (about
itself) into the ONE Analytics Registry. Every business KPI comes from `compute_metric`.

## Executive gating (inherited, no bypass)
`compute_metric` enforces `analytics.executive` server-side: an executive (firm revenue/AUM) metric returns
`restricted` (value withheld) for a non-executive. The dashboard registry additionally declares each
dashboard's `required_capabilities` — `analytics.executive` for the executive/revenue dashboards,
`analytics.view` for the operational ones. A principal lacking a dashboard's capability gets `None` (404);
executive widgets on a mixed dashboard self-restrict. Values are never leaked.

## Explainability
Every widget carries its explanation (what it shows + where it comes from), authoritative owner, source, and
a deep link to drill into the authoritative surface. Every dashboard carries a generated timestamp, source
inventory, governing services, explainable widgets, and deep links. A non-explainable widget is never
emitted.

## Runtime & policy governance
Gated through the Runtime Engine (`reporting.enabled` + `executive_dashboard.enabled` +
`executive_widgets.enabled`; no env fallback) AND the Policy Engine, alongside the RBAC capability checks.

## Rationale: no second BI platform
A second BI platform / warehouse would need ETL, copied operational data, a parallel metrics catalog, and its
own access model — duplicating governed, gated infrastructure with reconciliation + drift risk and no
benefit the composition doesn't provide. Composing over the single Analytics Registry keeps one source of
truth for every KPI and zero copied data. See [`REPORTING_GOVERNANCE.md`](REPORTING_GOVERNANCE.md).

## Integration
Advisor Workspace gains an **Executive Insights** panel; Client 360 + Household 360 gain an executive-only
**Executive** section; AI Assist **summarizes** executive KPI values (executive-only, never invents a
metric). The client portal is excluded (no executive dashboards, no operational metrics). See
[`EXECUTIVE_DASHBOARDS.md`](EXECUTIVE_DASHBOARDS.md), [`DASHBOARD_REGISTRY.md`](DASHBOARD_REGISTRY.md).

## References
`app/services/executive_intelligence/*`, `app/routes/executive_intelligence.py`,
`docs/platform_architecture_manifest.yaml`, `tests/test_executive_reporting.py`, ADR-053.
