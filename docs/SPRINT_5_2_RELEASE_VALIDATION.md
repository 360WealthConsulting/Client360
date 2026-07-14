# Sprint 5.2 Release Candidate Validation (RC6)

**Status:** Passed — recommended for merge
**Implementation commit tested:** `50cc73c7055cb34cfd8d5611a42956588ee717f2`
**Draft pull request:** #15
**Base release:** v0.9.4 / `g750b7d5f6a7`
**Alembic head:** `h860c8e6a7b8`
**Validation date:** July 14, 2026

## Release recommendation

RC6 passes engineering release validation for the Sprint 5.2 scope and is
recommended for merge after product review. The migration is additive, maintains
one linear Alembic head, installs on an empty PostgreSQL database, rolls back to
Release v0.9.4, removes Sprint-owned portal artifacts, preserves v0.9.4 sentinel
data, and re-upgrades successfully.

Client-facing seeded letter, organizer, questionnaire, and checklist content is
structural baseline content. Firm legal and tax approval of that content is an
operational launch gate, not a code-merge blocker.

## Automated validation

| Area | Result | Evidence |
|---|---|---|
| Full automated suite | Pass | 69 tests passed in 2.13 seconds |
| Focused tax/intake/portal/workflow/security suite | Pass | 39 tests passed in 1.25 seconds |
| Python compilation | Pass | `app`, `migrations`, and `tests` compiled |
| Clean database migration | Pass | Empty PostgreSQL database upgraded base-to-head |
| Upgrade from v0.9.4 | Pass | `g750b7d5f6a7` → `h860c8e6a7b8` |
| Downgrade and re-upgrade | Pass | Head → v0.9.4 → head |
| Alembic lineage | Pass | Exactly one head: `h860c8e6a7b8` |
| Application startup | Pass | Uvicorn startup and graceful shutdown completed |
| Route registration | Pass | 142 application routes; all 15 intake routes present |
| OpenAPI generation | Pass | All 13 versioned intake API operations present |
| Template loading | Pass | Staff and portal intake templates loaded |
| Staff authentication boundary | Pass | Live unauthenticated staff API request returned 401 |
| Portal authentication boundary | Pass | Live unauthenticated portal API request returned 401 |

The only environment warning was the existing urllib3 warning that local Python
uses LibreSSL 2.8.3 rather than OpenSSL 1.1.1 or newer.

## Functional validation matrix

| Capability | Result | Validation |
|---|---|---|
| Engagement-letter creation | Pass | New engagement creates one versioned snapshot |
| Client acceptance | Pass | Portal-scoped acceptance records account, time, and metadata |
| Immutable engagement templates | Pass | Published letter update rejected by database trigger |
| Organizer generation | Pass | Individual/business template selected and tax year captured |
| Organizer saved progress | Pass | Partial responses merge and progress persists |
| Questionnaire conditional logic | Pass | Dependent question appears only when condition matches |
| Questionnaire completion | Pass | Missing visible required answers block completion |
| Checklist generation | Pass | Required and optional snapshot items generated with due dates |
| Checklist synchronization | Pass | Portal request state updates checklist and missing-item state |
| Document uploads | Pass | Existing portal upload confirmation path exercised |
| Document versioning | Pass | Upload creates immutable `document_versions` row |
| Missing-information tracking | Pass | Required missing items open and resolve with documents |
| Reminder generation | Pass | Idempotent missing-document and questionnaire reminders published |
| Client readiness | Pass | Four objective intake gates produce percentage readiness |
| Preparer readiness | Pass | True only when every blocking gate passes |
| Workflow advancement | Pass | Existing intake/documents steps complete; prepare activates |
| Notification publication | Pass | Existing provider-neutral notification ledger reused |
| Timeline publication | Pass | Launch and milestones appear in client timeline |
| Immutable audit publication | Pass | Tax intake audit row created; direct mutation rejected |
| Staff authorization | Pass | Intake capabilities plus tax record scope enforced |
| Portal authorization | Pass | Person/household grant and task/document permissions enforced |
| Record filtering | Pass | Unauthorized staff and portal principals receive no records |

