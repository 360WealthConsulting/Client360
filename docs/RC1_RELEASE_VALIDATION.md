# Client360 Epic 4 — Release Candidate 1 Validation

**Candidate:** `integration/epic4` at `bd1e3c12c9688f62a2c3c2daabaeafb066185e9c`

**Draft PR:** #8, `integration/epic4` → `main`

**Validation date:** July 13, 2026

**Decision:** **Not ready for Release 1.0 or merge to `main`**

RC1 is stable when upgrading the existing `main` database and its application and domain checks pass. It is not yet release-ready because a truly empty database cannot be created by running the Alembic migration chain. No merge was performed.

## Validation environment and scope

- Python 3.9 virtual environment and local PostgreSQL.
- Disposable upgrade database initialized from the current `main` schema, stamped at `753c04edab33`, then migrated through the RC1 head.
- Separate, truly empty PostgreSQL database used to test `alembic upgrade head` from revision zero.
- Seeded administrator and advisor sessions used for direct ASGI HTTP checks.
- Microsoft Graph mail responses were simulated; calendar and document acquisition and processing were exercised by the automated suite.
- No production credentials, production data, or merge operations were used.

## Passed items

### Application and routes

- Application startup and registration of all 72 FastAPI routes passed.
- 45 direct HTTP cases passed, including the dashboard, people, households, tasks, activities, match review, search page and API, Microsoft status and review pages, Relationship and Portfolio search, identity administration, audit viewer, and session API.
- All nine Client Workspace tabs rendered successfully: Overview, Timeline, Tasks, Documents, Notes, Activities, Calendar, Relationships, and Portfolio.
- Person tasks, documents, activities, and timeline views passed.
- All 18 Jinja templates parsed successfully.
- Python compilation passed for `app` and `tests`.

### Authentication, authorization, and audit

- Public health access passed.
- Unauthenticated browser requests redirect to login; unauthenticated API requests return 401.
- Cross-origin state-changing requests are rejected with 403.
- An administrator can access firm-wide and administrative views.
- An advisor can open an assigned person and receives 403 for an unassigned person and for firm-wide Relationship, Portfolio, and People collections.
- Relationship creation produced both the relationship and its timeline event.
- Capability checks, record scope, immutable audit records, session handling, bootstrap rules, and provider-neutral OIDC behavior passed their automated tests.
- The only authenticated routes without a business capability rule are logout and the current-session endpoint; this is intentional because both operate only on the caller's session.

### Microsoft 365 intelligence

- Mail matching uses normalized email. Two identical simulated Graph syncs produced one timeline event and one unmatched-review item, confirming deduplication for both paths.
- Calendar tests passed for normalized matching, ambiguous-email exclusion, matched timeline publication, unmatched review, cancellation handling, and Microsoft event-ID deduplication.
- Document tests passed for matching rules, ambiguity handling, Drive Item-ID deduplication, timeline publication, and unmatched review.
- Microsoft mail, calendar, and document review routes rendered with an authenticated administrator. The mail view correctly redirected when no Microsoft account was connected.

### Relationship and portfolio intelligence

- Relationship CRUD behavior, graph generation, household expansion, timeline publication, search, and advisor recommendation rules passed.
- Portfolio normalization/import behavior, idempotency, household aggregation, timeline publication, recommendation rules, and search passed.
- Portfolio and Relationship tabs rendered inside the combined Client Workspace.

### Search, timeline, and dashboards

- Global search page and API passed with administrator scope.
- Relationship and Portfolio search passed with administrator scope and failed closed for a record-limited advisor.
- Timeline rendering and relationship timeline publication passed.
- Main dashboard and statistics API passed; the combined Client Workspace retained dashboard, relationship, and portfolio surfaces after integration.

### Existing database migration path

- Exactly one Alembic head exists: `c410f4a1b2c3`.
- Existing-`main` lineage is linear:

```text
753c04edab33
  -> 0e62f932fe88
  -> 9f34ec28b780
  -> 27bc61f29a81
  -> 5bd72a4cc901
  -> b16c8d9e4f20
  -> c410f4a1b2c3 (head)
```

- Existing `main` schema → RC1 head passed.
- RC1 head → `753c04edab33` → RC1 head passed.
- On the small seeded database, the six-revision downgrade took 0.25 seconds and re-upgrade took 0.32 seconds.

### Automated tests

```text
33 passed, 1 warning in 0.42s
```

Coverage includes identity/security, integration security, Microsoft Calendar, Microsoft Documents, Relationship Intelligence, and Portfolio Intelligence. The additional mail synchronization scenario was run as an RC validation check against PostgreSQL.

## Release blocker

### Alembic cannot create a clean database

