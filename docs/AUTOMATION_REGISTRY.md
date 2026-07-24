# Automation Registry (Phase D.51)

The **automation registry** (`AUTOMATION_REGISTRY` in `app/services/automation_orchestration/registry.py`) is
the declarative catalog of the firm's business automations and, for each, the **authoritative owners** it is
composed from. It is metadata only: the Automation Orchestration layer owns no automation state, executes
nothing, and never launches a workflow — it references the owners and explains the result with a deep link.
This document also covers the **action registry**.

## Automations

Each automation declares its `owner` (the business-process owner), `workflow_owner` (the authoritative
workflow-execution owner), `trigger_source` (a key into the [trigger registry](TRIGGER_REGISTRY.md)),
`execution_owner`, `scheduling_owner`, `notification_owner`, `runtime_gate`, and `deep_links`.

| Automation | Owner | Trigger source | Workflow owner |
| --- | --- | --- | --- |
| `annual_reviews` | annual_review | scheduled | workflow_automation |
| `onboarding` | workflow_automation | event | workflow_automation |
| `document_collection` | document_platform | document | workflow_automation |
| `client_communications` | communications | event | workflow_automation |
| `compliance_reminders` | compliance | compliance | workflow_automation |
| `licensing_reminders` | insurance_licensing | scheduled | workflow_automation |
| `tax_workflow` | tax_domain | workflow | workflow_automation |
| `insurance_workflow` | insurance | workflow | workflow_automation |
| `advisory_workflow` | advisor_work | event | workflow_automation |

Every automation's `execution_owner` is one of the authoritative engines (`workflow_orchestration` /
`workflow_automation` / `automation`); the layer executes none of them.

## Actions (`ACTION_REGISTRY`)

Each action declares its `authoritative_owner` (the owner of the effect), `execution_service` (the service
that performs it), `permissions` (the capabilities the authoritative execution requires), and `runtime_gate`.
The Automation Orchestration layer **never invokes** any of these — it only catalogs them for explainability.

| Action | Authoritative owner | Execution service | Permissions |
| --- | --- | --- | --- |
| `send_notification` | communications | communications.service | communications.manage |
| `create_work_item` | work_management | workflow_orchestration.actions | work.write |
| `request_document` | document_platform | workflow_orchestration.actions | documents.edit |
| `launch_workflow` | workflow_automation | workflow_automation.launch_workflow | workflow.execute |
| `generate_reminder` | automation | automation.runner | automation.execute |
| `escalate` | workflow_automation | workflow_automation.evaluate_sla | workflow.execute |

## Ownership boundaries (never re-implemented here)

- **Workflow execution** is owned by the Workflow Engine (`workflow_automation` + the
  `workflow_orchestration` facade). The registry names the workflow/execution owner; the layer **never calls**
  `launch_workflow` / `transition_workflow` / `request_approval` / `decide_approval` — governance forbids it.
- **Scheduled jobs / runs** are owned by the Automation engine (ADR-027). The registry names the scheduling
  owner; the layer never enqueues or runs a job.
- **Triggers + actions** are owned by `workflow_orchestration/triggers.py` + `actions.py`. The registry
  catalogs them; the layer never fires a trigger or executes an action.
- **Notifications** are owned by Communications; **scheduling** by the Scheduling domain.

## How the registry is used

The inventory + execution dashboards compose `registered_automations`, `workflow_templates`,
`automation_jobs`, `registered_actions`, `automation_runs`, `scheduling_activity`, and
`notification_activity`. Governance validates that every automation declares all eight fields, that every
automation's trigger source is a registered trigger type is not required (trigger sources map to trigger
categories), that every action declares owner + execution service + permissions + gate, that keys are
unique, and that the layer contains no workflow/automation **execution** call.

See [TRIGGER_REGISTRY.md](TRIGGER_REGISTRY.md), [AUTOMATION_ORCHESTRATION.md](AUTOMATION_ORCHESTRATION.md),
and [ADR-056](adr/ADR-056-automation-orchestration.md).