The isolated validation database recorded 12 engagement letters, 12 organizers,
12 questionnaires, 36 checklist items, 10 answers, 6 document versions, 4
resolved missing items, 26 intake notifications, 22 intake timeline events, 22
intake audit events, and 2 workflows advanced to active preparation during the
combined automated runs.

## Template and schema validation

- Twelve new intake tables are present.
- One published engagement-letter template is seeded.
- Two organizer templates and two questionnaire templates are seeded for
  individual and business audiences.
- Six dynamic questions include required and conditional examples.
- Two checklist templates contain six required/optional definitions.
- Two intake capabilities are granted to the administrator role.
- Published letter headers, organizer definitions, questionnaire headers and
  questions, checklist headers, and checklist items are protected through
  immutable published-template rules or their protected parent definitions.
- Launch-time snapshots isolate in-flight intake records from future versions.

## Manual validation

- Confirmed PR #15 is open, draft, and targets `main`.
- Inspected the engagement creation path and confirmed intake orchestration is
  automatic and idempotent.
- Loaded the staff intake dashboard and portal intake templates through Jinja.
- Generated OpenAPI and confirmed every staff and portal intake contract.
- Started the application with the RC6 database and confirmed both staff and
  portal unauthenticated requests are isolated with distinct 401 responses.
- Inspected the portal upload hook: the existing document request/version flow
  synchronizes tax checklist state rather than creating a second upload system.
- Inspected workflow advancement: only active `intake` and `documents` snapshot
  steps are completed, and existing dependency execution activates preparation.
- Confirmed scheduler registration for daily 9:00 AM Eastern intake reminders.
- Confirmed core intake code has no vendor-specific tax-preparation dependency.

An authenticated external-OIDC browser walkthrough was not performed against the
disposable database. Staff and portal authorization, household isolation, grant
permissions, and record filtering are covered by automated tests and live 401
boundary checks. An authenticated staging walkthrough remains an operational
pre-production gate.

## Sentinel preservation

The following counts were identical immediately before and after downgrade to
Release v0.9.4:

| Record type | Before | After |
|---|---:|---:|
| People | 77 | 77 |
| Documents | 10 | 10 |
| Workflow instances | 29 | 29 |
| Pre-existing portal document requests | 2 | 2 |
| Immutable audit events | 149 | 149 |

All 12 Sprint 5.2 tables were absent after downgrade. Portal document requests
and notifications created specifically by Sprint 5.2 were removed; existing
Release v0.9.4 portal requests were preserved. Re-upgrade completed at
`h860c8e6a7b8`.

## Migration risks

- Downgrade intentionally deletes Sprint 5.2 intake responses, acceptance state,
  checklists, and missing-item records. Production rollback requires backup or
  export after clients begin intake.
- Sprint-owned portal document-request rows and intake notifications are removed
  on downgrade. Uploaded document records and immutable historical audit/timeline
  records remain.
- Existing v0.9.4 tax returns require manual intake launch after upgrade; newly
  created engagements launch intake automatically.
- Seeded fixed template codes must be extended through new versions rather than
  edits to applied migration history.

## Known issues and scope boundaries

- Seeded client-facing content requires firm legal/tax approval.
- Rich browser template authoring, drag-and-drop questions, and compound boolean
  condition builders are deferred.
- In-app notifications are enabled; email, SMS, and push providers remain
  disabled by default.
- The portal shows progress and status but does not yet provide a polished
  multi-step form-builder experience.
- The local LibreSSL/urllib3 warning should be eliminated in production images.

## Production readiness checklist

- [x] Clean base-to-head migration
- [x] One migration head
- [x] v0.9.4 upgrade, downgrade, and re-upgrade
- [x] Sentinel and Sprint-artifact cleanup validation
- [x] Full and focused automated suites
- [x] Startup, routes, OpenAPI, and templates
- [x] Staff, portal, record, and household authorization
- [x] Workflow, documents, notifications, timeline, and immutable audit reuse
- [ ] Firm approval of client-facing intake template content
- [ ] Authenticated staff and client staging walkthrough using production OIDC

## Final decision

No engineering release blockers were found. Merge PR #15 after review approval.
Complete the two operational gates above before enabling client intake in
production. Do not begin Sprint 5.3 as part of RC6.
