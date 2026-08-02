# ADR-011 — Canonical document model (one document, many sources, independent ownership)

## Status
Accepted (target architecture). Partially realized; see "Current state" for what is implemented today
and the additive path to full realization.

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
which system it was pulled from.

## Decision
There is **one canonical document**. Everything else references it.

1. **Canonical Document** — the single source of truth for a document's content and derived data. It
   owns: content hash, canonical storage location (the durable Client360-local copy), OCR text,
   AI extraction, classification, version history, audit history, and retention metadata. The
   canonical document is identified by a stable surrogate key (`documents.id`), never by its source.

2. **Document Sources** — a document may have **many** source references (TaxDome, Drake, Schwab,
   AssetMark, Microsoft 365, manual upload, scanner, local repository). A source reference records
   *where a copy of this document exists in an external system* (system, external id/uri/path, source
   hash, timestamps). A source reference is **not** a document. Re-ingesting from another system adds a
   source reference to the existing canonical document; it does not create a second document.

3. **Ownership** — a document is owned by one or more of: household, person(s), business, trust,
   estate. **Ownership is independent of source.** Ownership relationships never participate in
   document identity or de-duplication. Ownership may be many-valued (a document can be owned by a
   household *and* surface to each member).

4. **OCR / AI metadata / classification** — computed **once per canonical document** and reused across
   every source reference. A new source reference for an already-known document inherits the canonical
   OCR/AI/classification rather than recomputing or forking it.

5. **Version history** — versions belong to the canonical document. A newer copy arriving from any
   source is a new **version** of the canonical document, not a new document.

6. **Audit trail** — recorded against the canonical document (and against source-reference and
   ownership changes), so the full history of a document is answerable from one place.

7. **Duplicate detection** — the same real-world document is recognized across sources primarily by
   **content hash (SHA-256)**, secondarily by strong corroborating signals (size + name/path + owner).
   On a hash match, ingestion attaches a **source reference** to the existing canonical document
   instead of inserting a new one. Detection is content-based and therefore source-independent.

### Data-model shape (target)
- `documents` — the canonical document (already exists). Canonical fields: `sha256`, `storage_provider`
  + `storage_uri` (the Client360-local copy), `current_version`, `ocr_status`, `classification`,
  `status`, retention fields, `tags`.
- `document_sources` (**future, additive**) — `(id, document_id → documents.id, source_system,
  source_uri, source_path, source_external_id, source_hash, first_seen_at, last_synced_at, metadata)`.
  One row per (document, source system). Backfilled from the source metadata already stored in
  `documents.tags`.
- Ownership — `person_id` / `household_id` / `organization_id` on `documents` remain valid for the
  common single-owner and household cases; a `document_ownership` join (**future, additive**) is
  introduced only when many-valued ownership (multiple persons, trust, estate) is actually required.

### Migration path (intended to be a single additive step, not a rework)
1. `CREATE TABLE document_sources`; backfill one row per synced `documents` row from its `tags`
   (`source_system`, `source_path`, `source_root`, …) plus its `sha256`. No documents rows are moved
   or deleted; `documents.id` stays the canonical id.
2. Point source-scoped queries (e.g. the TaxDome filter) at `document_sources` instead of
   `tags.source_system`. Code change, not a schema rework.
3. Future importers (Drake, Schwab, …) resolve by `documents.sha256`: on a hit, insert a
   `document_sources` row; on a miss, insert a new canonical `documents` row + its first source
   reference.
4. Introduce `document_ownership` only if/when many-valued ownership is required; single-valued
   ownership columns continue to work until then.

## Current state (what PR #168 and the current importer implement)
- The `documents` row already **is** the canonical document; `documents.id` is the canonical id.
- Canonical storage is already **separated from source**: `storage_provider="Client360 Local"` +
  `storage_uri` point at the durable local copy, while `tags.source_system` records the origin.
- Every synced row carries a **content hash** (`sha256`) and a **complete source reference** in `tags`
  — exactly the data a `document_sources` backfill needs.
- **Ownership is modeled as relationships only** (`person_id`/`household_id`), never as identity;
  de-duplication keys on `stored_name`/hash, not on owner. `get_person_documents` resolves visibility
  through ownership (person **or** household).
- **Not yet implemented (future, by design):** the `document_sources` table; hash-based *cross-source*
  canonical resolution (today identity is source-scoped via `stored_name = "taxdome:" + hash(path)`, so
  a Drake copy of the same file would currently create a separate canonical document until step 3
  above lands); many-valued ownership beyond household/person.

## Consequences
- New ingestion sources are added as **source references + hash-based resolution**, not new document
  tables. Duplicates are prevented at ingest.
- OCR/AI/classification are computed once and shared, reducing cost and drift.
- Because source metadata already lives in `tags` and every row carries `sha256`, adopting the
  canonical model is an **additive** migration + backfill — no duplicate-cleanup project and no
  identity rework of existing rows.
- Until `document_sources` exists, cross-source de-duplication is not automatic; this is an accepted,
  bounded gap, not a structural obstacle.

## Alternatives considered
1. **One document row per (source, file)** (status quo for a single source). Rejected as the long-term
   model: forks OCR/AI/version/audit across systems and makes a single truthful view impossible.
2. **Source system as the document identity** (e.g. keep `taxdome:`/`drake:` prefixes as identity).
   Rejected: bakes source into identity and guarantees cross-source duplicates.
3. **Encode ownership into identity/dedup** (e.g. dedup per person). Rejected: ownership is orthogonal
   to identity; a household document must not fork per member.

## Related
ADR-001 (composition layers), ADR-002 (domain ownership & source of truth), ADR-004 (authorization &
record scope — enforced on document reads regardless of source).
