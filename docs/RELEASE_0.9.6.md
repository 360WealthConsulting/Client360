# Client360 Release 0.9.6 — Tax Return Lifecycle & Production Automation

Released July 14, 2026 from merge commit
`c50898fd5a9de1e1383d480a0d00e60f3ce7ba31`.

## Overview

Release 0.9.6 replaces the production half of TaxDome's tax workflow with
native Client360 return lifecycle management. Engagement returns now move
through a canonical 15-state production pipeline with preparer/manager/partner
review routing, client portal approvals, provider-neutral e-filing, production
queues, and dashboards.

## Schema

- Schema version and Alembic head: `i970d9f7b8c9`.
- Parent: Release v0.9.5 / `h860c8e6a7b8`.
- New tables: `tax_return_lifecycle_events`, `tax_return_reviews`,
  `tax_review_corrections`, `tax_client_approvals`, `tax_filing_events`.
- Ten new columns on `tax_engagement_returns` covering lifecycle timestamps
  and filing status/provider/external ID.
- Nine seeded `work_queues` rows and two append-only triggers on the
  lifecycle and filing event ledgers.
- Exactly one Alembic head is maintained.

## Return lifecycle

- Canonical states: `received → ready_to_prepare → in_preparation →
  manager_review → partner_review → client_review →
  awaiting_efile_authorization → ready_to_file → filed → accepted →
  delivered → completed → archived`, with `awaiting_information` and
  review/client rejection paths back to preparation.
- Every transition is recorded in an immutable event ledger with prior state,
  target state, reason, actor, portal identity, and timestamp, and publishes a
  workflow-history event, a client timeline event, and an immutable audit
  event.
- Existing workflow execution snapshots automatically advance received,
  preparation, review, and filing stages without a second workflow engine.

## Review engine

- Preparer, manager, and partner reviews are linked to the existing
  independent `work_approvals` engine.
- Returned reviews create correction records and route work back to
  preparation; approvals advance the return to the next stage.

## Filing engine

- Provider-neutral filing status machine: `ready → submitted → accepted /
  rejected → resubmitted → accepted`.
- Filing events carry provider key, external ID, submission ID, reason code,
  message, metadata, and an idempotency key.
- Only the manual filing provider is enabled; no Drake, IRS, or other vendor
  API is bound to business logic.

## Portal additions

- Client return approval, e-file authorization, and delivery acknowledgement
  are protected by the existing portal grant model and recorded with portal
  account, decision, notes, and time.
- `/portal/tax-returns` and the portal dashboard expose return, filing, and
  approval status within the existing household authorization boundary.

## Queues and dashboards

- Nine reusable production queues: ready to prepare, preparing, awaiting
  client, manager review, partner review, ready to file, rejected, delivery,
  and completed today.
- Four staff dashboards — `/tax/returns`, `/tax/returns/reviews`,
  `/tax/returns/filing`, `/tax/returns/metrics` — expose counts by status,
  workload by preparer/reviewer, overdue and waiting counts, average
  preparation time, 30-day velocity, and review bottlenecks.

## APIs and UI

Staff APIs under `/api/v1/tax/returns` provide lifecycle detail/transitions,
workflow synchronization, review requests/decisions, correction resolution,
filing events, and production metrics. Portal APIs under
`/api/v1/portal/tax/returns` expose scoped return status and client decisions.
Seventeen new staff, API, and portal routes are registered.

## Platform integration

- Existing timeline, immutable audit, capability authorization, record scope,
  portal grants, assignments, and queues remain authoritative.
- Existing notification providers deliver idempotent client-facing lifecycle
  notifications.

## Validation

- Full suite: 74 passed (5 new Sprint 5.3 lifecycle tests).
- Clean PostgreSQL base-to-head migration passed.
- Upgrade from v0.9.5, downgrade, re-upgrade, and sentinel preservation
  (people, documents, workflows, portal requests, audit events, existing tax
  engagements/returns) passed with identical row counts and ID checksums at
  every step.
- FastAPI startup/shutdown, 167-route registration, OpenAPI generation, staff
  and portal template rendering, review routing, filing transitions, portal
  approvals, authorization/record filtering, timeline publication, and
  immutable audit-trigger enforcement all passed.
- Two template defects found during release-candidate validation (a missing
  shared staff base template and a Jinja/dict-key collision on the production
  dashboard) were fixed and re-validated before merge. See
  [PR #16](https://github.com/360WealthConsulting/Client360/pull/16) for
  the full validation record.

## Known limitations and launch gates

- Only the provider-neutral manual filing adapter is enabled; Drake/IRS
  integration is deferred to a future sprint.
- Production velocity uses current lifecycle timestamps rather than a
  separate materialized analytics warehouse.
- Dashboard preparer/reviewer groupings expose IDs; enriched staff names and
  capacity forecasts are future presentation work.
- Bulk lifecycle operations, electronic signature for e-file authorization,
  and provider polling are deferred.
- Downgrade removes Sprint 5.3 production state and requires backup planning
  after production return activity begins.

## Recommended Sprint 5.4

Build tax document intelligence and missing information: automated document
classification against checklist/organizer requirements, OCR/extraction
groundwork, missing-item detection beyond current checklist tracking, and
document-to-return linkage reporting. Reuse the existing document, checklist,
intake, timeline, audit, and authorization platforms.
