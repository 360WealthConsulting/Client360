# ADR-072 — Canonical document model: one document, many source references, ownership independent of source

## Status
Accepted — target architecture; partially realized today (see "Current state and PR #168 alignment").

## Date
2026-08-02

## Decision owners
Platform Architecture; Business Operations Owner (Michael Shelton); Domain Owner (Documents).

## Context
Client360 ingests the same real-world document from many systems — TaxDome, Drake, Schwab, AssetMark,
Microsoft 365, manual upload, scanner, and the local repository. A joint tax return may originate in
Drake, also appear in TaxDome, and later be uploaded by hand. If each ingestion path creates its own
row, the platform accumulates duplicate "documents" that fork OCR, AI extraction, classification,
version history, retention, and audit — and no screen can present a single truthful picture of a
document. Separately, a document's **owners** (household, person(s), business, trust, estate) are a
different concern from **where it came from**: a joint return is owned by a household regardless of
which system a copy was pulled from. We want to define the long-term model now so that new ingestion
sources are added without a duplicate-cleanup project or an identity rework migration later.

## Decision
There is **one canonical document**; everything else references it.

1. **Canonical Document** — the single source of truth for a document's content and derived data. It
   owns: content hash, canonical storage location (the durable Client360-local copy), OCR text, AI
   extraction, classification, version history, audit history, and retention metadata. It is identified
   by a stable surrogate key (`documents.id`), never by its source system.
2. **Document Sources** — a document may have **many** source references (TaxDome, Drake, Schwab,
   AssetMark, Microsoft 365, manual upload, scanner, local repository). A source reference records
   where a copy exists in an external system (system, external id/uri/path, source hash, timestamps).
   A source reference is **not** a document; re-ingesting from another system adds a source reference to
   the existing canonical document, it does not create a second document.
3. **Ownership** — a document is owned by one or more of household, person(s), business, trust, estate.
   **Ownership is independent of source** and never participates in document identity or
   de-duplication. Ownership may be many-valued (a document owned by a household surfaces to each
   member).
4. **OCR / AI metadata / classification** — computed once per canonical document and reused across all
   source references; a new source reference inherits them rather than recomputing or forking.
5. **Version history** — versions belong to the canonical document; a newer copy from any source is a
   new version, not a new document.
6. **Audit trail** — recorded against the canonical document (and against source-reference and
   ownership changes), so a document's full history is answerable in one place.
7. **Duplicate detection** — the same real-world document is recognized across sources primarily by
   **content hash (SHA-256)**, secondarily by corroborating signals (size + name/path + owner). On a
   match, ingestion attaches a source reference to the existing canonical document instead of inserting
   a new one. Detection is content-based and therefore source-independent.

Target data shape: `documents` remains the canonical document (canonical fields already present:
`sha256`, `storage_provider` + `storage_uri` for the local copy, `current_version`, `ocr_status`,
`classification`, retention fields, `tags`). A **future, additive** `document_sources` table
(`document_id → documents.id`, `source_system`, `source_uri`, `source_path`, `source_external_id`,
`source_hash`, `first_seen_at`, `last_synced_at`, `metadata`) holds the many source references,
backfilled from the source metadata already stored in `documents.tags`. Many-valued ownership beyond
the existing single-valued `person_id`/`household_id`/`organization_id` is introduced via a future,
additive `document_ownership` join only when person(s)/trust/estate ownership is actually required.

## Alternatives considered
1. **One document row per (source, file)** — the naive status quo for a single source. Rejected as the
   long-term model: forks OCR/AI/version/audit across systems and makes a single truthful view
   impossible.
2. **Source system as document identity** (keep `taxdome:`/`drake:` prefixes as identity). Rejected:
   bakes source into identity and guarantees cross-source duplicates.
3. **Encode ownership into identity/dedup** (e.g. dedup per person). Rejected: ownership is orthogonal
   to identity; a household document must not fork per member.
4. **Build `document_sources` + hash-based canonical resolution immediately.** Deferred, not rejected:
   there is no second-source importer yet; building the table now adds structure and a migration ahead
   of need. The current data already carries everything (hash + full source reference in `tags`) for a
   later additive migration, so deferring costs nothing.

## Reasons for the decision
- A single canonical document is the only shape that lets OCR, AI extraction, classification, version
  history, retention, and audit be computed and answered once rather than forked per system.
