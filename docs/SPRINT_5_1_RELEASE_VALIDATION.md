# Sprint 5.1 Release Candidate Validation (RC5)

**Status:** Passed — recommended for merge  
**Implementation commit tested:** `a33a445b0bdeced889a9e57977507aaee7281685`  
**Draft pull request:** #14  
**Base release:** v0.9.3 / `f640a6c4e5f6`  
**Alembic head:** `g750b7d5f6a7`  
**Validation date:** July 14, 2026

## Release recommendation

RC5 is production-ready for the Sprint 5.1 scope and is recommended for merge.
The migration is additive, maintains one Alembic head, migrates a truly empty
PostgreSQL database, rolls back to v0.9.3, and re-upgrades successfully. Existing
client, assignment, task, document, workflow, audit, and timeline sentinel data
was preserved across the cycle.

The release establishes domain infrastructure; it does not claim that baseline
deadline configuration replaces annual tax-authority review. Filing-calendar
approval remains an operational launch gate for each tax season.

## Automated validation

| Area | Result | Evidence |
|---|---|---|
| Full automated suite | Pass | 65 passed in 1.63s |
| Focused tax/work/workflow/security suite | Pass | 29 passed in 0.79s |
| Python compilation | Pass | `app`, `migrations`, and `tests` compiled |
| Clean database migration | Pass | Empty PostgreSQL database upgraded base-to-head |
| Upgrade from v0.9.3 | Pass | `f640a6c4e5f6` → `g750b7d5f6a7` |
| Downgrade and re-upgrade | Pass | Head → v0.9.3 → head |
| Alembic lineage | Pass | Exactly one head, `g750b7d5f6a7` |
| Application startup | Pass | Uvicorn startup and shutdown completed cleanly |
| Route registration | Pass | 127 application routes; all 11 tax routes present |
| OpenAPI generation | Pass | Every tax API path present in generated schema |
| Template loading | Pass | `tax/dashboard.html` loaded successfully |
| Authentication boundary | Pass | Live unauthenticated tax API request returned 401 with request ID |
| Provider neutrality | Pass | Core tax route, service, and migration contain no tax-software vendor bindings |

One non-blocking environment warning was emitted: the local Python build uses
LibreSSL 2.8.3 while urllib3 v2 recommends OpenSSL 1.1.1 or newer.

## Functional validation matrix

| Capability | Result | Validation |
|---|---|---|
| Tax engagement creation | Pass | Engagement and jurisdiction-specific return persisted atomically |
| Automatic workflow generation | Pass | Published `tax_engagement_foundation` template launched and linked |
| Tax assignments | Pass | Existing `record_assignments` engine assigned `tax_return` targets |
| Tax queues | Pass | Five reusable queues seeded in existing `work_queues` |
| Dashboard metrics | Pass | Return, deadline-risk, overdue, unassigned, and review metrics exercised |
| Tax-year handling | Pass | Tax years created and filtered independently from engagement records |
| Filing jurisdictions | Pass | Federal jurisdiction seeded and reference API validated |
| Return types | Pass | Eight provider-neutral return types seeded |
| Filing statuses | Pass | Six individual/default filing statuses seeded |
| Deadline calculations | Pass | Weekend and configured-holiday next-business-day behavior tested |
| Configurable deadline rules | Pass | Eight published, versioned baseline rules linked by jurisdiction and return type |
| Assignment integration | Pass | Assignment event, timeline target resolution, and audit integration exercised |
| Workflow integration | Pass | Launch snapshot, five steps, dependencies, and tax workflow link validated |
| Queue integration | Pass | Tax queues reuse the operational queue table and capability boundary |
| Timeline publication | Pass | Tax engagement events published to client/household timeline |
| Immutable audit publication | Pass | Tax audit record created; direct mutation rejected by database trigger |
| Authorization | Pass | Tax capabilities enforced by middleware and endpoint dependencies |
| Record filtering | Pass | Unassigned user without firm-wide scope received no tax records |
| API validation | Pass | OpenAPI contracts and route handlers validated; live 401 boundary confirmed |

The isolated validation database contained two test tax engagements, two
returns, two calculated deadlines, two workflow links, one tax assignment, two
tax timeline events, and two tax audit events after the automated suite.

## Sentinel preservation

Counts before downgrade and after downgrade to v0.9.3 were identical:

| Record type | Before | After |
|---|---:|---:|
| People | 36 | 36 |
| Households | 24 | 24 |
| Tasks | 3 | 3 |
| Documents | 3 | 3 |
| Workflow instances | 10 | 10 |
| Record assignments | 7 | 7 |
| Immutable audit events | 50 | 50 |
| Timeline events | 36 | 36 |

The downgrade removes all 14 Sprint 5.1 domain tables and its seeded reference
definitions. Tax-specific polymorphic assignments are retained as historical
records but made inactive. Launched workflow instances remain available through
their immutable snapshots with the removed tax template reference cleared.

## Manual validation

- Confirmed PR #14 remains draft, open, and targeted to `main`.
- Confirmed the tax dashboard template loads and uses the existing Client360
  visual components.
- Confirmed the generated API contract exposes reference data, dashboard,
  engagement collection/create, and controlled deadline-override operations.
- Confirmed live application startup and an unauthenticated API denial.
- Inspected the tax orchestration boundary: domain services call the existing
  assignment, workflow, timeline, and audit services rather than duplicating them.
- Confirmed the migration seeds one published workflow template, five queues,
  four capabilities, eight return types, six filing statuses, and eight rules.

An authenticated browser walkthrough was not performed because the disposable
RC database does not have a configured external OIDC identity provider. The
authorization and record-isolation behavior is covered by automated service,
middleware, dependency, and filtering validation.

## Migration risks

- Downgrade intentionally deletes Sprint 5.1 tax-domain data. A production
  rollback after tax engagement entry therefore requires a database backup or
  export before downgrade.
- Published deadline rules are baseline configuration, not legal advice or a
  live tax-authority feed. They must be reviewed before opening each season.
- Existing immutable audit and workflow history is retained during downgrade;
  consumers must tolerate workflow instances whose removed template is
  represented only by its launch snapshot.
- The migration seeds fixed reference codes. Administrators should extend them
  through future controlled configuration rather than editing applied history.

## Known issues and warnings

- The repository does not include optional `httpx`, so Starlette's in-process
  HTTP test client is unavailable. Live startup/401 validation, OpenAPI checks,
  route inspection, and direct service/authorization tests provide coverage;
  adding a dedicated development-test dependency is recommended later.
- Local urllib3 emits a LibreSSL compatibility warning. Production images should
  use a supported OpenSSL build.
- State and local jurisdiction catalogs, holiday feeds, disaster-relief rules,
  engagement intake, organizers, and provider synchronization are intentionally
  outside Sprint 5.1.

## Production readiness checklist

- [x] One migration head
- [x] Empty-database migration
- [x] v0.9.3 upgrade, downgrade, and re-upgrade
- [x] Sentinel preservation
- [x] Full and focused automated suites
- [x] Startup, routes, OpenAPI, and template validation
- [x] Capability and record-filtering validation
- [x] Immutable audit and timeline integration
- [ ] Approve filing-season deadline configuration before operational use
- [ ] Perform external-OIDC authenticated browser smoke test in staging

## Final decision

No engineering release blockers were found. Merge PR #14 after review approval.
Before enabling tax deadlines for production operations, complete the two
operational checklist items above. Do not begin Sprint 5.2 as part of this RC.
