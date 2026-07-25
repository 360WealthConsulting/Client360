# Automation Orchestration (Phase D.51)

The **Automation Orchestration** layer (`app/services/automation_orchestration/`) is a governed, **read-only
composition** that gives operations leadership one view of automation across the platform — inventory,
workflow status, trigger activity, execution status, pending automations, and failed automations — **without**
building a second workflow engine, scheduler, rules engine, orchestration engine, event bus, or automation
platform. Every number is composed on read from an **authoritative owner**; the layer owns no persistence and
never executes an automation, launches a workflow, fires a trigger, routes an event, or mutates anything.
**Panels carry counts + status only — never a workflow payload or client-sensitive automation data.**

## What it composes (and never duplicates)

| Concern | Authoritative owner (composed) |
| --- | --- |
| Workflow status / approvals / escalations | `app/services/workflow_automation.py` — `workflow_metrics()` |
| Workflow instances (record-scoped) | `app/services/workflow_orchestration/service.py` — `list_instances`, `metrics`, `templates` |
| Triggers | `app/services/workflow_orchestration/triggers.py` — `list_triggers` |
| Actions | `app/services/workflow_orchestration/actions.py` — `list_actions` |
| Scheduled jobs / runs | `app/services/automation/service.py` (ADR-027) — `metrics` |
| Event routing / outbox | `app/services/events/diagnostics.py` — `event_counts`, `events_by_domain` |
| Scheduling | `app/services/scheduling/service.py` — `metrics` |
| Notifications | `app/services/communications/service.py` — `metrics` |

See [AUTOMATION_REGISTRY.md](AUTOMATION_REGISTRY.md) for the automation + action catalogs,
[TRIGGER_REGISTRY.md](TRIGGER_REGISTRY.md) for the trigger types, and
[AUTOMATION_GOVERNANCE.md](AUTOMATION_GOVERNANCE.md) for the enforced invariants.

## Modules

- `registry.py` — the declarative catalogs: `AUTOMATION_REGISTRY` (9 automations), `TRIGGER_REGISTRY` (7
  trigger types), `ACTION_REGISTRY` (6 actions), `PANEL_REGISTRY` (17 panels), `ORCHESTRATION_DASHBOARDS` (6
  dashboards).
- `model.py` — `PanelResult` + `OrchestrationDashboard`. A panel is emitted only if `is_explainable`
  (explanation + source + deep link).
- `panels.py` — the per-panel compute functions. Read-only, fail-closed, **self-restricting** (a principal
  lacking `automation.view` gets a `restricted` panel, never its value). Counts + status only.
- `service.py` — the engine: `compose_dashboard`, `list_dashboards`, `get_panel`, `automation_summary`,
  `client_automation`, `household_automation`.
- `gate.py` — runtime gates (`automation.enabled`, `orchestration.enabled`, `triggers.enabled`) + policy
  composition. No raw environment gating.
- `stats.py` / `metrics.py` — low-cardinality in-process counters, registered into the **single** Analytics
  Registry (`analytics.metrics`). No second metrics registry; never workflow payloads.
- `diagnostics.py` — internal-only observability (`observability.audit`).
- `governance.py` — read-only invariant checker (never raises), including workflow/automation execution-call
  tells.

## Dashboards

`automation_inventory`, `workflow_automation`, `trigger_activity`, `execution_status`, `pending_automation`,
`failed_automation`. Each carries a generated timestamp, governing services, source inventory, explainable
panels, and deep links to the authoritative workflow/automation surface. Dashboards are gated by
`automation.view`; each panel additionally self-restricts to `automation.view`.

## Surfaces

- **HTTP** (`app/routes/automation_orchestration.py`, gated by `automation.view`; diagnostics by
  `observability.audit`): `/automation-orchestration` (HTML),
  `/api/v1/automation-orchestration/dashboards`, `/dashboard/{key}`, `/summary`, `/registry`, `/panel/{key}`,
  `/metrics`, `/automation-orchestration/diagnostics`.
- **Advisor Workspace** — the Automation Status panel (`automation_summary`).
- **Client 360 / Household 360** — the `automation_history` section (`client_automation` /
  `household_automation`, record-scoped workflow-facade rollups).
- **Executive Dashboard** — an `automation` dashboard (composed from existing D.48 widgets; no new widget),
  navigation deep-linking to `/automation-orchestration`.
- **AI Assist** — summarizes automation counts only; it never executes automations, approves workflows,
  triggers events, bypasses policies, or alters workflow state.

## Invariants

No new persistence, no new metric, no new capability, no migration (single Alembic head unchanged). No
mutation, no execution, no outbox publication, no audit write, no second engine. Every automation count comes
from an authoritative engine; every dashboard panel is explainable and deep-links to its authoritative
surface. Enforced by `app/services/automation_orchestration/governance.py` and
`tests/test_automation_orchestration.py`. See [ADR-056](adr/ADR-056-automation-orchestration.md).

## Related: Data Governance (D.52)

The D.52 Data Governance layer (`app/services/data_governance/`) provides the enterprise view of data
quality, lineage, stewardship, and ownership over the authoritative Governance package — a read-only
composition, never a second master-data platform or merge engine. Automation triggers/events surface there
via the Event registry's dependency-graph lineage panel. See [`DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md) and
ADR-057.

**Related (D.63):** the **Change Management** layer (`/change-management`) treats workflow definitions and
automation rules as change domains and references this layer's `automation_summary` as their read surface —
read-only, `automation.view`. It never creates a template / job, runs a workflow, or changes an automation
rule; Automation Orchestration remains the authoritative owner. Change management is an operational-readiness
view — **not** a deployment or production certification. See
[`ENTERPRISE_CHANGE_MANAGEMENT.md`](ENTERPRISE_CHANGE_MANAGEMENT.md) and
[`ADR-068`](adr/ADR-068-change-management.md).
