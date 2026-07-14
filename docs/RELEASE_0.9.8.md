# Client360 Release 0.9.8 — Tax Document Intelligence & Missing Information

Released July 14, 2026 from merge commit
`48a1bcbba201df75858d7bbdd2f0ffd4c6e893a0`.

## Overview

Release 0.9.8 delivers Epic 5 Sprint 5.4. It connects *received* documents —
from portal uploads and Microsoft drive synchronization — to the checklist and
missing-information system built in Sprint 5.2, through a single deterministic
matching engine. Critically, it **replaces the substring-based Microsoft document
matching (RC8 finding H13)** with a deterministic, authorization-aware,
confidence-scored engine that routes every ambiguous match to mandatory human
review, closing a confirmed cross-client document-exposure risk.

## Schema

- Schema version and Alembic head: `l2c03f1e0d9b`.
- Parent: Release v0.9.7 / `j0a81f9c8d7e`.
- New tables: `tax_document_links`, `tax_document_classifications`,
  `tax_document_match_evidence`, `tax_document_review_events` (append-only,
  DB-trigger protected).
- `tax_document_links` uses a **dual-source model**: a link references *either* a
  canonical document *or* a Microsoft document (`num_nonnulls(document_id,
  microsoft_document_id) = 1`), so no binary is duplicated; the return column is
  nullable so unmatched documents persist reviewably without fabricated ownership.
- New `tax.document.review` capability; four document review work queues; the
  `tax_missing_items` foreign-key index (RC9 H20); legacy free-text Microsoft
  matching rules deactivated and `rule_type` CHECK-constrained.
- Exactly one Alembic head is maintained; migrations are fully reversible.

## Deterministic matching engine

- Ownership is established **only** from exact, deterministic identifiers: portal
  document-request provenance (confidence 1.0), exact drive/folder-id rules
  (0.95), and exact uploader-email identity (0.90). Fuzzy signals never
  contribute to the confidence score.
- Auto-assignment requires a single candidate at or above the auto-match
  threshold (0.90) with no competing candidate above the ambiguity floor (0.50).
  Everything else — ambiguous, below threshold, or duplicate — is routed to
  mandatory human review. No substring/containment matching exists anywhere.

## Portal ingestion

- Portal document uploads flow through the engine via
  `tax_intake.sync_documents → ingest_document`. Deterministic request provenance
  produces an accepted link and resolves the corresponding checklist and
  missing-information records. Ingestion is idempotent — replay returns the
  existing link.

## Microsoft ingestion

- Microsoft drive documents flow through the engine via
  `microsoft_document_sync.bridge_microsoft_documents_to_tax →
  ingest_microsoft_document`, preserving the verified deterministic
  `match_drive_item` behavior. Matched documents with a single return auto-accept;
  ambiguous ones go to review; unmatched documents are recorded as reviewable
  links (visible to firm-wide reviewers) rather than silently discarded. Re-sync
  is idempotent.

## Ambiguity and review workflow

- Staff document-review workspace (`/tax/documents`) and versioned APIs under
  `/api/v1/tax` for checklist, documents, review queue, and the reviewer actions
  accept / reject / reassign / classify / duplicate / revert.
- Reviewer actions enforce a current-status guard (stale or invalid actions
  return HTTP 409) so an old decision cannot overwrite a newer one, and are
  record-scope authorized against the affected return.

## Missing-information engine

- A deterministic, explainable calculator recomputes each return's missing set
  from accepted document links and reuses the existing portal-request,
  checklist-status, and workflow-gating mechanisms — no parallel engine.

## Authorization and audit controls

- New `tax.document.review` capability gates review actions, with a middleware
  carve-out so it is not shadowed by the coarse `tax.write` inference (the H4
  lesson from 0.9.7).
- Accept and reassign re-validate that the document's canonical owner belongs to
  the target return's client/household; cross-owner mismatches are denied (HTTP
  403) with an immutable `owner_mismatch_denied` audit event, even when the
  reviewer is authorized for the target return.
- All match, classification, and review decisions are recorded in append-only,
  DB-trigger-protected ledgers; auto-matches and denials emit immutable audit
  events. Least privilege and record-level authorization are preserved.

## Tests and adversarial validation

- 136 automated tests pass (up from 111), including a parametrized multi-dataset
  H13 regression suite and a full producer → ingestion → accept →
  missing-recompute end-to-end test.
- Independent RC11 adversarial validation initially found the engine
  security-sound but flagged that ingestion was unwired plus robustness gaps; the
  RC11 remediation wired both producers and closed every gap. The RC11 retest
  (43/43 adversarial checks across two harnesses) confirmed H13 cannot be
  recreated across nine datasets, the dual-source constraint holds, ingestion is
  idempotent, stale actions return 409, cross-owner attempts are denied 403, and
  no new gap was introduced — concluding **SAFE TO MERGE**.
- Clean installation, v0.9.7 upgrade/downgrade/re-upgrade with byte-identical
  sentinel preservation, startup/shutdown, 178-route OpenAPI, template, and
  append-only audit validation all passed.

## Known limitations

- The AI classifier is an inert interface-only port (no vendor, no external
  call); real AI classification and extraction are Epic 6.
- Bulk historical re-matching of previously auto-assigned Microsoft documents is a
  separate resumable job, not part of this release.
- Idempotency is first-link-wins: a document ingested unmatched and later
  re-ingested with a deterministic signal returns the existing link rather than
  auto-upgrading; such links are resolved by a reviewer.

## Deferred Epic 6 items

- AI-assisted classification, extraction, and recommendation (governed AI port).
- External provider and IRS transcript integration (Drake / UltraTax / Lacerte /
  CCH), which supplies transcript facts that may satisfy checklists.
- OCR / tax-fact extraction.

## Recommended next work

Per the revised Epic 5 plan (`docs/EPIC_5_REVISED_PLAN.md`): Sprint 5.5 — Tax
Exceptions (extensions, estimated payments, notices, amendments). The Release
0.9.8-adjacent performance and Microsoft-365 token-security debt (RC9 H10,
H15–H20) remains scheduled ahead of or alongside subsequent sprints.
