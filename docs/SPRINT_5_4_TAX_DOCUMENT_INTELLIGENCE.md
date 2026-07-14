# Sprint 5.4 — Tax Document Intelligence & Missing Information

**Status:** accepted — implemented and RC11-remediated in
`feature/tax-document-intelligence` (draft PR, head `l2c03f1e0d9b`). See the RC11
remediation appendix in `docs/RC11_VALIDATION.md`.

> **RC11 remediation (post-review):** the ingestion pipeline is now wired to both
> producers — portal uploads via `tax_intake.sync_documents → ingest_document`,
> and Microsoft drive documents via
> `microsoft_document_sync.bridge_microsoft_documents_to_tax → ingest_microsoft_document`.
> Links now reference **either** a canonical document or a Microsoft document
> (dual-source model), unmatched documents persist with a NULL return (no
> fabricated ownership) and are surfaced only to firm-wide reviewers, ingestion is
> idempotent (replay returns the existing link), reviewer actions enforce a
> current-status guard (409 on stale actions), and accept/reassign re-validate
> document-owner vs return-owner (403 + denied audit on cross-owner mismatch).
**Epic:** 5 (Tax Practice Platform), per `docs/EPIC_5_REVISED_PLAN.md`.
**Baseline:** Release v0.9.7 (`main`, Alembic head `j0a81f9c8d7e`).
**Target release:** v0.9.9 (assumes Release 0.9.8 performance/token debt lands first,
per the revised plan; if 0.9.8 slips, all new read paths in this sprint must use
SQL-side aggregation from the start).

---

## 1. Objectives

Deliver the tax document-intelligence layer that connects *received* documents to
the checklist / missing-information scaffolding built in Sprint 5.2, and **replace
the substring-based Microsoft document matching (RC8 finding H13) with a
deterministic, authorization-aware, confidence-scored matching engine that routes
every ambiguous case to mandatory human review.**

Concretely, at the end of Sprint 5.4:

1. Every active return can generate an explainable, return-type/year-specific
   document checklist (reusing the Sprint 5.2 `tax_checklist_*` schema — not a new
   one).
2. Documents arriving from **portal uploads** (already deterministic) and
   **Microsoft drive sync** (currently unsafe) flow through **one** matching
   engine that assigns a document to a person/return **only** on deterministic
   evidence above a confidence threshold, and routes everything else to a review
   queue.
3. No substring/containment heuristic can auto-assign one client's document to
   another (H13 closed).
4. Missing-information is recalculated from accepted document matches, drives the
   existing portal document requests, and blocks preparation workflow steps.
5. Staff have a document workspace, classification/matching review queue, and
   missing-information panel; clients see only their own request status.
6. An AI-classifier **port** exists (interface + contract tests) with **no vendor
   implementation** — the real AI classifier is Epic 6.

**Non-goals (explicitly deferred):** OCR / tax-fact extraction, real AI
classification vendor, transcript facts satisfying checklists (Epic 6 provider
work), and bulk historical document backfill (a separate resumable job).

---

## 2. Architecture

### 2.1 Principles (consistent with the platform)

- **Canonical documents stay canonical.** `documents` and `document_versions`
  remain the only binary store. Tax adds link/classification metadata; no binary
  is copied.
- **One matching engine, two ingestion sources.** Portal uploads and Microsoft
  drive items both resolve through a single `tax_document_matching` service.
  Ingestion adapters produce a normalized *document candidate* with evidence; the
  engine scores and decides; a review service arbitrates ambiguity.
- **Deterministic-first, default-deny.** A document is auto-assigned only when a
  single candidate person/return is supported by deterministic evidence above the
  auto-match threshold. Weak, ambiguous, or conflicting evidence → review queue,
  never an auto-assignment.
- **Reuse platform services.** `work_queues` (review queues), `record_assignments`
  (reviewer routing + authorization), `portal_document_requests` (client
  requests), workflow steps (preparation blocking), timeline + immutable audit,
  and the canonical record-scope authorization service
  (`app/security/authorization.py`).
