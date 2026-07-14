# Client360 Release 0.9 — Epic 4 Foundation

Release date: July 14, 2026

## Overview

Release 0.9 establishes Client360 as an integrated client, relationship,
Microsoft 365, portfolio, identity, and audit platform. It consolidates the
prerequisite intelligence work and the first Epic 4 security foundation into a
single production-candidate code line.

## Features delivered

- Outlook mail and Microsoft Calendar synchronization with normalized-email
  matching, timeline publication, deduplication, and unmatched review.
- SharePoint and OneDrive metadata synchronization with configurable matching,
  original-file links, deduplication, and Client Workspace integration.
- Relationship Intelligence for family, household, professional, business,
  trust, estate, beneficiary, emergency, and referral relationships.
- Schwab Portfolio Intelligence for accounts, registrations, positions,
  holdings, cash, lots, beneficiaries, performance, billing, and household
  rollups.
- Unified Client Workspace tabs for timeline, tasks, documents, activities,
  calendar, relationships, and portfolio information.
- Search, dashboards, timeline events, and advisor recommendations across the
  integrated intelligence layers.
- Firm identity, teams, capability-composed roles, record assignments,
  record-level authorization, secure sessions, and immutable audit events.
- Provider/adapter foundations for vendor-independent identity and portfolio
  acquisition.

## Database and migrations

- Schema version: Release 0.9
- Alembic head: `c410f4a1b2c3`
- Application tables: 45
- Migration lineage: one head
- Supported paths:
  - empty PostgreSQL database → Release 0.9 head;
  - previous `main` head `753c04edab33` → Release 0.9 head.

The historical stamp-only baselines now contain explicit Alembic DDL. No
production migration depends on `metadata.create_all()` or a hidden manual
schema prerequisite.

## Validation

- 33 automated tests passed.
- Empty-database upgrade, downgrade-to-base, and re-upgrade passed.
- Upgrade from the previous `main` schema preserved sentinel client data.
- Python compilation and application startup passed.
- 72 routes and 18 templates registered and parsed.
- 45 direct HTTP, authentication, authorization, and record-scope cases passed.
- Microsoft mail repeat-sync deduplication and unmatched review passed.

## Documentation

- `MICROSOFT_CALENDAR_SYNC.md`
- `MICROSOFT_DOCUMENT_SYNC.md`
- `RELATIONSHIP_ENGINE.md`
- `SCHWAB_PORTFOLIO_ENGINE.md`
- `IDENTITY_AUTHORIZATION_AUDIT.md`
- `EPIC_4_PRACTICE_MANAGEMENT_PLATFORM.md`
- `EPIC4_INTEGRATION_RELEASE_READINESS.md`
- `RC1_RELEASE_VALIDATION.md`

## Known limitations

- Managed OIDC/MFA requires final production-environment validation.
- Microsoft integrations require controlled live-tenant validation for token
  renewal, throttling, pagination, permissions, and retry behavior.
- Schwab mappings require validation with representative sanitized firm exports.
- Production-sized migration timing, backup/restore, visual/accessibility, and
  operational observability rehearsals remain deployment gates.
- The development Python runtime emits a LibreSSL compatibility warning from
  urllib3; production should use a supported OpenSSL runtime.

## Remaining roadmap

- Complete Release 1.0 staging and operational readiness gates.
- Sprint 4.2 Operational Work Management Platform.
- Workflow automation and tax-practice operations.
- Tax, QuickBooks, additional custodian, and live Schwab integrations.
- AI meeting preparation, client briefs, and planning intelligence.

Sprint 4.2 is not included in Release 0.9 and remains paused pending explicit
approval after release finalization.
