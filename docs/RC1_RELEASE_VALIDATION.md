# Client360 Epic 4 — RC1.2 Final Release Candidate Validation

**Validated application commit:** `integration/epic4` at `c011140`

**Draft PR:** #8, `integration/epic4` → `main`

**Validation date:** July 14, 2026

**Decision:** **Ready for staging/UAT; not yet approved for production or merge to `main`**

RC1.2 integrates the approved clean-database repair into `integration/epic4`. Automated application, migration, route, authorization, and service validation is green. Production approval remains conditional on the manual external-provider, identity-provider, backup/restore, visual, and production-scale gates below. PR #8 remains draft and unmerged.

## Overall release recommendation

- **Migration readiness:** ready. Both clean installation and current-`main` upgrade paths pass with one Alembic head.
- **Application readiness:** ready for staging and representative-user acceptance testing.
- **Production readiness:** conditional, pending the manual gates in this report.
- **PR #8 recommendation:** do not merge into `main` yet. Merge only after staging sign-off, production identity configuration, external-integration validation, and backup/restore rehearsal are documented and approved.

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

## RC1.1 clean-database repair

### Root cause

The original root revision, `da46d875eab7`, was a stamp-only baseline created after the five core tables had been provisioned through SQLAlchemy metadata. The later `54fd91b24da6` revision was another stamp-only baseline created after Tasks, Activities, Household Relationships, and Documents had also been provisioned outside Alembic. A new database therefore reached `5baa24cbdc65` without `people` or `source_contacts`, and later revisions would also have lacked the four second-baseline tables.

### Repair strategy

The two baseline revisions now contain the explicit DDL for the schemas they originally represented. Revision IDs, parent links, and the single-head lineage are unchanged. Databases already stamped beyond these revisions do not execute the added DDL, so their data and upgrade behavior are preserved. New databases now receive the missing tables through Alembic without `metadata.create_all()` or a manual prerequisite.

Application metadata was also aligned with the existing Portfolio and Sprint 4.1 migrations: custodian and registration foreign keys belong to `accounts`, and identity attribution belongs to `tasks`, not `import_jobs`.

### RC1.2 validation evidence

- Empty PostgreSQL database → `alembic upgrade head`: passed.
- Expected application tables: 45; actual: 45; missing/unexpected tables: none.
- Expected versus actual columns: no mismatches.
- `alembic downgrade base`: passed; only `alembic_version` remained.
- Second `alembic upgrade head`: passed.
- Current `main` schema stamped at `753c04edab33` → RC head: passed.
- Sentinel household/person data survived the existing-database upgrade.
- Exactly one Alembic head remains: `c410f4a1b2c3`.
- Full suite: 33 passed in 0.43 seconds, with one environment warning.
- Python compile checks for application, migrations, and tests: passed.
- Application startup: passed; 72 routes registered.
- Strict template parsing: 18 templates passed.
- Direct HTTP/authentication/authorization matrix: 45 cases passed.
- Microsoft mail repeated-sync check: one matched timeline row and one unmatched-review row after two syncs.
- Seeded route pass: 5.36 ms mean and 31.97 ms maximum on the small local dataset; not a load test.

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

- **Resolved in RC1.1 candidate:** clean database bootstrap now passes from base to head.
- **Medium:** two already-applied stamp-only baseline files now contain explicit DDL. Existing databases at later revisions skip that DDL, but reviewers should confirm no deployment process forcibly replays historical revisions against a populated schema.
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

1. Add the validated blank-database and existing-main upgrade paths to required CI so the repaired baselines cannot regress.
2. Configure required CI checks on PR #8 for tests, compilation, migration-head validation, and clean-database creation.
3. Deploy RC1 to staging with production-equivalent PostgreSQL, Python/OpenSSL, secrets, and managed OIDC; test administrator bootstrap and least-privilege role assignments.
4. Run controlled Microsoft mail, calendar, OneDrive, and SharePoint syncs against a non-production tenant, including pagination, throttling, token expiry, retries, deleted items, and unmatched-review workflows.
5. Validate sanitized Schwab exports, account/household mappings, beneficiaries, cost basis, and idempotent re-imports.
6. Perform visual, accessibility, and mobile-width review of the dashboard, Client Workspace, review queues, Relationship graph, and Portfolio views.
7. Test backup/restore and rollback on a production-sized clone; do not rely on destructive Alembic downgrade after accepting live RC1 data.
8. Establish performance budgets and observability for scheduler runs, Graph errors, queue depth, dashboard latency, audit growth, and import failures.

## Deployment checklist

- [ ] Record and verify a database backup; perform a restore rehearsal in an isolated environment.
- [ ] Confirm production is at Alembic revision `753c04edab33` and no unexpected schema drift exists.
- [ ] Configure a supported Python/OpenSSL runtime, production session secret, managed OIDC issuer/client settings, redirect URLs, and MFA policy.
- [ ] Review initial administrator bootstrap, role/capability composition, teams, assignments, and segregation of duties.
- [ ] Configure Microsoft Graph credentials, least-privilege consent, token storage, scheduler cadence, retry policy, and alerting.
- [ ] Validate sanitized Schwab column mappings and household/account assignment rules.
- [ ] Run migrations in staging with a production-sized database clone and record lock duration and total runtime.
- [ ] Complete visual, accessibility, and mobile-width review of primary workspaces and review queues.
- [ ] Freeze data-changing jobs during the production migration window and confirm rollback decision owners.
- [ ] Deploy application code only after the database migration completes successfully; then enable schedulers in a controlled sequence.

## Rollback strategy

1. Stop application workers and Microsoft/import schedulers to prevent new writes.
2. Prefer application rollback plus database restore to the pre-deployment backup if RC1 tables have accepted production writes.
3. Use Alembic downgrade only during a rehearsed pre-production rollback. Downgrading below RC1 drops feature data, and downgrading to base removes the entire Client360 schema.
4. Restore secrets and identity-provider configuration only through the approved secret-management process.
5. Verify restored Alembic revision, row counts, representative client records, document links, and timeline integrity before reopening access.

## Post-deployment verification checklist

- [ ] `/health` responds successfully and application startup logs contain no migration or reflection failures.
- [ ] Alembic reports exactly `c410f4a1b2c3` as the sole current head.
- [ ] Administrator login, MFA, logout, session expiration, and access-denied audit events behave correctly.
- [ ] Advisor access succeeds for an assigned client and fails for an unassigned client.
- [ ] Dashboard, search, all Client Workspace tabs, timeline, Relationship, and Portfolio views render with representative data.
- [ ] Microsoft mail, calendar, and document test syncs publish one deduplicated event and route unmatched items to review.
- [ ] Schwab test import is idempotent and preserves household totals and beneficiary data.
- [ ] Background schedulers execute once without duplicate jobs; queue depth and failures are observable.
- [ ] Audit events are written and remain immutable.
- [ ] Database connections, route latency, error rate, worker health, and storage growth remain within agreed thresholds.
- [ ] Backup monitoring and the next scheduled backup complete successfully.

## Release decision

Keep PR #8 draft and unmerged. RC1.2 is suitable for staging/UAT and its migration paths are release-ready, but production approval requires completion of the unchecked deployment gates. Once those gates are documented as passed, PR #8 can be marked ready and presented for explicit merge approval.
