# Client360 Release 0.9.4 — Tax Domain Foundation

Released July 14, 2026 from merge commit
`6d8ef3351647d50ff7b1a3ca96fb5d45c379c107`.

## Overview

Release 0.9.4 begins implementation of Epic 5 and establishes the normalized,
provider-neutral tax domain. Client360 can now create tax engagements and
jurisdiction-specific returns, calculate configurable deadlines, assign tax
work, launch a versioned workflow automatically, publish client timeline and
immutable audit events, and present authorized production metrics.

## Schema

- Schema version and Alembic head: `g750b7d5f6a7`.
- Parent release: v0.9.3 / `f640a6c4e5f6`.
- New tables: `tax_firms`, `tax_offices`, `tax_office_memberships`,
  `tax_years`, `filing_jurisdictions`, `tax_return_types`,
  `tax_filing_statuses`, `tax_seasons`, `tax_calendars`,
  `tax_deadline_rules`, `tax_engagements`, `tax_engagement_returns`,
  `tax_deadlines`, and `tax_workflow_links`.
- Exactly one Alembic head is maintained.

## Tax platform capabilities

- Multiple firms and offices with preparer/reviewer office memberships and
  capacity metadata.
- Tax years, seasons, filing jurisdictions, return types, and filing statuses.
- Engagements with independent jurisdiction-specific returns.
- Versioned deadline rules preserving both calculated and overridden due dates.
- Baseline federal reference data for 1040, 1065, 1120, 1120S, 1041, 706,
  990, and 941 work.
- Tax dashboard metrics for engagement volume, returns, upcoming deadlines,
  overdue work, unassigned work, and review queues.

## APIs

Versioned operations under `/api/v1/tax` provide reference data, firms,
offices, tax years, jurisdictions, return types, filing statuses, dashboard
metrics, engagement listing and creation, and controlled deadline overrides.

## Platform reuse

- New engagements launch the published `tax_engagement_foundation` workflow.
- Tax return ownership uses the existing assignment engine.
- Five tax queues use the existing reusable queue model.
- Timeline publication, immutable audit, capability-based authorization, and
  record filtering use the existing Client360 services.
- Core tax business logic has no Drake, UltraTax, Lacerte, CCH, or other
  tax-software vendor binding.

## Validation

- 65 automated tests passed; focused RC suite: 29 passed.
- Clean PostgreSQL base-to-head migration passed.
- Upgrade from v0.9.3, downgrade, sentinel preservation, and re-upgrade passed.
- Python compilation, application startup, 127-route registration, OpenAPI
  generation, tax template loading, authorization, record filtering, timeline,
  and immutable audit checks passed.

See [Sprint 5.1 Release Validation](SPRINT_5_1_RELEASE_VALIDATION.md) for the
complete evidence and risk assessment.

## Known limitations and launch gates

- Baseline deadlines require filing-season review for statutory changes,
  holidays, and disaster relief before operational use.
- State/local jurisdiction catalogs and live authority feeds are not included.
- An authenticated staging walkthrough depends on the production OIDC provider.
- Engagement letters, organizers, questionnaires, missing-information tracking,
  e-file lifecycle, notices, estimates, and tax-provider synchronization remain
  future Epic 5 work.
- Production rollback after entering tax-domain data requires a database backup
  or export because downgrade removes the Sprint 5.1 tax tables.

## Recommended Sprint 5.2

Build tax engagement intake and client collaboration: engagement letters,
organizers, questionnaires, document checklists, missing-information tracking,
portal delivery/completion, notifications, assignments, and automatic workflow
advancement. Reuse the Release v0.9.4 tax IDs and all existing portal, document,
workflow, queue, notification, timeline, audit, and authorization services.
