# Trigger Registry (Phase D.51)

The **trigger registry** (`TRIGGER_REGISTRY` in `app/services/automation_orchestration/registry.py`) is the
declarative catalog of the firm's automation trigger types and, for each, the **authoritative owner** it
fires through. It is metadata only: the Automation Orchestration layer owns no trigger, fires nothing, and
never routes an event — it references the owners and explains the result with a deep link.

## Trigger types

Each trigger type declares its `owner` (the authoritative trigger owner), `source` (where it fires from),
`execution_owner` (the authoritative execution owner), and `runtime_gate`.

| Trigger type | Owner | Source | Execution owner |
| --- | --- | --- | --- |
| `scheduled` | automation | automation.runner (schedules) | automation |
| `event` | workflow_orchestration.triggers | events.outbox | workflow_automation |
| `workflow` | workflow_automation | workflow_automation (steps) | workflow_automation |
| `document` | document_platform | document.* events | workflow_automation |
| `compliance` | compliance | compliance.* events | workflow_automation |
| `manual` | workflow_orchestration | operator action | workflow_orchestration |
| `lifecycle` | workflow_automation | entity lifecycle events | workflow_automation |

## Ownership boundaries (never re-implemented here)

- **Domain-event triggers** are owned by `workflow_orchestration/triggers.py` — `list_triggers` (read) and
  `fire` / `configure_trigger` / `set_active` (write). The registry catalogs the trigger types; the layer
  **never calls** `fire` or `configure_trigger` — governance forbids it.
- **Scheduled triggers** are owned by the Automation engine's `runner.py` (`due_schedules`,
  `run_worker_cycle`) + `automation/service.py` schedules. The layer never advances a schedule or runs a
  worker cycle.
- **Event routing** is owned by the Event outbox (`events/publisher.py` write; `events/diagnostics.py` read).
  The layer composes the outbox **diagnostics** (`event_counts`, `events_by_domain`, dead letters) and never
  publishes or routes an event.

## How the registry is used

The `trigger_activity` dashboard composes:

- **`registered_triggers`** — configured domain-event triggers from the Trigger engine (`list_triggers`).
- **`trigger_types`** — the registered trigger-type catalog (this registry).
- **`event_activity`** — event outbox activity by delivery status, from the Event outbox diagnostics.

Governance validates that every trigger type declares all four fields (owner, source, execution owner,
runtime gate), that keys are unique, and that the layer contains no trigger **fire** / event **publish** call.

See [AUTOMATION_REGISTRY.md](AUTOMATION_REGISTRY.md), [AUTOMATION_ORCHESTRATION.md](AUTOMATION_ORCHESTRATION.md),
and [ADR-056](adr/ADR-056-automation-orchestration.md).