- Making ownership independent of source matches reality (a joint return is a household's document
  regardless of origin) and keeps de-duplication content-based and correct.
- Because the current importer already separates canonical local storage from source origin and stores
  a content hash plus a complete source reference in `tags`, the full model is reachable by an
  **additive** migration + backfill — avoiding exactly the six-months-from-now rework this decision
  exists to prevent.

## Consequences

### Positive consequences
- New ingestion sources are added as source references + hash-based resolution, not new document
  tables; duplicates are prevented at ingest.
- OCR/AI/classification are computed once and shared, reducing cost and drift.
- Adopting the canonical model is an additive migration + backfill from data already stored — no
  duplicate-cleanup project and no identity rework of existing rows.

### Negative consequences and tradeoffs
- Until `document_sources` exists, identity is source-scoped (`documents.stored_name = "taxdome:" +
  hash(relative_path)`), so a copy of the same file arriving from a different system (e.g. Drake) would
  currently create a separate canonical document. This is an accepted, bounded gap — the content hash
  is stored on every row, so future cross-source resolution is enabled, not blocked.
- Many-valued ownership (multiple persons, trust, estate) is not representable until the additive
  `document_ownership` join lands; today the single-valued household/person columns cover the common
  and household cases.

## Current state and PR #168 alignment
- The `documents` row **is** the canonical document; `documents.id` is the canonical id.
- Canonical storage is already **separated from source**: `storage_provider="Client360 Local"` +
  `storage_uri` are the durable local copy, while `tags.source_system` records the origin.
- Every synced row carries a **content hash** (`sha256`) and a **complete source reference** in `tags`
  (`source_system`/`source_root`/`source_path`/`source_relative_path`/`last_synced_at`/…) — exactly the
  data a `document_sources` backfill needs.
- **Ownership is a relationship only** (`person_id`/`household_id`), never identity; de-duplication keys
  on `stored_name`/hash. `get_person_documents` resolves visibility through ownership (person **or**
  household). The `--repair-links` command fills ownership columns only and does not touch source
  structure, so it coexists with a future `document_sources` table with no further migration.
- **Not yet implemented (future, additive):** the `document_sources` table + backfill; hash-based
  cross-source canonical resolution in future importers; the `document_ownership` join for
  person(s)/trust/estate.

## Enforcement
- New document-ingestion code MUST treat `documents.id` as the canonical identity, MUST store the
  content `sha256`, and MUST record source origin as a source reference (today in `tags`; after the
  additive migration, in `document_sources`) rather than as document identity.
- New ingestion sources MUST resolve to an existing canonical document by content hash before inserting
  a new `documents` row.
- Ownership MUST NOT be encoded into identity or de-duplication keys.
- Reviewers reject changes that create a second document row for a copy already represented, or that
  couple source system into canonical identity beyond the existing legacy `stored_name`.

## Exceptions
- The existing source-scoped `stored_name` is grandfathered until `document_sources` lands; it remains a
  lookup/dedup key within a source and is not to be extended to encode additional source semantics.
- A genuinely distinct document that merely shares a hash prefix (hash collisions are not expected for
  SHA-256) is out of scope; corroborating signals exist for defense in depth.

## Revisit conditions
- A second ingestion source (Drake, Schwab, AssetMark, scanner) is scheduled — implement
  `document_sources` + hash-based resolution then.
- Many-valued ownership (multiple persons, trust, or estate ownership) becomes a requirement — add the
  `document_ownership` join.
- OCR/AI extraction is introduced — confirm it is computed once per canonical document and shared
  across source references.

## References
- `app/importers/taxdome_drive.py` — one-way sync; canonical local copy + source reference in `tags`;
  content hash; `resolve_folder`/`--repair-links` (ownership only).
- `app/services/documents.py` — `get_person_documents` resolves visibility by person or household
  (ownership, not identity).
- `tests/test_taxdome_drive.py` — ownership/identity separation, household visibility, repair coverage.
- `docs/TAXDOME_DOCUMENT_SYNC.md` — operational runbook for the current importer.
- ADR-002 (docs/adr/ADR-002-domain-ownership-and-source-of-truth.md) and ADR-004
  (docs/adr/ADR-004-server-side-authorization-and-record-scope.md) — document reads remain scope-
  enforced regardless of source.
