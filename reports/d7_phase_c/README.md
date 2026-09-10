# D7 Phase C — frozen production manifest

**APPLY IS NOT AUTHORIZED.** These files are a reviewed, frozen record. Nothing in this directory
has been applied to production, and running the apply requires its own authorization.

## What Phase C does

`drake_identity` holds natural-person Drake identities. 133 rows in production describe businesses,
estates and trusts instead, and each one blocks the attribution of the business it misdescribes,
because `drake_machine_attribution._check_identifier_is_free` refuses any identifier that
`drake_identity` mentions. Phase B stopped creating such rows and reports them as
`pending_d7_migration`; it relocates nothing. Phase C is that later phase.

A relocated identity becomes one `drake_business_identity` row with `relationship_entity_id`,
`trust_level`, `confirmation_source` and `evidence_method` all NULL — **typed, not yet attributed**,
the same shape `drake_subject_routing._DBI_UPSERT` already writes at ingestion. Deciding *which*
entity owns an identifier stays with the attribution service.

## State at freeze

| | |
|---|---|
| feature head | `1cc337b0899f4ee3bf1c4582503401d397118837` |
| production deployed head | `c563d31f9f2b81483be922278c51b9763c9c387a` |
| production database | `client360` |
| Alembic | `drake03` (no migration — Phase C needs none) |

## Population

| | |
|---|---|
| non-natural backlog | 133 |
| **frozen eligible** | **122** |
| cohort A (unlinked) | 111 |
| cohort B (linked, with backing `person_source_links`) | 11 |
| `business_entity` | 121 |
| `estate_or_trust` | 1 |
| excluded | 11 |

Case 7379 (`HANDPICKED WINE WAREHOUSE LLC`) is included through ordinary cohort-B rules, with
backing PSLs **7581** and **7794**. There is no special-case code for it. Its natural-person identity
`bce8a55f…` is outside the target set and must remain byte-identical.

## Exclusions

Refusal codes reflect **check order, not cohort** — the pending-candidate check runs before
classification, so `UNEXPECTED_DEPENDENCY` masks the underlying cohort for six rows.

| Code | Rows | Underlying cohort |
|---|---|---|
| `UNEXPECTED_DEPENDENCY` | 6 | 2 × B, 3 × C, 1 × D |
| `PERSON_LINK_WITHOUT_PSL` | 5 | C |

`ROBBINS REFRIGERATION` refuses first as `UNEXPECTED_DEPENDENCY`; `MIXED_SUBJECT` would also refuse
it independently after candidate resolution. `TNT LYNCHBURG INC` and `EDWARD J FRIAR BUILDER INC`
are cohort-B rows held out solely by their pending candidates.

## Hashes

```
d7_phase_c_apply_manifest.csv   6f667cf85a2090abe179d6aa294dee17520532dc6d717794f271139bfc5ddfa4
d7_phase_c_apply_manifest.json  c1361b3600b9821a3219bd092f3805aa0237c71def208d7ff97b1b5877cb2694
d7_phase_c_exclusions.csv       23471c430c081a45b3382171de072b83a055a83fe3194573e71e77c97e712b7d

plan digest                     4703f2f181e5320ca7534083c8d9728daf7a882315aec4d4b45c1e8f3795e847
confirmation phrase             APPLY-D7-PHASE-C-122
```

These files are pure LF and SHA-pinned, so `.gitattributes` marks this directory `-text`. Without
that rule `core.autocrlf` would hand a Windows checkout CRLF and the reviewed hashes could never be
satisfied again — the same protection the strict-safe ownership and document-filing manifests carry.

## This is not a rollback receipt

`drake_business_identity` ids are generated on INSERT and do not exist yet; none are fabricated here.
A future authorized apply must capture every `INSERT … RETURNING id`, build a **populated** rollback
manifest and SHA-256 it **before** commit. The pre-apply manifest and the populated rollback manifest
are two separate artifacts.

## Expected write shape, if an apply is ever authorized

```
drake_identity            DELETE  122
drake_business_identity   INSERT  122   (entity NULL, trust NULL, source NULL, method NULL)
audit_events              INSERT  122   (drake.identity_relocated_phase_c, actor_user_id NULL)

people · person_source_links · entity_source_links · relationship_entities
drake_client_returns · source_contacts · documents · drake_identity_match_candidates      0
```
