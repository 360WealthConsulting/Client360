# D7 partial-provenance, document-corroborated attribution — frozen 29-row batch

**APPLY IS NOT AUTHORIZED.** These files are a reviewed, frozen record. Nothing here has been
applied to production, and running the apply requires its own authorization.

## Why a third runner exists

| runner | Drake source contacts | stored provenance coverage | extra requirement |
|---|---|---|---|
| `apply_d7_attribution_batch1` | **2 or more** | complete | — |
| `apply_d7_document_verified_attribution` | 1 or more | **complete** | document corroboration |
| this one | 1 or more | **deliberately incomplete** | document corroboration |

These 29 rows satisfy neither of the first two. Their entities' stored provenance names only *some*
of the contacts the identifier appears on — typically the first filing year, with later years never
added. Coverage runs from 1 of 5 up to 6 of 7.

The Drake provenance gap is real and this batch does not pretend otherwise. What closes it is
evidence the provenance model does not hold: **229 documents already filed under the proposed
entities**, each carrying a taxpayer identifier that derives to the same hash with the entity's own
name printed beside it, 1,653 corroborating occurrences in total. For 27 of the 29, a corroborating
document is dated to the very year the provenance is missing.

## The shortcut this runner refuses to take

Writing the missing contact ids into `relationship_entities.details` would make the gap disappear and
let the stricter runner accept every one of these rows. **It does not do that.**
`relationship_entities` is first in its forbidden list and is fingerprinted before and after the
writes; a test asserts no `INSERT`/`UPDATE` against it exists in the source, and another asserts the
table is byte-identical after an apply.

Backfilling provenance would manufacture the corroboration rather than record it, and would leave the
stored provenance asserting a Drake corroboration that Drake never supplied. The gap stays visible in
the data, and the document evidence is recorded beside it — in the audit metadata and in the rollback
receipt — as what it is: supplemental authorization.

## What is held back

Nine rows are excluded and the runner refuses any manifest containing them:

```
531  533  552  555  563  578  589  620  637
```

**Three carry contradictory document evidence** — an identifier beside the entity's own name that
does *not* derive to the expected hash: 555 `1 Shabby Chic LLC`, 578 `Amerasia LLC`,
620 `Homes by Amy LLC`. Four have no documents at all under their entity. One,
552 `Annette Smith Estate`, has person links on its missing contacts. One has no sufficient evidence.

A parametrised test asserts each of the nine is refused by the manifest gate.

DBI 535 `mignard company llc` is also absent: it targets entity 52, one of the three entities that
would end up holding two distinct identifiers. That is why this batch is 29 and not 30, and why the
partial-provenance population is 38 rather than the 39 an earlier count reported.

## The batch

| | |
|---|---|
| rows | 29 |
| distinct target entities | 29 (one-to-one) |
| source contacts | 110 |
| missing contacts (the gap) | 63 |
| authorizing documents | 229 |
| corroborating occurrences | 1,653 |
| recorded contradictions | **0** |

Coverage before documents: 1/2 ×4, 1/3 ×9, 2/3 ×1, 2/4 ×1, 3/4 ×4, 3/5 ×9, 6/7 ×1.

Note that the links follow the **identifier**, not the entity's partial provenance: all 110 contacts
are linked, including the 63 the stored provenance never named. That is the point of the batch.

## Trust is machine, and there is no approver

`identifier_verified` / `machine` / `drake_entity_provenance`, with `confirmed_by_user_id` NULL and
the audit actor NULL. The document review was performed by software, not by a person.

## Evidence rules

Imported from the deployed `apply_d7_document_verified_attribution` rather than restated, so the two
cannot drift apart. An identifier token may not sit inside a longer numeric run, and must sit within
**60 normalised characters** of the entity's own name. Both numbers were measured from the real
corpus. An identifier inside that window that does not derive to the frozen hash is a contradiction
and aborts the whole batch. Unreadable evidence is refused, never treated as absent. No replacement
document is ever searched for.

## Expected write shape, if an apply is ever authorized

```
drake_business_identity   UPDATE   29
drake_business_identity   INSERT    0
entity_source_links       INSERT  110
audit_events              INSERT   29   (drake.entity_attribution_machine, actor_user_id NULL)

relationship_entities · drake_identity · person_source_links · people · source_contacts
drake_client_returns · documents · document_ocr · drake_identity_match_candidates          0
```

## Hashes

```
d7_partial_doc_batch_manifest.csv   6df4bc90a1ffc5ee8ea7e2a14d5bfa40d86a158895a855806f585e25b261d08a
d7_partial_doc_batch_manifest.json  1ef7367cd41f305c8215abf1ec4fd9974ac334ae3ad1ce6e68601854b8255ffb
d7_partial_doc_batch_summary.json   245c78d7389e3c82c97dad414c7283717985d08304afc8eda4ac6a77547b7e71

plan digest          162155d553791a548410842b0e46df6071cece0a91b5f4e1f54345a8e1739feb
confirmation phrase  APPLY-D7-PARTIALDOC-29
```

The plan digest covers the **authorizing document ids** as well as the rows, so swapping which
documents justify a row changes the plan rather than passing silently. A test pins that.

The census this batch derives from is pinned inside the sidecar:

```
review          3dacf83fe8474b80a518a8db5d202159f7da383a6bd51e7aafbe64644d8666ef
summary         92c0777d032be0d83365e2dbed6eac7b6fc89190c7b1e5207db4a22fbfd92fe3
document_ready  b7993313e4b25b04cfec28781369963c4d2b671c7c03b30d991bfc2d729a5428
hold            ab5f3ce3b77e0fab44da803b8782cdfd22090ba7314a0c74c073b81d54ef9c0f
```

**No raw taxpayer identifier appears in any of these files.** Identifiers are present only as derived
hashes.

## State at freeze

| | |
|---|---|
| production database | `client360` |
| release head | `c96af7348d1e1e1cdd739da41d3e0188521e8f9c` |
| Alembic | `drake03` (no migration — this batch needs none) |
| `drake_business_identity` | 150 total, 75 attributed, 75 unattributed |
| `entity_source_links` | 190 |
| machine attribution audits | 47 |

## What remains after this batch

Of the 75 unattributed identities, applying these 29 would leave 46: 25 single-contact rows without
document corroboration, the 9 held here, 6 across three multi-identifier entities, one contested
between two valid entities, and five that no entity can validly own.