- **Provenance and confidence are first-class.** Every match records its evidence
  signals, the resulting confidence, the decider (engine / rule / reviewer /
  ai-port), and is reversible via an append-only event log.

### 2.2 Data flow

```
Ingestion source ─► normalized DocumentCandidate(evidence[]) ─► Matching engine
     │                                                              │
  (a) Portal upload   evidence: request_id (deterministic)         │ score signals
  (b) MS drive item   evidence: exact email id, registered         │ + ownership
                      drive/folder rule, hash                       │   validation
                                                                    ▼
                                        ┌── single candidate ≥ auto-threshold ──► ACCEPTED link
                                        │        (recorded, reversible, audited)
                                        ├── ambiguous / below threshold ────────► REVIEW queue
                                        │                                          (mandatory human)
                                        └── no candidate ─────────────────────────► UNMATCHED queue

ACCEPTED link ─► classify to checklist item ─► resolve tax_missing_items ─► recompute missing engine
                                                                              │
                                                          ┌───────────────────┴──────────────────┐
                                                    portal request status                 workflow step gate
```

### 2.3 Where this replaces existing behavior

- `app/jobs/microsoft_document_sync.py::match_drive_item` — the substring branches
  (`email in search_text` → `embedded_email`, `full_name in normalized_parent` →
  `folder_name`, and the rule `pattern in target` containment) are **removed** and
  replaced by calls into the new engine (see §7).
- `app/services/tax_intake.py::sync_documents` — the current portal-request →
  checklist-item resolution is **generalized** into the missing-information engine
  (§9) but keeps its deterministic provenance semantics.

---

## 3. Document matching strategy (the core of this sprint)

### 3.1 Substring matching is eliminated

The following are **removed** and must never reappear:
- `email in search_text` (partial email containment).
- `full_name in normalized_parent` (partial name containment in a folder path).
- `pattern in target` free-text containment for admin rules.

No matcher may use Python `in` / `LIKE '%…%'` substring containment against
person names or emails to establish document ownership.

### 3.2 Deterministic identifiers (the only auto-match evidence)

A match is proposed only from **deterministic, boundary-exact** signals, each
carrying a fixed confidence weight (final weights tuned during implementation;
illustrative below):

| Signal | Evidence | Boundary rule | Weight |
|---|---|---|---|
| **Portal request provenance** | Document uploaded against a specific `portal_document_requests.id` | Exact FK; the request already binds person/household/return/checklist-item | **1.00 (deterministic)** |
| **Registered drive/folder mapping** | Microsoft `drive_id` or folder `item_id` explicitly mapped to a person by an admin rule | **Exact equality** of the identifier (not a pattern) | 0.95 |
| **Exact uploader identity** | `createdBy`/`lastModifiedBy` email **equals** a person's `normalized_email` | Exact normalized-email equality | 0.90 |
| **Document hash → known document** | `documents.sha256` equals an already-classified document | Exact hash equality | 0.90 (duplicate handling, §3.5) |
| **Explicit reviewer decision** | A prior manual match/override for the same drive item or hash | Exact identifier | 1.00 |

Weak, discretionary, or fuzzy signals (name-in-path, email-in-text, filename
keywords) are **not** ownership evidence. They may at most be surfaced to a human
reviewer as *hints* on the review card, clearly labelled non-authoritative, and
they never contribute to the auto-match score.

### 3.3 Confidence scoring and thresholds

- Each candidate person/return accumulates a confidence from its matched signals
  (capped at 1.0; deterministic provenance short-circuits to 1.0).
- **Auto-match** requires: exactly **one** candidate with confidence ≥
  `AUTO_MATCH_THRESHOLD` (proposed 0.90) **and** no other candidate ≥
  `AMBIGUITY_FLOOR` (proposed 0.50).
