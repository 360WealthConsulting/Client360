# ADR-022 — Workflow Automation as an orchestration layer over the existing engine

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Workflow); Compliance Architecture (execution audit);
Business Operations Owner (Michael Shelton — process requirements).

## Context
A comprehensive workflow engine already existed (`workflow_templates`, `workflow_template_steps`,
`workflow_instances`, `workflow_steps`, `workflow_step_dependencies`, `workflow_events`,
`workflow_escalations`, plus `automation_triggers`/`automation_actions`) with `launch_workflow`,
`transition_workflow` (pause/resume/cancel/complete state machine), `complete_step` (dependency
DAG advancement + auto-complete), `process_event` (launches workflows from the trigger registry),
SLA/escalation sweeps, manual-approval (segregation-of-duties) gates, conditions, serial/parallel
execution, an append-only event ledger, audit, and write-once evidence. It powers tax engagements.
What it lacked for the enterprise workflow objective was: per-step **retry** and direct
**assignment**, **wired domain-event triggers** (the `automation_triggers` table was empty), a
deterministic **action registry** beyond `publish_timeline`, and a **capability surface** distinct
from the tax/`work.*` gating.

## Decision
Workflow Automation is a deterministic **orchestration layer OVER the existing engine** — not a
rebuild. It **owns no business entities** and never becomes a source of truth.
- It **reuses** `launch_workflow` / `transition_workflow` / `complete_step` / `process_event` /
  `workflow_detail` / `list_templates`; the existing engine, published (immutable) templates,
  `/workflows` routes, `work.*` capabilities, and the tax launcher are **not modified**.
- It adds the genuinely-missing pieces: per-step **retry** (`retry_count`/`max_retries`) and
  **assignment** (`assigned_user_id`) columns on `workflow_steps`; **domain-event triggers**
  seeded (INACTIVE) into the existing `automation_triggers` table mapping D.13–D.16 business events
  to existing templates; a deterministic **action registry** (`app/services/workflow_orchestration/
  actions.py`) that **invokes existing domain services** (timeline event, document relationship,
  notification, assignment) and never duplicates business logic; and the **`workflow.*`
  capability family** on a new `/workflow-automation` surface.
- **Triggers `fire()` are failure-isolated:** a trigger error never breaks the calling domain
  operation. Triggers are **inactive by default** — nothing auto-launches until an admin activates
  one. Triggers are **deterministic** — no AI, no probabilistic launches.
- Execution stays **in-process and deterministic**: no event bus, no message queue, no BPMN
  engine, no distributed orchestration. Step advancement is completion-driven (as today); SLA
  escalation is the existing observe-only scheduler sweep.
- **Advisor Work / Documents / Compliance are REFERENCED, never owned** (workflow steps ARE the
  work; actions link documents/create timeline events via the owning services). **Timeline**
  receives approved workflow lifecycle events via the existing engine's publishers. **Analytics**
  consumes an `active_workflows` statistic; Workflow never depends on Analytics. **Microsoft 365**
  is reused through existing integrations/references — not duplicated. Record scope is enforced
  in-service (instance person/household anchor + `record.read_all`; firm workflows to
  `workflow.view`).

## Alternatives considered
1. **Build a new parallel workflow engine.** Rejected: duplicates a mature, tax-critical engine;
   two engines fragment execution/audit and violate "extend, don't replace" + single ownership.
2. **Extend `execute_automation_action` in-place for new actions.** Rejected: it is on the
   tax-critical path; a separate orchestration action registry keeps the base engine untouched
   while still invoking existing services (the "domain adapter" seam the engine documents).
3. **Auto-activate domain triggers on seed.** Rejected: would surprise existing flows; seeded
   inactive, admin-activated, so behavior is opt-in and deterministic.

## Reasons for the decision
The engine already implements the hard parts correctly; the enterprise value is cross-domain
triggering, retry/assignment, and a governed capability surface. A thin reuse layer delivers that
without risking the tax flow, preserving the D.5 golden and every ADR.

## Consequences
### Positive consequences
- Cross-domain orchestration (business event → workflow) with retry/assignment, on one engine.
- Zero change to the tax launcher, published templates, `/workflows`, or `work.*`.
- Deterministic, in-process, auditable; no new distributed infrastructure.

### Negative consequences and tradeoffs
- Two capability families now touch workflows (`work.*` legacy engine surface; `workflow.*`
  orchestration surface) — a documented coexistence.
- Retry re-activates a step but does not itself re-run automation side effects (idempotent by
  design); step advancement remains completion-driven, not a background executor.
- `automation_actions.execute_automation_action` still supports only `publish_timeline`; new
  orchestration actions run through the separate registry.

## Enforcement
- `app/services/workflow_orchestration/{service,triggers,actions}.py`; routes
  `app/routes/workflow_automation.py` (in-route `workflow.*` gating; `/workflow-automation` matches
  no middleware rule). Migration `o5f6a7b8c9d0` (adds retry/assign columns + seeds inactive
  triggers + 7 capabilities; no new tables). D.5 golden untouched; the base engine never imports
  the orchestration layer (one-way). Tests: `tests/test_workflow_orchestration.py`;
  manifest/platform-architecture/route guards updated.

## Exceptions
None currently approved.

## Revisit conditions
A background step executor, scheduled workflow launches, distributed orchestration, or an external
BPMN/queue would each warrant a new or superseding ADR.

## References
- `app/services/workflow_orchestration/`, `app/routes/workflow_automation.py`,
  `app/services/workflow_automation.py` (reused engine)
- migration `migrations/versions/o5f6a7b8c9d0_workflow_orchestration.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_workflow_orchestration.py`; relates to ADR-002, ADR-007, ADR-009, ADR-013