Running `alembic upgrade head` against an empty PostgreSQL database fails at `5baa24cbdc65` because the baseline revision does not create the original Client360 tables. The first dependent migration attempts to reference `people` and `source_contacts`, which do not exist.

This prevents a reliable new-environment bootstrap and weakens disaster-recovery confidence. It does not affect the verified upgrade from the current `main` database, but it must be resolved and retested before Release 1.0.

Recommended resolution: add an explicit, versioned bootstrap mechanism for the baseline schema without rewriting revisions already deployed to `main`. Document and test both supported paths:

1. New database → full schema at RC head.
2. Existing production database at `753c04edab33` → RC head.

## Warnings and known issues

- Live Microsoft OAuth, token refresh, Graph throttling/pagination, SharePoint permissions, and real tenant data were not validated because release credentials were not provided. The provider-independent processing paths passed with controlled responses.
- Schwab validation used automated fixtures and seeded portfolio data, not a sanitized representative export from the firm.
- Managed OIDC login, MFA enforcement, recovery policy, session-cookie production settings, and initial administrator operations still require staging validation with the selected identity provider.
- No browser-based visual regression or accessibility pass was performed. Server rendering and strict template parsing passed.
- Global Relationship and Portfolio searches intentionally require `record.read_all`; assigned advisors cannot use those global surfaces until assignment-filtered queries are implemented.
- The dedicated `/timeline/person/{id}` view is a firm-wide collection under the current policy. Assigned advisors can see the same timeline in their authorized Client Workspace, but the dedicated view is limited to users with firm-wide scope.
- Python emits `urllib3`'s `NotOpenSSLWarning` because the local interpreter uses LibreSSL 2.8.3. Production should use a supported OpenSSL runtime.
- PR #8 has no configured CI status checks. Local validation is therefore the only automated release evidence currently attached to RC1.

## Breaking changes

- Authentication is now required for all non-public application routes.
- Firm-wide collections and administrative functions now require explicit capabilities and, where applicable, `record.read_all` or `record.write_all`.
- New identity, team, role, assignment, audit, Microsoft document, relationship, and portfolio tables are introduced.
- Existing staff may lose access until user identity mapping, roles, teams, and assignments are seeded and reviewed.
- Client Workspace navigation and dashboard content expand to include Calendar, Microsoft Documents, Relationships, and Portfolio data.

## Migration risks

- **High:** clean database bootstrap is broken, as described above.
- **High:** downgrading drops RC1 tables and their data. A downgrade is technically successful but is destructive; backup/restore is the safer rollback strategy after production writes begin.
- **Medium:** role/capability seeds and first-user bootstrap require a controlled deployment sequence to avoid access lockout or excessive privilege.
- **Medium:** migration timing was measured only on a small local database. Lock duration and index creation must be measured on a production-sized clone.
- **Medium:** integration tables can grow quickly. Timeline, audit, unmatched-review, position snapshot, and document metadata retention need production sizing and monitoring.

## Performance observations

- The 45-case seeded HTTP pass averaged 6.18 ms server-side; the slowest request was the Client Workspace overview at 37.57 ms.
- The local migration cycle completed in under one second for both directions combined.
- These numbers demonstrate absence of obvious small-data regressions only. They are not concurrency, throughput, Graph-volume, or production-data benchmarks.
- Before Release 1.0, load-test global search, Client Workspace aggregation, relationship graph traversal, household portfolio rollups, dashboard metrics, and large unmatched-review queues with representative volumes.

## Recommendations before Release 1.0

1. Resolve and automate the blank-database migration path; repeat fresh install, existing upgrade, downgrade, and backup/restore validation.
2. Configure required CI checks on PR #8 for tests, compilation, migration-head validation, and clean-database creation.
3. Deploy RC1 to staging with production-equivalent PostgreSQL, Python/OpenSSL, secrets, and managed OIDC; test administrator bootstrap and least-privilege role assignments.
4. Run controlled Microsoft mail, calendar, OneDrive, and SharePoint syncs against a non-production tenant, including pagination, throttling, token expiry, retries, deleted items, and unmatched-review workflows.
5. Validate sanitized Schwab exports, account/household mappings, beneficiaries, cost basis, and idempotent re-imports.
6. Perform visual, accessibility, and mobile-width review of the dashboard, Client Workspace, review queues, Relationship graph, and Portfolio views.
7. Test backup/restore and rollback on a production-sized clone; do not rely on destructive Alembic downgrade after accepting live RC1 data.
8. Establish performance budgets and observability for scheduler runs, Graph errors, queue depth, dashboard latency, audit growth, and import failures.

## Release decision

Keep PR #8 in draft and do not merge `integration/epic4` into `main`. RC1 may proceed to staging after the blank-database strategy is approved, but it should not be designated Release 1.0 until the migration blocker is fixed and the external-provider and production-scale checks above are complete.
