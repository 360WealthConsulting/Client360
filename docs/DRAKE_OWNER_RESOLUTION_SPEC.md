# Drake Tax Document Owner Resolution Specification

## Purpose

Define the permanent Client360 workflow for resolving Drake tax documents to canonical people, households, and organizations without relying on unsafe generic name matching.

This specification captures production lessons from the 2026 Drake migration and reconciliation effort.

## Core Principle

A tax document owner must be determined from taxpayer-role evidence, not merely from arbitrary names, phone numbers, email addresses, preparer information, or other identifiers appearing anywhere in the document.

## Evidence Sources

Client360 should evaluate the following evidence together:

1. Drake document provenance.
2. Drake source_external_id.
3. Drake source-contact role data.
4. Taxpayer and spouse names from the return itself.
5. OCR/native document text.
6. Structured document filename.
7. Existing canonical people.
8. Existing canonical households.
9. Existing canonical organizations/businesses.
10. Previously resolved documents when their ownership is independently trustworthy.

## Evidence Hierarchy

### Strong evidence

- Drake source contact explicitly marked role=taxpayer.
- Drake source contact explicitly marked role=spouse.
- Taxpayer/spouse names printed in the taxpayer section of the return.
- Filename taxpayer identity when corroborated by return content and Drake role data.
- Canonical taxpayer and spouse belonging to the same canonical household.
- Exact canonical business/entity matching the taxpayer entity on a business return.

### Supporting evidence

- Stable Drake source_external_id.
- Address.
- Email.
- Phone.
- Previously resolved sibling documents.

Supporting evidence must not override taxpayer-role evidence.

## Evidence That Is Not Sufficient By Itself

- person_source_links.confirmed=true.
- Exact email appearing somewhere in a return.
- Exact phone appearing somewhere in a return.
- Exact address appearing somewhere in a return.
- A preparer or ERO name appearing in the document.
- A business officer or shareholder name appearing in a business return.
- A single previously owned document sharing a Drake ID when that historical ownership may itself be semantically wrong.

## Individual Returns

For Forms 1040, 1040-X, state individual returns, signature forms, and related individual-tax documents:

1. Identify the primary taxpayer.
2. Identify spouse when applicable.
3. Resolve aliases and common-name variants.
4. Match taxpayer/spouse to canonical people.
5. If both taxpayer and spouse resolve to members of the same household, assign the document to the household.
6. If the return is truly single-taxpayer and one canonical taxpayer is deterministically resolved, assign to that person.
7. If taxpayer exists but spouse is missing, create/link the spouse and household only when Drake role data and return evidence support doing so.

## Business Returns

For Forms 1120, 1120-S, 1065, business state returns, and related documents:

1. Resolve the taxpayer business/entity.
2. Do not assign the return to an individual merely because an owner, officer, member, accountant, preparer, or contact appears in the document.
3. Business legal name and taxpayer/entity section of the return are primary evidence.
4. If the legal entity is missing from canonical data, create or link the organization only after deterministic evidence is established.

## Alias Handling

The resolver must support controlled first-name aliases and abbreviations when other identity evidence agrees.

Examples discovered during reconciliation:

- Reginald <-> Reggie
- Kenneth <-> Ken
- Jonathan <-> Jon
- Joseph <-> Joe
- Thomas <-> Tom
- Robert <-> Bob
- Teresa/Theresa <-> Tere

Aliases must not permit surname mismatches.

## Filename Semantics

Drake-generated filenames are evidence, not noise.

Examples:

- LYNCH RODNEY M and TERE
- WOOD RONALD D and JENNI
- HENZEY THOMAS F and KAR

Filename evidence may be used when corroborated by:

- Drake taxpayer/spouse role data; and
- taxpayer/spouse identity found in the return or OCR.

Filename evidence alone must not cause an ownership write.

## Automatic Resolution Rules

Client360 may automatically assign when all required evidence is deterministic.

### Household auto-file

- Primary taxpayer identified.
- Spouse identified.
- Both supported by return/OCR and Drake role data.
- Both canonical people resolve to the same household.
- No conflicting canonical identity.

### Person auto-file

- Individual/single-taxpayer document.
- Primary taxpayer identified by Drake role data.
- Taxpayer identity supported by return/OCR.
- Exactly one canonical person resolves.
- No spouse/household ownership is indicated.

### Organization auto-file

