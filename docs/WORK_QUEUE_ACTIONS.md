# Work Queue Actions (Phase D.39)

How the Unified Work Queue performs state-changing actions. **The queue never mutates business state.**
Every action delegates to the authoritative owning service, which enforces record scope, appends its own
ledger/audit, and publishes any domain event through the existing transactional outbox.

See [`UNIFIED_WORK_QUEUE.md`](UNIFIED_WORK_QUEUE.md), [`ADR-044`](adr/ADR-044-unified-work-queue.md).

## The dispatch contract

`work_queue.dispatch.dispatch_action(principal, *, work_item_key, action, params, request_id)`:

1. parse `work_item_key` (`domain:type:id`);
2. reject `open` (a plain deep link, not a dispatched action);
3. validate `action` ∈ the domain's allowed actions (`ALLOWED_ACTIONS`);
4. check the route-level capability floor (`ACTION_CAPABILITY`);
5. **delegate** to the authoritative owning service (which enforces record scope + audits + publishes);
6. return `{ok, outcome, message}` — never raises into the route (POST-redirect-GET).

The dispatch layer contains **no business-state mutation of its own**, records **no duplicate audit
event**, and publishes **no domain event** — those remain the owning service's responsibility.

## Supported actions

| Action | Domains | Delegates to | Capability floor | Scope + audit owner |
|---|---|---|---|---|
| open | all | (deep link only — no mutation) | — | — |
| claim / assign | tasks, workflow, exceptions, documents, tax | `work_management.assign_work` (+ `authorize_assignment_target`) | `work.write` | assignment engine (scope + `assignment.created` audit) |
| complete | workflow | `workflow_orchestration.complete_step` | `work.write` | workflow engine (scope + `workflow.step.completed` audit) |
| acknowledge | exceptions | `exception_engine.acknowledge` | `exception.write` | exception engine (scope + `exception.acknowledged` audit) |
| resolve | exceptions | `exception_engine.resolve` | `exception.write` | exception engine (scope + audit + `exception.resolved` outbox) |
| approve | documents | `document_platform.approve` | `documents.write` | document service (scope + `document.*` outbox/timeline) |

Assignment reuses the **single authoritative assignment engine** — the queue does not implement a second
assignment system. `claim` assigns to the acting user; `assign` assigns to `params.user_id`; both go
through `authorize_assignment_target`, which enforces write scope (and `assignment.manage` for client
records).

Sources that are **open-only** in the queue (advisor_work, compliance, insurance, opportunities,
meetings): their mutations have domain-specific semantics (e.g. compliance decisions are
authority-gated; tax lifecycle mutators need explicit scope enforcement) and are performed on their own
surface. The queue links to the source record; it does not invent a generic action.

## Bulk actions

`dispatch_bulk(principal, *, work_item_keys, action, params, request_id)` supports only **proven-safe,
semantically-identical** actions: **claim, assign, acknowledge**. Each selected item is:

- resolved to its adapter/domain;
- capability-checked;
- record-scope-enforced (by the authoritative call);
- validated against the domain's allowed actions;
- **delegated individually** to the authoritative service.

**Partial success is reported honestly** — the result carries `{total, succeeded, failed, results[]}`
with a per-item outcome. The queue never bulk-completes heterogeneous items and never bulk-approves
compliance items; per-item policy checks are never bypassed.

## Audit + events (no duplication)

The owning service is the audit + event owner. The dispatch layer does not double-audit a business
mutation and does not publish a duplicate domain event. Presentation-only actions (saving a personal
view) touch only the queue's own view-state tables; the queue governance validation records a
best-effort `work_queue.governance_validated` audit event, which is not a business mutation.
