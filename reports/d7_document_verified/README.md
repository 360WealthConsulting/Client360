# D7 document-corroborated attribution — frozen 7-row batch

**APPLY IS NOT AUTHORIZED.** These files are a reviewed, frozen record. Nothing here has been
applied to production, and running the apply requires its own authorization.

## Why this batch is separate from Batch 1

`apply_d7_attribution_batch1` requires at least **two** Drake source contacts. An identifier that
appears on two or more filings lets the entity's stored provenance and the identifier's contact set
check each other; with one contact that check does not exist, and Batch 1 rightly refuses.

These seven rows have exactly one contact each. They are admissible on a different basis: for every
one of them, a document already filed under the proposed entity — its own 1120S, its e-file
authorisation, its Form 940 or its W-2s — carries a taxpayer identifier that derives to the same
hash, with the entity's own name printed beside it. That is corroboration from outside Drake.

So `apply_d7_document_verified_attribution` lowers the contact floor to one and makes the document
evidence **mandatory** in exchange. A single-contact row is never accepted merely because canonical
provenance covers its one contact. Batch 1's policy is untouched, and this runner refuses to load
Batch 1's manifest.

## Trust is machine, and there is no approver

| | |
|---|---|
| `trust_level` | `identifier_verified` |
| `confirmation_source` | `machine` |
| `evidence_method` | `drake_entity_provenance` |
| `confirmed_by_user_id` | **NULL** |
| audit `actor_user_id` | **NULL** |

The document review that corroborates these rows **was performed by software, not by a person**.
Recording `human_approved` would misstate who examined the evidence, so the manifest records machine
trust throughout and the policy block carries `human_approver: null`.

## The batch

| | |
|---|---|
| rows | 7 |
| distinct target entities | 7 (one-to-one) |
| source contacts | 7 (one each) |
| corroborating documents re-verified | 32 |
| `business_entity` | 7 |
| return type / year | 1120S, tax year 2021 |

| DBI | entity | corroborating documents | contemporaneous |
|---|---|---|---|
| 523 | 156 Henry Tobacco Inc | 2 | no (2020) |
| 541 | 149 DURGA SHAKTI LLC | 9 | **yes (2021)** |
| 562 | 157 A SHEAR EXPERIENCE INC | 2 | no (2019) |
| 582 | 150 PRECISION WINDOWS AND DOORS INC | 2 | no (2020) |
| 619 | 154 AKASH INC | 4 | no (2018) |
| 624 | 148 TD&K ENTERPRISES INC | 5 | **yes (2021)** |
| 635 | 151 GHFN PROPERTY INC | 8 | no (2020) |

Five rows are corroborated by an adjacent year rather than 2021. An employer identification number
does not change between years, and these are the entity's own federal filings with its name printed
beside the number, so they establish that the identifier denotes that business.

## Two evidence rules, measured from the documents

**An identifier token may not sit inside a longer numeric run.** Depreciation schedules contain
strings like `DUMP 196810-01-2013100.0`, where a plain `\d{2}-\d{7}` match reads the in-service date
`10-01-2013` as an identifier. That invented five contradictions across this evidence and would have
rejected `PRECISION WINDOWS AND DOORS INC`. Excluding a neighbouring digit, dot or dash removed all
five and found *more* genuine matches, not fewer — 345 against 304.

**"Beside the entity name" means within 60 normalised characters.** Measured: across this evidence
the subject's identifier sits a median of **18** characters from the entity name, and the preparer's
firm identifier is **never closer than 308**. Sixty covers every genuine occurrence with a fivefold
margin. A window wide enough to reach the preparer line calls a correct document contradictory on a
compactly laid-out form.

## No raw identifier appears in these files

The manifest, sidecar, summary and evidence review contain **zero** taxpayer identifiers. Identifiers
appear only as derived hashes. The runner reads the raw value from the filed return, checks that it
derives to the frozen hash, hands it to the attribution service — which derives it again
independently — and never prints, logs or stores it.

## Expected write shape, if an apply is ever authorized

```
drake_business_identity   UPDATE   7   (entity set; trust triple filled by the COALESCE amendment)
drake_business_identity   INSERT   0
entity_source_links       INSERT   7
audit_events              INSERT   7   (drake.entity_attribution_machine, actor_user_id NULL)

drake_identity · person_source_links · people · relationship_entities · source_contacts
drake_client_returns · documents · document_ocr · drake_identity_match_candidates          0
```

## Hashes

```
d7_one_contact_document_verified_batch_manifest.csv   389824c9c383107fb4df7d9a60bac49e58bb7d842c36ed5ec53a9a0480a5ab98
d7_one_contact_document_verified_batch_manifest.json  eabcb78df49208e2f05f5c96831bd8c6af5f3d02853eec8bfa553b1a0d3bce32
d7_one_contact_document_verified_batch_summary.json   abe3b79a46f89f7afebfa0ec184a0c5abd73b8cd85c0574e2085ae17f5577972
d7_one_contact_7_document_review.csv                  73925d10bfb3b91a66785176367f349f2dd858ba520481da2eb9ec8b3eff8cc0

plan digest          53ae4b9c6629e22d50b2de955ad4b7e013329c07626ae2861588d5751b3e5e99
confirmation phrase  APPLY-D7-DOCVERIFIED-7
```

The runner checks all four. The sidecar pins the manifest's own hash and cites the evidence review's,
so a swapped pair is caught even if each file is individually well formed. These files are pure LF
and SHA-pinned, so `.gitattributes` marks this directory `-text`, as the ownership, document-filing,
Phase C and Batch 1 manifests already are.

## State at freeze

| | |
|---|---|
| production database | `client360` |
| production head at freeze | `3ef6112ae95a5f4fa6b35d5e08f61e9c191a2a29` |
| Alembic | `drake03` (no migration — this batch needs none) |
| `drake_business_identity` | 150 total, 68 attributed, 82 unattributed |
| `entity_source_links` | 183 |
| machine attribution audits | 40 |

## What remains after this batch

Of the 82 unattributed identities, these 7 are the document-corroborated single-contact rows. The
rest stay held: 25 single-contact rows without document corroboration, 39 partial-provenance rows,
6 across three multi-identifier entities, one contested between two valid entities, and five that no
entity can validly own.
