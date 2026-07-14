# Epic 4 Prerequisite Integration — Release Readiness

Status: RC1.2 is ready for staging/UAT; not yet approved for merge or production deployment.

## RC1.2 finalization

The approved RC1.1 migration repair was merged into `integration/epic4` at `c011140`. Draft PR #9 is superseded by the integrated RC branch. PR #8 remains the only candidate for eventual promotion to `main` and must remain unmerged until the manual production gates are approved.

Final automated evidence:

- Clean database base → head, head → base, and base → head: passed.
- Current `main` revision → RC head with sentinel client preservation: passed.
- Expected tables and columns: 45/45 tables, no mismatches.
- Alembic heads: one (`c410f4a1b2c3`).
- Full suite: 33 passed.
- Python compilation, application startup, 72 routes, and 18 templates: passed.
- Direct HTTP/authentication/authorization validation: 45 passed.
- Microsoft mail repeat-sync deduplication and unmatched review: passed.

Release recommendation: proceed to staging/UAT. Do not merge PR #8 until managed OIDC/MFA, live Microsoft test-tenant flows, representative Schwab imports, production-sized migration timing, backup/restore, and UI/accessibility review have passed.

## Integrated scope

The `integration/epic4` branch integrates the work represented by draft PRs #2 through #7 while preserving each sprint as a distinct commit:

1. Microsoft Calendar Intelligence
2. Microsoft Document Intelligence
3. Relationship Intelligence
4. Schwab Portfolio Intelligence
5. Epic 4 Practice Management technical design
6. Firm Identity, Teams, Capability Authorization, and Audit

The source draft branches were not rewritten, merged, or closed. Paused Sprint 4.2 work remains separately safeguarded and is not included.

## Migration lineage

The integrated Alembic history has exactly one head:

```text
753c04edab33
  -> 0e62f932fe88
  -> 9f34ec28b780
  -> 27bc61f29a81
  -> 5bd72a4cc901
  -> b16c8d9e4f20
  -> c410f4a1b2c3 (head)
```

RC1.1 repairs the historical stamp-only baselines with explicit Alembic DDL. Validation now covers both a truly empty PostgreSQL database from base to head and a current-`main` database stamped at `753c04edab33` upgraded to the integration head. No manual schema initialization is required by the repaired migration path.

## Automated evidence

- Alembic heads: one (`c410f4a1b2c3`).
- Integrated migration upgrade: passed.
- Integrated migration downgrade and re-upgrade: passed.
- Empty database upgrade to head, downgrade to base, and second upgrade: passed on the RC1.1 repair branch.
- All 45 application tables and columns matched application metadata after clean migration.
- Current-`main` upgrade preserved seeded sentinel client data.
- Full test suite: 33 passed.
- Python 3.9 compile checks for application, migrations, and tests: passed.
- Application import and FastAPI route registration: passed; 72 routes validated.
- Strict parsing of all 18 Jinja templates: passed.
- Identity seed validation: 15 capabilities, four roles, and five teams.
- Audit-event database immutability: update rejection confirmed.
- Security middleware registration: confirmed.
- Git whitespace validation: passed.

The environment emits an existing urllib3 warning because the system Python is linked to LibreSSL 2.8.3 rather than OpenSSL 1.1.1 or newer. It does not fail the suite, but the production runtime should use a supported OpenSSL build.

## Integration defects resolved

- Preserved Calendar, Microsoft Documents, Relationships, and Portfolio tabs in the Client Workspace instead of allowing later branches to overwrite earlier navigation and content.
- Preserved every dashboard navigation entry and combined portfolio metrics with existing operating metrics.
- Combined relationship-aware and portfolio-aware advisor recommendations with backward-compatible service calls.
- Registered Relationship, Portfolio, Microsoft Document, and identity routes together.
- Retained all corresponding timeline display event types.
- Corrected Python 3.9-incompatible FastAPI Portfolio route annotations discovered during application startup validation.
- Added capability and record-scope enforcement for Relationship and Portfolio global surfaces. Global searches and imports now fail closed without explicit firm-wide read/write scope.
- Added record-scope enforcement for relationship deactivation through its associated person.
- Replaced two historical stamp-only baseline bodies with their original explicit core-schema DDL so Alembic can build a new database without `metadata.create_all()`.
- Corrected application metadata placement for Portfolio account foreign keys and Sprint 4.1 task attribution columns.

## Manual review required before merge

1. Review the complete combined Client Workspace and dashboard visually with representative client data.
2. Configure and test the managed OIDC provider, MFA requirements, recovery policy, production session secret, and initial administrator bootstrap.
3. Run controlled Microsoft Calendar and SharePoint/OneDrive synchronizations with a non-production Microsoft tenant; verify unmatched review and original-file links.
4. Validate relationship types, inverse labels, primary-household behavior, and AI-inferred provenance with blended-family and professional-network examples.
5. Validate Schwab CSV header mappings, household assignment, beneficiary rules, allocation thresholds, and timeline events using sanitized representative exports.
6. Review seeded role compositions, privileged capabilities, assignment policy, audit access, retention, and segregation of duties.
7. Run backup/restore rehearsal and migration timing against a production-sized database before scheduling deployment.

## Known limitations and decisions

- Browser-level HTTP smoke tests were not added because the optional `httpx` test dependency is not installed. Application import, route registration, templates, middleware, database behavior, and service tests are validated directly.
- Global Relationship and Portfolio searches intentionally require `record.read_all` until assignment-filtered query services are implemented. This prevents record leakage at the cost of limiting those views to privileged staff.
- Microsoft and custodian integrations require external credentials and representative test data for final operational validation.
- The integration branch is a consolidation vehicle. A single draft PR should supersede PRs #2–#7 only after stakeholder review; no source draft should be closed automatically.

## Recommendation

Open one draft PR from `integration/epic4` to `main` and keep it unmerged. After approval of this integration baseline, rebase the paused Sprint 4.2 branch onto the integration branch (or onto `main` after the integration PR is eventually merged) so its first migration follows `c410f4a1b2c3`.