- **Review** (mandatory human): best candidate below the auto threshold, **or**
  two or more candidates above the ambiguity floor (conflicting owners), **or**
  any tax-sensitive category flagged for always-review (configurable).
- **Unmatched**: no candidate reaches the ambiguity floor.
- Thresholds are configuration constants (not per-user input), surfaced in the
  design and adjustable via a single settings location; changes are audited.

### 3.4 Authorization-aware ownership validation

Before a proposed match becomes an **accepted** link, the engine validates that
the assignment is legitimate — matching by identifier is necessary but not
sufficient:

1. **Candidate is a real, active person** with a resolvable household.
2. **Tax context consistency:** the matched person must own (or share via
   household) at least one engagement/return the document could plausibly attach
   to; a document with no tax relationship to the matched person is routed to
   review, not accepted.
3. **No cross-client widening:** an accepted link binds the document to exactly
   the person the deterministic evidence supports. The engine never assigns to a
   *different* person than the evidence identifies, and never to more than one
   client.
4. **Reviewer authorization:** when a human accepts/overrides a match, the
   canonical record-scope check (`record_in_scope(principal, "person"/"household",
   …, write=True)` or tax office/assignment scope via `list_engagements`) must
   pass for the target return; a reviewer cannot accept a document onto a return
   outside their authorized scope. Denied attempts emit an immutable
   `outcome="denied"` audit event (reusing the 0.9.7 `audit_denied` pattern).

This makes ownership a function of *deterministic evidence + explicit
authorization*, structurally preventing the H13 cross-client exposure.

### 3.5 Duplicate handling

Duplicates are detected by **exact `documents.sha256` equality** (no fuzzy
matching). A new document whose hash matches an existing classified document
inherits a *proposed* link to the same return/checklist context but is routed to
a lightweight **duplicate-review** queue rather than silently merged, so a
coincidental hash collision or a genuinely re-sent file is confirmed by a human
before it resolves a checklist item.

---

## 4. Microsoft document ingestion

- The Microsoft drive sync job continues to enumerate drive items and persist
  `microsoft_documents` rows (delta-aware sync is unchanged). It **stops** calling
  the substring matcher.
- For each new/changed drive item, the sync produces a normalized
  `DocumentCandidate` carrying only deterministic evidence (exact uploader email,
  drive/folder identifiers, hash) and hands it to the matching engine.
- Items with no deterministic evidence are stored as `microsoft_documents` with
  `status='pending'` and enter the **unmatched** review queue — the existing
  review-queue pattern, but now the *default* outcome for anything not
  deterministically identified (previously the code auto-assigned on a single weak
  substring hit).
- The admin matching-rules table (`microsoft_document_matching_rules`) is
  **repurposed** from free-text `pattern` containment to **structured exact
  rules**: `rule_type` values become `drive_id`, `folder_item_id`, or
  `email_exact`, evaluated by equality. Existing free-text patterns are migrated
  to a disabled/`legacy` state (never silently reinterpreted) and surfaced for
  admin re-entry (see §12). No rule may express substring containment.
- SharePoint/OneDrive provenance (drive id, item id, web URL, provider hash) is
  preserved on the tax document link for audit and safe navigation; binaries are
  never copied.

---

## 5. Checklist integration

- **Reuse** the Sprint 5.2 schema: `tax_checklist_templates`,
  `tax_checklist_template_items`, `tax_checklist_items` (with
  `portal_document_request_id`, `required`, `document_id`, `status`), and
  `tax_missing_items`. Sprint 5.4 adds *classification and matching* on top; it
  does not create a parallel checklist model.
- When a document is **accepted** to a return, it is classified to a checklist
  item by, in priority order: (a) the portal request's `template_item_id`
  (deterministic), else (b) a deterministic category rule mapping document
  category → checklist item, else (c) routed to classification review if the
  checklist item is ambiguous.
