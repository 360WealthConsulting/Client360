# Unified Work Queue (Phase D.39)

`GET /work` is the **Unified Work Queue** — one governed, cross-domain place to work through actionable
items. It is a **read-only composition surface** over the authoritative work services; it is **not** a
task/workflow/exception/assignment engine and is **never** the source of truth. Every state-changing
action delegates to the authoritative owning service.

See also: [`ADR-044`](adr/ADR-044-unified-work-queue.md), [`WORK_QUEUE_ADAPTER_GUIDE.md`](WORK_QUEUE_ADAPTER_GUIDE.md),
[`WORK_QUEUE_ACTIONS.md`](WORK_QUEUE_ACTIONS.md), [`WORK_QUEUE_GOVERNANCE.md`](WORK_QUEUE_GOVERNANCE.md).

## Invariants (why the queue is safe)

- **Composition, not a new engine.** No second task table, workflow engine, assignment engine, exception
  system, event bus, event log, CQRS write model, event sourcing, or shadow business logic. The three
  legacy sources (tasks, workflow steps, exceptions) are read through the existing, record-scoped
  `work_management.work_items`.
- **The queue never mutates.** Actions go through the dispatch layer → the authoritative owning service,
  which enforces scope, audits, and publishes any domain event through the existing outbox. The queue
  records no duplicate audit and publishes no domain event.
- **Projections stay disposable.** No unified-work projection. Count widgets reuse the D.37 adoption
  sources (projection when healthy+fresh, else authoritative fallback). The queue never reads an `rm_*`
  table directly.
- **RBAC / record-scope preserved, fail closed.** A tab, filter, or item appears only where the
  principal has the capability (never shown-then-403). An adapter that cannot resolve scope returns
  nothing — an item never appears because scope could not be determined.
- **Saved views are presentation state only** — they never alter a source record.

## Work-source inventory (adopted)

| Source domain | Authoritative service (list) | Item id | Deep link | Projection | Queue actions |
|---|---|---|---|---|---|
| tasks | `work_management.work_items` | task id | `/people/{id}` or `/tasks` | `rm_operational_tasks` (count) | open, claim, assign |
| workflow | `work_management.work_items` (active steps) | step id | `/workflow-automation/{instance}` | — | open, claim, assign, complete |
| exceptions | `work_management.work_items` (tax/benefits/insurance) | exception id | `/exceptions/{id}` | `rm_exception_dashboard` | open, claim, assign, acknowledge, resolve |
| advisor_work | `advisor_work.list_work` | item id | `/advisor-work/{id}` | — | open |
| compliance | `compliance.reviews.list_reviews` | review id | `/compliance/reviews/{id}` | `rm_compliance_queue` | open |
| documents | `document_platform.list_documents(status="review")` | document id | `/document-library/{id}` | `rm_document_status` | open, claim, assign, approve |
| tax | `tax_domain.list_engagements` | return id | `/tax/returns?return_id={id}` | `rm_tax_pipeline` | open, claim, assign |
| insurance | `insurance.list_cases` | case id | `/insurance/cases/{id}` | `rm_insurance_pipeline` | open |
| opportunities | `opportunity.list_opportunities(status="open")` (follow-up due) | opportunity id | `/opportunities/{id}` | `rm_opportunity_pipeline` | open |
| meetings | `scheduling.list_meetings(upcoming_only=True)` | meeting id | `/scheduling/{id}` | — | open |

### Excluded sources (documented, not adapted)

- **Insurance requirements** — no firm-wide scoped list (needs a case/policy anchor).
- **Benefits enrollments** — no `list_enrollments(principal)`; **benefits obligations** — org-anchored
  only (no firm-wide list).
- **Annual-review sessions** — keyed per-person; no cross-client "due" list (use
  `portfolio.accounts_due_for_review` + `/annual-review/{person_id}` on that surface).
- **Meeting follow-ups** — no cross-meeting scoped list (sub-item of a meeting only).
- **Notifications** — a non-authoritative delivery ledger with no record scope and no operator action.

Sources are excluded rather than adapted with parallel domain logic; each can be adopted later once it
gains a safe firm-wide scoped actionable list (see the adapter guide).

## The UnifiedWorkItem contract

References-only, presentation + routing metadata: `work_item_key` (`domain:type:id`, stable),
`source_domain`, `source_type`, `source_id`, `title`, `summary`, `status` (source status, preserved),
`status_group` (display/filter normalization), `priority`, `due_at`, `overdue`, `age_days`, `sla_state`,
`assignee_user_id`/`assignee_name`, `team`, `person_id`/`person_name`, `household_id`/`household_name`,
`workflow_instance_id`, `exception_id`, `deep_link`, `allowed_actions`, `capability`, `created_at`,
`updated_at`, `source_reference`. Business state is never copied unnecessarily; the source status is
never overwritten.

## Views, filters, sort

- **Tabs / built-in views:** All Work, My Work, Team Work, Unassigned, Overdue, Due Today, Due This
  Week, High Priority, SLA Breaches, Workflow Exceptions, Compliance Queue, Tax Season, Insurance,
  Document Review, Opportunities, Meetings — each shown only where the capability is held.
- **Filters:** domain, status (group), priority, SLA state, overdue, unassigned, assignee, team,
  person/household, search, due (today/week/overdue), due_from/due_to.
- **Saved views:** save / apply / rename / delete / set-default / reset-to-default / remember filters.
- **Deterministic default sort:** overdue → SLA-breached → priority → earliest due → oldest unresolved →
  stable source key. No AI ranking.

## SLA presentation (reused, not recreated)

Authoritative SLA/priority stay in the owning domains + `work_intelligence`. The queue normalizes them
into presentation states only: `on_track`, `due_soon`, `overdue`, `breached`, `escalated`, `unknown`.
**Unknown stays unknown — no deadline is invented when the source has none.**

## Routes

`GET /work` (view + filters, `work.read`), `POST /work/action`, `POST /work/bulk-action`,
`POST /work/views` / `/work/views/default` / `/work/views/delete` (`work_queue.saved_views`),
`GET /work/summary` (AI-ready, `work.read`), `GET /work/diagnostics` (`observability.audit`).

## Performance

Each adapter fetches a bounded candidate set (`CANDIDATE_LIMIT`), merged + sorted + paginated in one
pass; no unbounded loads, no N+1 (display names resolved for the visible page only). Cross-domain totals
are bounded by the per-adapter caps (a federated merge) — deterministic, documented. Projection-backed
counts reuse the D.37 adoption fallback. Diagnostics report page latency, per-adapter latency/errors,
suppression, projection/fallback usage, and action counts.

## Capabilities

New: `work_queue.saved_views` (non-sensitive) → advisor, operations, compliance, administrator. Reused:
`work.read` (view), `capacity.read` (team/unassigned), `work.write` (assignment via the authoritative
engine), `exception.write` (acknowledge/resolve), `documents.write` (approve), the per-domain read caps
(tab/item visibility), and `observability.audit` (diagnostics/governance).