- Business return.
- Taxpayer legal entity identified from return/Drake evidence.
- Exactly one canonical relationship entity resolves.
- No conflicting business identity.

## Exception Queue

Documents must remain in the staff filing queue when:

- taxpayer identity is missing;
- canonical taxpayer is missing and creation cannot be deterministic;
- spouse/household relationship is unresolved;
- multiple canonical identities compete;
- business legal name is incomplete or truncated;
- historical ownership evidence conflicts;
- a document belongs to a specifically frozen identity-review case.

## Frozen Cases

Frozen cases must never be automatically assigned merely because the proposal engine returns HIGH.

Current known example:

- Melunis / Drake ABBE7490.

## Known Failure Modes That Must Become Regression Tests

### Preparer contamination

Tax returns previously proposed unrelated preparer/accounting identities because their contact information appeared in many returns.

Required regression behavior:

- preparer/ERO phone, email, address, and business names must not become taxpayer ownership evidence.

### Shared-contact contamination

Confirmed source links previously caused spouse/shared contacts to be treated as document owners.

Required regression behavior:

- confirmed source linkage alone cannot establish taxpayer ownership.

### Business-to-person contamination

Occupational Therapy documents demonstrated that a business Drake ID historically attached to an individual does not prove the business return belongs to that person.

Required regression behavior:

- business tax returns must resolve to canonical business/entity ownership.

### Joint return assigned to one spouse

A joint return must not be filed to whichever spouse happened to be the only canonical name found.

Required regression behavior:

- when both taxpayers are present, prefer household resolution.

## Current Regression Fixtures

These document IDs should be preserved as test fixtures or equivalent anonymized fixtures:

- 121600 / 121601: Lynch joint-return canonical-gap case.
- 121607 / 121608 / 121609: Wood taxpayer/spouse creation/link case.
- 121618 / 121619 / 121620: Occupational Therapy business-vs-person case.
- 121627 / 121628: frozen Melunis case.
- 121633: Doyle primary-present/spouse-missing case.
- 121656 / 121657 / 121658 / 121659: Reginald/Reggie alias case.
- 121661 / 121662: Campbell canonical-gap/joint-return case.
- 121713 / 121714 / 121715 / 121716: Henzey household-positive case.
- 121717: Kenneth/Ken alias plus spouse case.
- 121728: Sparks taxpayer/spouse creation case.
- 121730: Joseph/Joe alias case.
- 121731: Jonathan/Jon alias plus spouse case.
- 121763: Powitz canonical-gap case.
- 121809: Porter taxpayer/spouse canonical-gap case.

## Implementation Target

Permanent implementation belongs in the Client360 document-owner resolution layer.

It should:

- parse taxpayer roles;
- build a structured identity evidence object;
- score evidence by role and source;
- support controlled aliases;
- distinguish individual and business return types;
- resolve household ownership;
- create/link missing canonical identities only under deterministic rules;
- record assignment reason and evidence;
- remain idempotent;
- never overwrite existing ownership;
- expose unresolved cases through the admin filing queue.

## Operational Goal

Future Drake ingestion should automatically file deterministic documents and leave staff only a small true-exception queue.

The 2026 manual reconciliation workflow is not intended to be repeated.
## SSN-Derived Drake Identity Rules

Client360 already imports Drake taxpayer and spouse Social Security Numbers through a deterministic protected hash. The Drake import normalizes the source SSN to digits and computes a SHA-256 identifier using the configured secret key. Raw SSNs must not be stored in resolver logs, browser output, manifests, audit comments, or ownership evidence.

For individual Drake tax returns, the existing Drake taxpayer/spouse identifier hashes are the strongest available structured identity evidence.

Identity precedence for an individual Drake return is:

1. Drake taxpayer/spouse identifier hash derived from the source SSN.
2. Drake role: taxpayer or spouse.
3. Existing canonical drake_identity.primary_person_id.
4. Confirmed person_source_links carrying that same SSN-derived Drake identity.
5. Taxpayer/spouse name from the Drake return record.
6. Taxpayer/spouse name from the actual return content.
7. Structured Drake filename corroboration.
8. Date of birth where available.
9. Email, phone, and address as supporting evidence only.

Email, phone, address, preparer data, ERO data, employer data, officer/member data, and generic names elsewhere in the document must never override contradictory SSN-derived taxpayer/spouse identity evidence.

### Married-name and alias continuity

