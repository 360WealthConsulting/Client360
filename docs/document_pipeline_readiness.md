# 360Plus Document Pipeline — Readiness Audit

**Status:** read-only assessment. No code changed. Purpose: what's implemented vs incomplete across
document classification, client/entity linking, canonical naming, duplicate handling, and manual review —
and what to close before migrating real client documents. Evidence is cited `file:line`.

**Architectural foundation (ADR-072/073):** one `documents` row per unique content (SHA-256), many
`document_sources` references, ownership as a *mutable relationship never part of identity*. **The pipeline
is proposal-only:** no ingestion path, classifier, or matcher writes ownership. Ownership is written only by
one atomic, re-checking service (`app/services/households.py:328 resolve_document_ownership`) invoked from
explicit human "Confirm" actions. This safety property holds across every module audited and is the most
important guarantee for a client-data migration.

---

## 1. Document classification

**Implemented — two coexisting layers:**
- *Coarse, name/extension-only (ingestion time):* `sharepoint.py:82 sharepoint_doc_type()` (`_DOC_FAMILY`
  `:57-65`) → file family; `taxdome_drive.py:201 infer_category()` (`:204-214`) → 5 buckets
  (`tax_document`/`statement`/`agreement`/`identification`/`invoice`/None) by keyword over filename+path,
  no content read. This value populates `documents.category`.
- *Rule-based doc-type over filename + text (analysis time):* `document_classification.py:60
  classify_document()` — deterministic ordered-regex classifier (`CLASSIFIER_VERSION="rules-v1"`), 24-value
  vocabulary (`:15-20`), most-specific-first rules (`:27-57`), base confidence 0.7–0.9 with ±0.05
  adjustments (`:66-76`). Persisted to `document_classifications` via `document_pipeline.py:101,114-123`.
- It is rule/name/text-based, **not ML** (module is explicit a learned classifier can slot behind the same
  interface later). `ai_assist` is **not** wired into classification (read-only advisory only).

**Incomplete / gaps:**
- **Two category vocabularies that don't reconcile:** `documents.category` (5, name-only, set once) vs
  `document_classifications.doc_type` (24, text-based). Nothing maps them; the richer text classifier never
  upgrades `documents.category`. **Decide which is authoritative before migration.**
- Confidence values are hand-assigned constants — no calibration/eval harness, no confidence floor gating
  downstream behavior.
- On the ingestion path analysis runs with `ocr=False` (`document_pipeline.py:176-181`), so scanned/image
  docs get `doc_type` from filename only until a later OCR-enabled batch runs.
- `unknown`/0.0 is silent — no surface counts "classified as unknown."

---

## 2. Client / entity linking (ownership proposal)

**Implemented — the most developed area; proposal-only with ONE narrow auto-write:**
- *Content proposal engine* (`document_owner_proposal.py`): eligibility gate = all of person/household/org
  NULL and not permanent-reject (`:635`, `:37`). `extract_document_text` native-first (Excel/docx/ics/eml/
  xls/PDF-text/plaintext), OCR fallback only for image/scanned when `ocr=True`, bounded (`_MAX_TEXT_CHARS`
  `:311-359`). `build_match_indexes` (`:364-411`) builds email/phone(NANP)/full-name/(first,last)/ZIP/street
  indexes; `_augment_from_source_contacts` (`:414-457`) unions in the real emails/phones from
  `source_contacts`+`person_source_links` (canonical `people` columns are largely NULL).
  `analyze_identity` (`:473-613`) scores with weights `email:100, phone:90, address:60, name:40` (`:83`) and
  yields confidence tiers (`:462-470`): email/phone → HIGH; name+address → HIGH; unique full name → MEDIUM;
  else LOW/AMBIGUOUS/NO_MATCH. Institutions are context-only, never owners (`:491-492`).
- *Folder auto-link (the ONLY automatic ownership write):* `taxdome_drive.py resolve_folder` (`:253-285`) —
  a TaxDome top-level folder auto-links to a person/household **only** on a unique exact normalized-name
  match; joint → shared household; anything ambiguous → `(None,None)` for review. Applied to NULL fields
  only (`_apply_folder_link :547-558`). SharePoint reuses it when `client_folder` is present.
- *Persistence:* `document_pipeline.py analyze_document` (`:84-109`) routes HIGH/MEDIUM/AMBIGUOUS/
  NEW_CLIENT_CANDIDATE/NO_MATCH/UNSUPPORTED/ERROR; `persist_proposal` (`:149-166`) writes a versioned,
  **sanitized** `owner_proposal` fact (masked evidence, no raw text/full SSN) and the classification, and
  **writes no ownership**. Auto-analysis fires for each new canonical doc (`AUTO_ANALYZE_NEW_DOCUMENTS=True`,
  `:37`), fully guarded.

