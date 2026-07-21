# ADR-016 — Linear migration chain and declared-schema registration

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (each domain introducing schema).

## Context
Schema evolves through Alembic. Parallel migration heads, unregistered declared tables, or
fabricating data during migration would produce environment drift, ambiguous upgrade paths, and
integrity problems. The platform needs a predictable, reversible, single-headed migration chain.

## Decision
- The migration chain **must** be **linear** with **exactly one current Alembic head**; parallel
  heads **must not** be introduced silently (a deliberate merge migration is required if branches
  ever occur).
- Each domain migration **owns** its tables and **seeds** its capabilities idempotently.
- Declared-schema modules (`app/database/*_tables.py`) **must** be **registered** via
  `define_*_tables(metadata)` in `app/database/schema.py`; reflected tables used at runtime are
  bound in `app/db.py`.
- Migrations **must** be reversible (upgrade/downgrade); downgrade removes the migration's
  tables/indexes and capabilities.
- Migrations **must not** backfill fabricated data — where no source of truth exists, the migration
  is **prospective-only** (e.g. `business_planning_profiles`).
- **Migration squashing is prohibited** without a separately approved migration strategy.

## Alternatives considered
1. **Allow parallel heads and merge later.** Rejected: ambiguous upgrade order and drift; a single
   head is simpler and enforceable.
2. **Skip declared-schema registration (rely on reflection only).** Rejected: loses a
   documented/typed schema surface and the registration test that guards it.

## Reasons for the decision
A single-headed, reversible, registered schema chain is predictable across environments and
testable, and prospective-only migrations avoid fabricating history (ADR-015).

## Consequences
### Positive consequences
- Unambiguous upgrade path; reversible migrations; registered declared schema.
- New tables are discoverable and test-guarded.

### Negative consequences and tradeoffs
- Contributors must rebase onto the current head rather than branch freely.
- 57 migrations and growing; squashing is disallowed without an approved strategy.

## Enforcement
- Single head `j0b1u2s3o4w5` (verified via Alembic `ScriptDirectory`):
  `tests/test_platform_architecture.py::test_migration_head_matches_manifest_and_is_single`.
- Declared-schema registration verified:
  `tests/test_platform_architecture.py::test_declared_schema_modules_registered`.
- Capability seeding per migration (e.g. `i9a1n2r3e4v5`, `j0b1u2s3o4w5`).

## Exceptions
None currently approved.

## Revisit conditions
A future performance or maintenance need to squash migrations requires a separately approved
migration strategy documented in a new ADR.

## References
- `app/database/schema.py`, `app/db.py`, `migrations/versions/*`
- `docs/PLATFORM_ARCHITECTURE.md` §21 (Database and migration architecture)
- `docs/DATABASE.md`, `tests/test_platform_architecture.py`
