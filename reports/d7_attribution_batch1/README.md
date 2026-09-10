# D7 machine attribution — Batch 1 frozen manifest

**APPLY IS NOT AUTHORIZED.** These files are a reviewed, frozen record. Nothing here has been
applied to production, and running the apply requires its own authorization.

## What this batch does

D7 Phase C relocated 122 mis-filed identities out of `drake_identity` into
`drake_business_identity` as **typed but unattributed** rows — subject known, owner not yet decided.
Deciding *which* entity owns an identifier belongs to `drake_machine_attribution`, which binds an
identifier to an entity only when that entity's **own stored provenance already references the
identifier's source records**. A name, an address or a contact point never satisfies it.

Batch 1 is the first unattended production batch of those attributions.

## Population and policy

Of the 121 currently unattributed rows, 76 have a unique provenance-valid owner whose provenance
covers the identifier's **complete** source-contact set. This batch is deliberately narrower than
that:

| | |
|---|---|
| unattributed `drake_business_identity` | 121 |
| complete provenance, unique target | 76 |
| held — exactly one source contact | 36 |
| multi-contact before multi-identifier exclusion | 40 |
| excluded — multi-identifier entity | 1 |
| **frozen** | **39** |

**One-contact rows are held, not rejected.** A single-contact identifier satisfies "complete
coverage" trivially, because one contact *is* the complete set. Those 36 rows are valid under the
deployed service contract and are simply outside this more conservative first batch. They carry
`HELD_SINGLE_SOURCE_CONTACT_FIRST_BATCH_POLICY` in the exclusions file and are a later review lane.

**Three entities would each end up holding two distinct identifiers**, in every case the same
business name across adjacent, non-overlapping year ranges: entity 20 `ARRINGTON PAVING COMPANY
INC`, entity 29 `BONDED PERMANENT JEWELRY`, entity 52 `Mignard Company LLC`. One business with two
EINs in consecutive years suggests a re-key or a data-entry error in one of them, and attributing
both would enshrine the split rather than repair it. All six rows are excluded — including
`Mignard`'s, whose sibling sits in the partial-provenance bucket, because a sibling held in another
bucket still leaves the entity holding two.

The remaining holds are 39 partial-provenance rows, one row contested between two valid entities
(DBI 529, entities 23 and 159), and five rows no entity can validly own.

The exclusions file lists 87 rows covering 82 distinct identities: five rows legitimately belong to
two categories at once and are listed under both rather than silently assigned to one.
39 + 82 = 121.

## Complete provenance

Coverage is not an intersection. The service accepts an entity whose provenance references *any one*
of the identifier's contacts; this batch requires the entity's provenance to account for **every**
contact the identifier has. Provenance stored as `source_record_ids` or `drake_return_ids` is
resolved back to the contacts it denotes, so a complete set expressed in any canonical form counts
as complete.

## The batch

| | |
|---|---|
| rows | 39 |
| distinct target entities | 39 (one-to-one) |
| source contacts | 112 |
| `business_entity` | 39 |
| contacts per row | 2 x 15, 3 x 18, 4 x 2, 5 x 4 |
| return types | 1120S 19, 1065 16, 990 2, 1120 2 |
| provenance forms | `details.source_contact_ids` 38, `details.canonical_repair.source_contact_ids` 1 |

Every row was put through the deployed `attribute_entity_by_provenance` inside a rolled-back
savepoint, and the whole batch was then applied **together** in one savepoint and rolled back, so
the batch interaction was measured rather than assumed. Inside that savepoint: 39/39 passed, the DBI
row count did not move, attributed went 29 → 68, entity source links 71 → 183, machine-attribution
audits 1 → 40, and all 39 rows carried `identifier_verified` / `machine` / `drake_entity_provenance`.

## Expected write shape, if an apply is ever authorized

```
drake_business_identity   UPDATE  39   (entity set, trust triple filled by COALESCE)
drake_business_identity   INSERT   0
entity_source_links       INSERT 112
audit_events              INSERT  39   (drake.entity_attribution_machine, actor_user_id NULL)

drake_identity · person_source_links · people · relationship_entities
source_contacts · drake_client_returns · documents · drake_identity_match_candidates      0
```

## Hashes

```
d7_machine_attribution_batch1_manifest.csv    2cbfadecb448ce3be60abc8bcbe83aa181f3c347a8552d5a8f887623927bc6b4
d7_machine_attribution_batch1_manifest.json   1a55d3dd50ca1c08712c84f849583e73282b9de8db5dc2919e8caeee12de219c
d7_machine_attribution_batch1_exclusions.csv  282d686d56e09d124f30561e078a48365047a72762ae584d6f06484a945a49f2
d7_machine_attribution_batch1_summary.json    49f8dbc66e54da4d59da9d2f92194e5d22d1c6d8a45f6d9e185acdabe7c366eb

plan digest          2b4066bdbe60650fbdefe763b497b5f7c2af7659c47e3e6a3f18a99b501894ec
confirmation phrase  APPLY-D7-ATTRIB-BATCH1-39
```

These files are pure LF and SHA-pinned, so `.gitattributes` marks this directory `-text` — the same
protection the strict-safe ownership, document-filing and D7 Phase C manifests carry. Without it
`core.autocrlf` would hand a Windows checkout CRLF and the reviewed hashes could never be satisfied
again.

The sidecar JSON freezes 36 fields per row, including the full-row hash of each identity and target
entity, per-contact and per-return hashes, the entity's exact stored provenance values, the resolved
provenance contact set, and the proof that coverage is complete. It also pins the CSV's own hash.

## State at freeze

| | |
|---|---|
| production database | `client360` |
| deployed head | `c60f33b4da9988aeccdf58b4b05f3aac9c118d6b` |
| Alembic | `drake03` (no migration — this batch needs none) |
| `drake_business_identity` | 150 total, 29 attributed, 121 unattributed |
| `entity_source_links` | 71 |

## Case 7379 is already closed

`HANDPICKED WINE WAREHOUSE LLC` was attributed separately under its own authorization: DBI 532 to
entity 153, with links for source contacts 8617 and 9805 and one audit event. It is already
attributed and therefore **absent from the 121-row population by construction**. It does not appear
in this manifest.
