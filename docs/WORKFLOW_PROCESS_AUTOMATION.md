# Workflow and Process Automation

Sprint 4.3 adds a vendor-independent orchestration layer above Client360 records. Published templates are immutable versions; running instances retain their template ID and version so later template changes never alter in-flight work.

## Architecture

- `workflow_templates`, `workflow_template_steps`, and `workflow_step_dependencies` define versioned directed acyclic processes. Conditions are declarative JSON and are evaluated only against explicit launch context.
- Published definitions are database-immutable. A new template version is required for every change, and recursive validation rejects direct, multi-hop, and cross-template dependency cycles.
- `workflow_instances` and `workflow_steps` remain backward compatible with Release 0.9.1. New nullable template references allow legacy instances to continue operating.
- Each launch stores a template snapshot and a definition snapshot for every instantiated step, insulating in-flight execution and history from future versions.
- `workflow_events` is append-only and provides the execution ledger. Idempotency keys protect launches, domain events, and automation actions from retry duplication.
- `work_approvals` is linked to workflow steps. Independent approvals are enforced by service validation and a database check constraint.
- `workflow_escalations` records idempotent SLA breaches. Escalation evaluation can be called by the scheduler or controlled API.
- `automation_triggers` maps domain events to published templates. The event envelope supports people, households, documents, relationships, portfolio records, Microsoft events, and future domain records without vendor-specific workflow code.

## Execution

Instances support pause, resume, cancel, complete, and reopen with a validated state machine. A step activates only when all declared dependencies are complete. Parallel branches are represented by several steps sharing satisfied dependencies. False conditions become `skipped`, satisfying downstream completion dependencies without manual intervention.

Approval steps cannot complete until an approved review exists. The requester can never act as independent approver, and an approval assigned to a specific user cannot be decided by another user.

## API and UI

The UI is available at `/workflows`; instance detail is `/workflows/{id}`. Versioned APIs under `/api/v1/workflows` cover template discovery, launch, lifecycle actions, step completion, approval routing, domain-event processing, SLA evaluation, metrics, and reporting data.

Access uses existing `work.read`, `work.write`, and `capacity.read` capabilities. Independent decisions require the new sensitive `work.approve` capability, granted initially to administrators and compliance.
Workflow detail applies the same person, household, workflow-instance, user, and team assignment scope used by My Work; capability possession alone does not expose an unassigned client workflow.

## Operational guidance

Run SLA evaluation frequently enough for the firm's shortest SLA. Use a stable source event ID as the event idempotency key. Template revisions must be created as a new version; published rows should not be edited. Automation actions must use the action ledger before invoking an external provider.

## Seeded templates

Prospecting, client onboarding, Schwab account opening, asset transfer, annual review, tax preparation, tax extension, IRS notice, estate planning, insurance review, client termination, and compliance review are seeded at version 1. Each includes intake, execution, independent approval, and delivery stages.
