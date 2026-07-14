# Tax Domain Foundation — Sprint 5.1

## Purpose

Sprint 5.1 establishes the canonical, provider-neutral tax domain used by the
remainder of Epic 5. It adds tax firms and offices, staff office memberships,
tax years and seasons, filing jurisdictions, return types and filing statuses,
engagements and returns, configurable calendars and versioned deadline rules.

## Architecture

The tax layer owns tax facts and orchestration links. It does not duplicate
platform services. A return is assigned through `record_assignments` with the
`tax_return` entity type; its process is a normal versioned workflow instance;
tax queues are normal `work_queues`; lifecycle changes use `timeline_events`
and immutable `audit_events`. Client, household, relationship, document,
portal, and notification identifiers remain owned by their existing domains.

An engagement represents the agreement with the client for a tax year. One or
more jurisdiction-specific returns belong to it. Each return can have its own
deadline and workflow, allowing federal and state work to move independently.
External tax software must connect through future provider adapters and store
vendor identifiers as external keys; vendor concepts must not enter core rules.

## Authorization

`tax.read`, `tax.write`, `tax.review`, and `tax.deadline.manage` are capabilities.
Collection queries are filtered to firm-wide readers, assigned users/teams, or
active members of the return's office. Deadline overrides require a reason and
create an immutable audit record. Route middleware and endpoint dependencies
both enforce capability boundaries.

## Deadlines and calendars

Deadline rules are versioned by jurisdiction, return type, rule code, and
version. Calculated and effective due dates are stored separately so an override
does not erase the source calculation. The baseline federal rules are marked as
configuration requiring annual verification; production administrators must
review statutory and disaster-relief changes before opening each season.

## APIs and UI

- `GET /tax` — authorized tax-production dashboard.
- `GET /api/v1/tax/reference-data` — firms, offices, years, jurisdictions,
  return types, filing statuses, and seasons.
- `GET /api/v1/tax/dashboard` and `/engagements` — filtered production data.
- `POST /api/v1/tax/engagements` — engagement, return, deadline, assignment,
  workflow, timeline, and audit orchestration.
- `PATCH /api/v1/tax/deadlines/{id}` — controlled deadline override.

## Operational checklist

1. Verify federal and state deadline rules for the tax year.
2. Configure offices, active staff memberships, roles, and capacity.
3. Create the tax year, season, and office calendars.
4. Launch a test engagement and confirm assignment, deadline, workflow,
   timeline, and audit visibility.
5. Confirm restricted staff can see only assigned or authorized-office work.

## Known limits

Sprint 5.1 does not implement organizers, engagement letters, source-document
classification, e-file status, notices, estimates, or provider synchronization.
Those remain in later Epic 5 sprints. Holiday and disaster-relief data is
configuration, not an embedded legal calendar.