A canonical person's current or historical surname does not need to equal the surname on a later tax return when the same SSN-derived Drake taxpayer identity continues across years.

Proven regression example:

- NATALIE DISALVO in Drake 2023 and NATALIE PORTER in Drake 2024/2025 carry the same taxpayer identifier hash.
- The 2023 identity is canonically linked to person #7680.
- Therefore the 2024/2025 Porter taxpayer identity resolves to the same canonical person rather than creating a duplicate Natalie Porter.
- The canonical person's display name is not automatically renamed solely because a later Drake return uses a married surname.

### Joint-return household resolution

When taxpayer and spouse each have distinct SSN-derived Drake identifier hashes:

- If both hashes resolve to canonical people already sharing one household, own the joint return by that household.
- If both resolve to canonical people with no household and there is no conflicting household state, create one household and attach both.
- If exactly one hash resolves canonically and the other is an unambiguous Drake spouse/taxpayer identity with its own SSN-derived hash, the missing person may be promoted to a canonical person and the household created.
- If either identity belongs to a conflicting existing household, fail closed and send the case to review.
- Never assign an MFJ/joint return to only one spouse merely because that spouse is the only pre-existing canonical person.

### SSN OCR is secondary validation, not the primary Drake identity source

Do not create a second independent SSN identity system from generic OCR number matching when structured Drake taxpayer/spouse identifier hashes are available.

OCR may be used to corroborate structured Drake evidence, but arbitrary nine-digit strings in OCR are unsafe because tax returns contain ZIP+4 values, account numbers, form identifiers, EINs, and other numeric strings.

Any OCR SSN extraction used for validation must be tied to the actual taxpayer/spouse field structure of the tax form. A number appearing near a joint-name block must not automatically be attributed to the spouse simply because the spouse name appears on the same OCR line.

### Business returns

SSN-derived taxpayer/spouse rules apply to individual returns only.

Business returns must resolve against the taxpayer business entity using business taxpayer evidence such as legal name, EIN/TIN-derived business identity, return type, and structured Drake provenance.

An officer, managing member, employee, preparer, ERO, or contact person must never become the owner of a business return merely because their personal identity appears in the document.

### Controlled name equivalence learned during reconciliation

Controlled aliases and source-name normalization may support an already-identified Drake taxpayer/spouse identity, but must not replace the SSN-derived identity when it exists.

Additional proven equivalences include:

- Ronald / Ronnie
- Jackie / Jacki
- Jennie / Jenni

These are supporting name normalizations only.

### Regression cases

The permanent resolver must include regression coverage for at least:

- Lynch: joint taxpayer/spouse return must resolve to household.
- Wood: Ronald/Ronnie and Jennie/Jenni aliases must not defeat stable identity evidence.
- Doyle: Joseph/Joe alias plus spouse household ownership.
- Campbell: taxpayer/spouse roles must outrank anomalous secondary contact data.
- Otey: Kenneth/Ken identity continuity.
- Sparks: Tony/Jackie household creation.
- Simmons: Joseph/Joe plus spouse household ownership.
- Iott: Jonathan/Jon identity continuity and stale canonical-ID protection.
- Powitz: taxpayer/spouse household ownership.
- Porter: surname change from DiSalvo to Porter must resolve through the same SSN-derived taxpayer identity.
- Porter: unrelated existing Porter Household must not be selected merely by surname.
- Occupational Therapy: business return must not resolve to Melissa Glass personally.
- Melunis: frozen historical identity must remain held until explicitly reconciled.
- Preparer/ERO contamination: Mike Agree, Michael Shelton, 360 Tax Solutions, or other preparer/business identities must not become taxpayer owners solely because they appear in tax-return content.

### Implementation requirement

The permanent Drake filing resolver should consume the existing structured Drake identity tables and relationships rather than re-solving Drake taxpayer identity with generic document scoring.

For Drake-sourced individual returns, implementation should consult:

- drake_client_returns.taxpayer_identifier_hash
- drake_client_returns.spouse_identifier_hash
- drake_identity.identifier_hash
- drake_identity.primary_person_id
- Drake source_contacts role and identifier hash
- confirmed person_source_links
- canonical people
- canonical households

The generic document-owner proposal service may still provide fallback/supporting evidence, but it must not supersede stronger Drake taxpayer/spouse identity evidence.

Automatic resolution must remain idempotent, auditable, fail-closed on conflicts, and must never overwrite an existing document owner.