- Accepting a document that satisfies a checklist item transitions that
  `tax_checklist_items` row to `received` and sets `document_id` (generalizing
  today's `sync_documents`), and marks the corresponding `tax_missing_items` row
  `resolved` — all inside one transaction, with an append-only match/resolution
  event.

---

## 6. Missing-information engine

- A pure, explainable calculator recomputes, per return: for each **required**
  checklist item, whether an accepted, non-duplicate document satisfies it;
  unsatisfied required items are the *missing set*.
- Outputs drive three existing mechanisms (no new engines):
  1. **Portal requests** — open/overdue `portal_document_requests` reflect the
     missing set; resolved items close their request.
  2. **Workflow gating** — preparation steps are blocked while critical required
     items are missing (via the existing workflow step/idempotent event
     mechanism); unblocked when resolved.
  3. **Queues** — `missing critical`, `waiting on client`, `uploaded awaiting
     review` reuse `work_queues` criteria.
- The calculator is deterministic and unit-testable in isolation (no I/O), taking
  checklist + accepted-link state and returning the missing set with reasons.

---

## 7. Human-review workflow

Mandatory human review is the default for anything not deterministically
resolved. The review workflow:

1. **Queues** (reusing `work_queues`): `unmatched` (no candidate),
   `match review` (ambiguous/below-threshold), `duplicate review`, `classification
   review`, `low confidence`. Routing uses existing `record_assignments`
   automatic rules by office/return-type/document-type.
2. **Review actions** (all authorization-checked and audited): accept a proposed
   match, reject, reassign to a different (authorized) return, classify to a
   checklist item, mark duplicate, or send back to client (re-request). Every
   action writes an append-only `tax_document_review_events` row and an immutable
   audit event; denied (out-of-scope) attempts are audited as `denied`.
2. **No silent auto-resolution of ambiguity.** The engine never resolves a
   checklist item from an ambiguous match; only an authorized reviewer's accept,
   or a deterministic auto-match, resolves it.
3. **Reversibility.** An accepted match can be reverted by an authorized reviewer;
   the checklist item and missing-item recompute accordingly. History is
   preserved (append-only), never deleted.

---

## 8. Database changes

All additive, one linear head with parent `j0a81f9c8d7e`. New tables adopt the
CHECK-constraint / lookup discipline (RC9 H21) and index every foreign key and
hot filter column from the start (RC9 H20). Proposed tables (names to be
confirmed against conventions in an ADR):

- **`tax_document_links`** — document ↔ return (and optional checklist item):
  `document_id` (FK), `tax_engagement_return_id` (FK), `tax_checklist_item_id`
  (FK, nullable), `status` CHECK in (`proposed`,`accepted`,`rejected`,`superseded`),
  `confidence` (numeric, CHECK 0–1), `match_source` CHECK in
  (`portal_request`,`drive_rule`,`email_exact`,`hash`,`manual`,`ai_port`),
  `matched_by_user_id` (FK, nullable), timestamps. Unique constraint preventing
  duplicate accepted links per (document, return). Indexes on `document_id`,
  `tax_engagement_return_id`, `tax_checklist_item_id`, `(status, confidence)`.
- **`tax_document_classifications`** — `document_id` (FK), `label`/`category`
  (lookup or CHECK-constrained), `confidence` (numeric, CHECK 0–1), `source` CHECK
  in (`deterministic`,`rule`,`manual`,`ai_port`), `reviewer_user_id` (FK,
  nullable), provenance JSON (bounded), timestamps. Indexed on `document_id`.
- **`tax_document_match_evidence`** (optional, for explainability) — append-only
  per-signal evidence for a proposed match: `document_link_id` (FK), `signal_type`
  CHECK-constrained, `value_hash`, `weight`. Indexed on `document_link_id`.
- **`tax_document_review_events`** — append-only review/override ledger:
  `document_link_id` (FK), `action` CHECK-constrained, `actor_user_id` (FK),
  `reason`, `metadata` JSON, `created_at`. Protected by an append-only trigger
  (reusing the platform's mutation-prevention pattern). Indexed on
  `document_link_id`.
- **Column adjustments:** add the missing index on
  `tax_missing_items.tax_engagement_return_id` (RC9 H20) if not delivered in 0.9.8;
  extend `microsoft_document_matching_rules.rule_type` to the structured exact
  types and add a CHECK constraint (see §12 for legacy-row migration).

No new binary storage. No changes to `documents`/`document_versions` structure.

---

## 9. APIs

All under `/api/v1/tax`, versioned, with capability + office + record + portal
scope, masked identifiers, pagination, and the shared response envelope (adopting
the consistency RC8 recommended rather than a per-router shape):

- `GET /api/v1/tax/returns/{id}/checklist` — checklist items, status, and missing
  set with reasons.
- `GET /api/v1/tax/returns/{id}/documents` — accepted document links with
  classification, confidence, provenance, and safe download references.
- `GET /api/v1/tax/documents/review` — review queues (unmatched / ambiguous /
  duplicate / classification), scoped to the caller's authorized returns.
- `POST /api/v1/tax/documents/{link_id}/accept` — accept a proposed match
  (authorization-validated; audited).
- `POST /api/v1/tax/documents/{link_id}/reject` — reject a proposed match.
- `POST /api/v1/tax/documents/{link_id}/reassign` — reassign to a different
  authorized return.
- `POST /api/v1/tax/documents/{link_id}/classify` — set/confirm checklist-item
  classification.
- `POST /api/v1/tax/documents/{link_id}/duplicate` — confirm/deny duplicate.
- `POST /api/v1/tax/returns/{id}/missing/recompute` — recompute missing set (also
  triggered internally on accept/reject).
- Portal (client-facing, scoped): `GET /api/v1/portal/tax/requests` (request
  status only — never classifier internals or other clients' data); uploads
  continue through the existing portal request-upload endpoint.

Every mutating endpoint maps `ValueError`→400/404 and `PermissionError`→403
consistently, and re-uses the canonical tax authorization helper on every call
(the pattern hardened in 0.9.7).

---

## 10. UI

Staff (reuse the `base.html` shell; **ensure the tax dashboard CSS classes are
actually styled** — an open RC8 gap that this sprint's document workspace should
close):

- **Tax document workspace** per return: checklist progress, accepted documents
  with classification + confidence + provenance, and the missing-information panel.
- **Match review queue**: cards showing the document, its deterministic evidence,
  confidence, non-authoritative hints (clearly labelled), and accept / reject /
  reassign / classify / duplicate actions.
- **Unmatched documents** and **duplicate review** views.

Portal (client): request status and upload only; no classifier internals, no
confidence scores, no other-client data.

---

## 11. Security considerations

- **H13 closed by construction:** no substring ownership matching; auto-match only
  on exact deterministic identifiers with single-candidate + threshold + ambiguity
  guards; everything else to mandatory human review.
- **Authorization on every action:** reviewers can only accept/reassign documents
  onto returns within their record/office scope; portal clients see only their own
  request status. Denied attempts audited as `denied`.
- **Least privilege:** a new `tax.document.review` capability (composed into
  preparer/reviewer roles) gates the review actions; `tax.read`/`tax.write` govern
  read/attach as today. No capability is widened.
- **Data minimization:** classification/AI-port inputs are minimized; provenance
  JSON is bounded and excludes document contents and sensitive identifiers;
  quarantine/scanning states block download of unverified content.
- **Immutable trail:** match, classification, and review decisions are append-only
  and auditable; timeline publishes *document requested/received/accepted* and
  *checklist complete* milestones only — never document contents or extracted
  facts.
- **AI port is inert:** the AI classifier interface performs no external calls in
  this sprint; enabling any provider requires explicit approval and its own review
  (Epic 6).

---

## 12. Migration plan

- One additive Alembic revision, parent `j0a81f9c8d7e`, single head. Clean-install
  base→head, v0.9.7→head upgrade, downgrade→v0.9.7, re-upgrade, and sentinel
  preservation all validated.
- New tables only; no rewrite of `documents`, `document_versions`, or the Sprint
  5.2 checklist tables.
- **`microsoft_document_matching_rules` legacy handling:** existing free-text
  `pattern` rules are **not** silently reinterpreted under the new exact-match
  semantics (that could change who a document matches). The migration marks
  existing rows `legacy`/inactive and the new engine ignores legacy rules; admins
  re-enter structured exact rules. This is documented as an explicit, reversible
  behavior change.
- **No document re-matching backfill in this migration.** Re-evaluating already
  auto-assigned Microsoft documents against the new engine (to catch historical
  H13 mis-assignments) is a **separate, resumable, dry-run-first job** with counts
  and idempotency — planned within the sprint but run deliberately, not as a
  migration side effect.
- Downgrade drops the new tables and restores the prior `rule_type`/matcher
  behavior; destructive effects (loss of new links/review history) are stated
  explicitly before approval.

---

## 13. Testing strategy

- **Anti-H13 regression (mandatory):** explicit tests proving no substring/partial
  name or email can auto-assign a document; the "Ed Munson" ⊂ "Fred Munson"
  collision from RC8 must route to review, not auto-assign.
- **Matching engine unit tests:** deterministic signal weighting; single-candidate
  auto-match; ambiguity (two candidates above floor) → review; below-threshold →
  review; no candidate → unmatched; exact-email vs. partial-email; registered
  drive/folder exact rule vs. legacy substring rule (latter inert).
- **Authorization-aware ownership:** reviewer cannot accept a document onto an
  out-of-scope return (403 + denied audit); auto-match never crosses client
  boundaries; portal isolation (client sees only own requests).
- **Missing-information engine:** pure calculator fixtures; required vs. optional;
  resolution on accept; re-open on revert; workflow-step gating; portal request
  reflection.
- **Duplicate handling:** exact-hash duplicate → duplicate-review queue, not
  silent resolution.
- **Checklist integration:** portal-upload provenance resolves the right checklist
  item; classification review path.
- **AI port contract tests:** interface honored; no external call; inert by
  default.
- **Platform gates:** compilation, startup/shutdown, route + OpenAPI, template
  render, timeline/audit, queues, migration up/down/re-up, one Alembic head,
  full regression suite green.

---

## 14. Acceptance criteria

1. Every active return generates an explainable checklist (reusing Sprint 5.2
   schema) with a computed missing set and reasons.
2. **No substring/containment matcher exists in the codebase; no partial name or
   email can auto-assign a document.** The RC8 H13 collision case routes to review.
3. Auto-assignment occurs **only** on a single deterministic-identifier candidate
   above the auto-match threshold with no competing candidate above the ambiguity
   floor; all other documents enter a mandatory human-review queue.
4. Ownership is validated against deterministic evidence **and** reviewer
   authorization; no reviewer can attach a document to an out-of-scope return, and
   no document is assigned to more than one client. Denied attempts are audited.
5. Accepted documents resolve the correct checklist item and missing-information
   record; missing information drives portal requests and blocks/unblocks
   preparation workflow steps.
6. Portal clients see only their own request status; no classifier internals or
   other-client data are exposed.
7. Duplicate documents (exact hash) route to duplicate review, not silent
   resolution.
8. Clean install and v0.9.7 upgrade/downgrade/re-upgrade preserve sentinel data
   with exactly one Alembic head; new tables carry FK indexes and CHECK
   constraints.
9. Full regression suite plus the anti-H13 and authorization negative tests pass.
10. The AI classifier port exists with contract tests and performs no external
    call; the Microsoft matching-rule legacy migration is explicit and reversible.

---

*Design submitted for review. No application code has been written and nothing has
been committed for Sprint 5.4. Awaiting approval before implementation.*
