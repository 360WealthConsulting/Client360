# ADR-012 — Business Planning Profile as narrow prospective persistence

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Business Owner Planning); Business Operations Owner
(Michael Shelton).

## Context
The D.12 pre-implementation audit proved that several business-planning facts had **no
authoritative home** anywhere in the schema: succession status, buy-sell status, continuity status,
key-person risk status, successor, emergency contact, valuation date/amount, and planning
notes/provenance. Only a `buy_sell_agreement` relationship *label* and an advisor-AI text nudge
existed. These facts had to live somewhere without duplicating existing domains or fabricating
history.

## Decision
Introduce **one narrowly scoped table**, `business_planning_profiles`:
- **one profile per business** (1:1, unique `business_id`);
- **controlled status vocabulary** (`unknown, not_started, in_progress, documented,
  review_needed, complete, not_applicable`) via CHECK constraints; controlled `source_type`
  (`advisor_entered, client_reported, document_derived`);
- **prospective-only** — **no fabricated backfill** (there is no source of truth to backfill
  from; backfilling would fabricate facts);
- it **must not** duplicate organization, ownership, insurance, tax, benefits, retirement, work,
  compliance, or annual-review data.

## Alternatives considered
1. **Reuse an existing table** (e.g. stuff succession into `organization_profiles` or a relationship
   detail). Rejected: those tables own different facts; overloading them would blur ownership and
   risk collisions.
2. **Store planning facts as free-text notes only.** Rejected: not queryable, no controlled status,
   no provenance; the firm needs structured status for planning.

## Reasons for the decision
A single, tightly-scoped, controlled-vocabulary table is the minimal honest persistence for facts
with no other home — and it keeps Business Owner Planning a composition layer that owns only this
one genuinely-new record.

## Consequences
### Positive consequences
- Structured, queryable succession/continuity facts with provenance and controlled status.
- No duplication of any existing domain; reversible migration.

### Negative consequences and tradeoffs
- **Incomplete historical coverage** (prospective only).
- Data is **advisor/client-reported** provenance, not authoritative source-of-record.
- If a dedicated **Succession domain** is later introduced, a future migration may move these
  facts (a superseding ADR would define it).

## Enforcement
- Migration `j0b1u2s3o4w5_business_owner_planning.py` (table + CHECK constraints + unique index,
  no backfill); declared schema `app/database/business_planning_tables.py` registered in
  `app/database/schema.py`.
- Vocabulary + lifecycle enforced in `app/services/business_owner.py`
  (`upsert_planning_profile`, `PLANNING_STATUS_VOCAB`, `SOURCE_VOCAB`).
- Tests: `tests/test_business_owner.py` (`test_planning_profile_lifecycle_and_vocab_and_timeline`).

## Exceptions
None currently approved. No backfill was performed.

## Revisit conditions
Introduction of a dedicated Succession/Continuity domain, or a need for historical versioning of
planning facts, would justify a superseding ADR and migration.

## References
- migration `j0b1u2s3o4w5_business_owner_planning.py`; `app/database/business_planning_tables.py`
- `app/services/business_owner.py`; `docs/PLATFORM_ARCHITECTURE.md` §17, §5
- `tests/test_business_owner.py`, `docs/PHASE_D12_BUSINESS_OWNER_PLANNING_WORKSPACE.md`