**Incomplete / gaps:**
- `EMIT_NEW_CLIENT_CANDIDATE=False` (`document_pipeline.py:42`) — the strong-identity-but-no-match route is
  **disabled**; such docs route to NO_MATCH (a separate live detector surfaces new-entity proposals for
  admins — see §5 — but the pipeline's own routing label is off; confirm the intended single path).
- **MDM canonical-field backfill is explicitly deferred** (`:414-423`). Match quality depends entirely on
  `source_contacts`/`person_source_links` coverage being present and correct.
- OCR off on the ingestion path — image/scanned docs get name-only proposals until an OCR batch runs.
- Six permanent-reject document IDs are hard-coded in three modules — dataset-specific constants; verify
  they match the actual migration dataset (else meaningless/harmful).

**Risks before migration:**
- The folder auto-link **is an automatic write** during sync. It is conservative, but on a fresh migration
  where canonical `people` may be duplicated, **de-duplicate/validate canonical people first** or exact-name
  accuracy degrades.
- Name-only is deliberately MEDIUM/AMBIGUOUS → human review: **expect a large NO_MATCH/AMBIGUOUS backlog**
  for documents lacking email/phone/address.

---

## 3. Canonical naming

**Implemented:** `documents.stored_name` is a deterministic, unique, non-human key — SharePoint
`"sharepoint:"+sha256(uri:sha)` (`sharepoint.py:92-96`, hashes content so a changed file at the same URI is
distinct), TaxDome `"taxdome:"+sha256(relative-path)` (`taxdome_drive.py:95-99`, drive-letter-independent,
with legacy-upgrade). Human-meaningful path is separate (`storage_path`/`storage_uri`): SharePoint
`_rel_path` = `Site/Library/<folder>/<file>` after stripping the Graph `…/root:` prefix (`:105-135`); all
paths pass `sanitize_relative_path` (rejects absolute/drive-letter/`..`/embedded-`:`, strips illegal chars,
`:139-161`); physical copy is atomic + SHA/size-verified (`_copy_verified :173-198`).

**Incomplete / gaps:**
- Naming is collision-safe by construction (hash key + unique constraint) but the **human path is not
  collision-guarded**: two *different* documents with the same `Site/Library/folder/name` map to the same
  `storage_path`, and `_copy_verified` does `os.replace` at the destination — a genuine same-folder/same-name
  clash of differing content would overwrite the local copy. Content dedupe (§4) makes this rare; no
  per-file suffixing exists. **Pre-migration check:** scan for duplicate `(folder,name)` with differing
  hashes.
- Canonical content is a straight copy — no normalized/human rename scheme (e.g. `2021_1040_Smith.pdf`);
  meaning comes only from the preserved source tree + `original_name`.

---

## 4. Duplicate handling / dedupe

**Implemented — strong, content-hash based:**
- `document_sources.resolve_or_create_canonical` (`:45-93`): resolve by SHA-256 vs non-deleted docs; hit →
  reuse row + attach a source ref (fill NULL ownership only, never overwrite); miss → create. **Cross-source
  by design** (all importers funnel through it). `add_source_reference` idempotent on
  `(document_id, source_system, source_uri)`.
- SharePoint checks SHA before copying and reuses/skips the second copy, backfilling a local copy only if the
  reused canonical has none (re-verifying content SHA) (`sharepoint.py:311-333`, `backfill_local_source`).
- Incremental skip avoids re-hashing: SharePoint on `size+modified` (`:280-291`), TaxDome on
  `source_size+source_modified` (`:471-481`). TaxDome also reconciles legacy duplicate rows (`:413-429`).

**Incomplete / gaps:**
- Exact SHA-256 only — no fuzzy/perceptual dedupe. Near-duplicates (re-scan, re-save, re-export) are distinct
  canonical rows; same logical doc from two systems with any byte difference will not dedupe.
- **`documents.sha256` uniqueness is enforced by application logic, not (visibly) a DB unique index.** Single
  -threaded sync is safe; **parallel/bulk ingestion could race to two rows for identical content.** Confirm a
  DB unique/partial-unique index on `sha256` before any parallel migration.

---

## 5. Manual-review workflow

**Implemented — several purpose-built read-only review surfaces, each with live re-evaluation + a guarded
atomic write** (all under `/admin`, gated by `identity.manage` + `client.write`/`record.write_all`):
1. *Unassigned folders/documents* (`routes/admin.py:243,322,353,400`) — folder- and doc-level assign, always
   preview→`confirm=yes`, routed through `households.resolve_document_ownership` (atomic all-NULL-and-not-
   reject recheck in the WHERE clause).
2. *HIGH bulk-confirm* (`document_high_validation.py` + `document_high_confirm.py`) — live re-evaluation
   splits HIGH into selectable "eligible" vs "review" (7 contradiction classes `:41-45,142-177`); each
   selected doc is re-checked again immediately before its atomic write.
3. *MEDIUM/AMBIGUOUS/HIGH-review queue* (`document_review_queue.py`) — per-doc worklist; `approve_ownership`
   requires `confirm=yes`, record-scope, atomic, never overwrites.
4. *New-entity proposals* (`document_entity_proposal.py`) — NO_MATCH docs with a corroborated identity;
   reviewer can approve (create one entity + assign), reject (retained so it won't re-propose), or
   assign-existing. Versioned `new_entity_proposal` facts.
5. *Context review* (`document_nomatch_analysis.py`) — buckets NO_MATCH by folder context; only concrete
   CONTEXT_HIGH/LIKELY are assignable, re-verified live before assign.
- *Adjacent formal state machine:* the **Exception Engine** (`exception_engine.py`, ADR-17) with real
  statuses (`open/acknowledged/in_progress/waiting/escalated/reopened/resolved/cancelled`), `TRANSITIONS`,
  `SELECT…FOR UPDATE`, immutable `exception_events`, SLA, reporting. Documents connect via the **`linkage`**
  domain (`migrations/.../lnkg01_linkage_exception_domain.py`) which turns unresolved ingestion subjects into
  tracked review work; one type seeded (`linkage.unresolved_subject`, severity `low`).

**State model:** the document-proposal surfaces are **not** a persisted state machine — proposal state lives
in versioned `document_facts` (`owner_proposal`/`new_entity_proposal`, `is_current`/`version`) and the
"states" are derived live each page load; terminal transition = `resolve_document_ownership` sets an owner.
The **formal** state machine is the Exception Engine, with the `linkage` domain as the intended bridge.

**Incomplete / gaps:**
- **Fragmentation:** five separate document-review surfaces + the Exception Engine, each recomputing live.
  No single reviewer inbox with "everything needing attention + counts." The `linkage` domain is the intended
  consolidation but only one exception type is seeded (severity `low`).
- **Live-recompute cost at scale:** each review page rebuilds match indexes and re-runs proposals over the
  unassigned set on every request (`review_queue.py:40-64`, `document_high_validation.py:265-279`). For tens
  of thousands of unassigned docs this needs pagination/caching or a precomputed backlog before go-live.
- **OCR backend not installed in-repo** (`document_ocr.py default_extractor` raises; real engine needs
  Tesseract/Poppler wired at deploy). Until wired, scans stay in a truthful retryable `failed` OCR state and
  produce name-only proposals → they pile into NO_MATCH/UNSUPPORTED review. **This is the single biggest
  readiness dependency if the corpus is scan-heavy.**

---

## Readiness summary — do before client migration

Strengths: consistent proposal-only safety, one atomic self-rechecking ownership write with audit,
savepoint-isolated non-blocking analysis, one-way read-only sources with retained local copies,
`--authoritative`-gated missing reconciliation (guards the documented "21,697 false-missing" bug).

**Must-close before migrating real client documents (priority order):**
1. **Wire + validate the production OCR backend** (Tesseract/Poppler) if the corpus includes scans/images —
   otherwise scanned docs get name-only proposals and flood NO_MATCH/UNSUPPORTED review.
2. **Complete/verify MDM canonical-field backfill** (or confirm `source_contacts`/`person_source_links`
   coverage) — content matching depends on it.
3. **De-duplicate/validate canonical `people`** before running folder auto-link (the one automatic write).
4. **Confirm a DB uniqueness guard on `documents.sha256`** if any parallel/bulk ingestion is planned.
5. **Decide the authoritative category** (`documents.category` name-only vs `document_classifications.doc_type`
   text-based) and reconcile the two vocabularies.
6. **Plan for review at scale** — expect a large AMBIGUOUS/NO_MATCH backlog; add pagination/precomputation to
   the live-recompute review pages, and consider a unified reviewer inbox via the Exception Engine `linkage`
   domain.
7. **Verify dataset-specific constants** (permanent-reject IDs, "validated HIGH" sets) match the real dataset;
   confirm whether `EMIT_NEW_CLIENT_CANDIDATE` should be enabled or the entity-proposal detector is the single
   intended path.

Key files: `document_owner_proposal.py`, `document_pipeline.py`, `document_classification.py`,
`document_sources.py`, `importers/{sharepoint,taxdome_drive}.py`, `document_ocr.py` + `ocr_backend.py`,
`document_{high_validation,high_confirm,review_queue,entity_proposal,nomatch_analysis}.py`,
`households.py:328`, `routes/admin*.py`.
