# RC11 — Independent Adversarial Validation of Sprint 5.4

**Subject:** Draft PR #18 (`feature/tax-document-intelligence`, commit `8b09b99`,
Alembic head `k1b92e0d9c8a`) — Tax Document Intelligence & deterministic matching.

**Mandate:** independent, adversarial validation. Do not assume the
implementation is correct. Attempt to defeat the deterministic matching engine,
recreate RC8 H13 with multiple datasets, and break every authorization/integrity
boundary. No application code modified.

**Method:** the actual committed engine, routes, and Microsoft sync were driven
against a fresh migrated database with 30+ adversarial cases (9 H13-recreation
datasets, engine threshold/confidence probes, ingestion/duplicate/ambiguity,
record/reviewer/queue authorization, orphan/replay/stale-decision, cross-client
defense-in-depth, and audit-integrity), plus a source scan for substring
constructs, a full migration up/down/re-up, and the full regression suite. The
service and migration were re-read line-by-line.

**Headline:** the **core security objective — eliminating substring ownership
matching (H13) — is fully achieved and verified** against nine adversarial
datasets, with all authorization and audit boundaries holding and the migration
fully reversible. **No security FAIL was found.** However, six CONCERNs were
confirmed: the document-intelligence ingestion entry point (`ingest_document`)
is **not wired to any producer**, so the review pipeline is dormant in
production; reviewer actions lack a status guard (API-reachable stale decisions);
and the ingest function has two latent unhandled-500 paths. On balance these
correctness/completeness gaps warrant remediation and a retest before merge.

---

## 1. Substring ownership — cannot reappear (PASS)

The RC8 H13 failure was recreated with multiple adversarial datasets against the
live `match_drive_item`. **Every one was correctly blocked** (returned
`(None, None)` — routed to review, never auto-assigned):

| Attack vector | Result |
|---|---|
| Folder name contains the client name (`/Clients/Ed Munson`) | **PASS** — no match |
| Parent path contains the client name inside another (`Fred Munson Family Trust/Ed Munson docs`) | **PASS** — no match |
| Filename contains the client name (`Ed Munson 2026 return.pdf`) | **PASS** — no match |
| Partial/substring email (`ed@` ⊂ `fred@example.com`) | **PASS** — no match |
| Uploader display name (not email) equals client name | **PASS** — no match |
| Alias / partial identifier email (`ed.munson.tax@…`) | **PASS** — no match |
| Legacy free-text substring rule (`rule_type='filename'`, pattern `return`) | **PASS** — inert, no match |
| Two people sharing an exact email (ambiguous) | **PASS** — no single match |
| Exact uploader email equals the client (positive control) | **PASS** — matches |

- **Source scan (PASS):** neither `app/services/tax_document_intelligence.py` nor
  `app/jobs/microsoft_document_sync.py` contains `in search_text`,
  `in normalized_parent`, `LIKE '%`, `.contains(`, `ilike(`, or `full_name in`.
  Ownership can only be established by exact identifier equality.
- **Verdict:** substring ownership matching is eliminated and cannot reappear
  through filenames, folder names, parent paths, uploader names, emails, aliases,
  or partial identifiers. This is the sprint's central requirement and it holds.

## 2. Matching engine — thresholds, confidence, ambiguity (PASS)

- Single deterministic candidate ≥ 0.90 → **accept**. **PASS**
- Two candidates above the 0.50 ambiguity floor → **review** (never auto-assign). **PASS**
- No evidence → **unmatched**. **PASS**
- Fuzzy/unknown "hint" signal type contributes **zero** to the score. **PASS**
- Confidence is the strongest single signal, capped at 1.0 (weak signals cannot
  combine into a false auto-match). **PASS**
- 0.90 boundary accepts (threshold inclusive). **PASS**

## 3. Ingestion behavior — provenance, duplicate, ambiguity (PASS)

- Portal-request provenance auto-accepts and resolves the checklist/missing item. **PASS**
- Exact-hash duplicate → duplicate **review**, never silent merge. **PASS**
- Exact-email identity with two candidate returns → **proposed** (review), never
  auto-assigned. **PASS**

## 4. Authorization — record, reviewer, queue (PASS)

- Read endpoints (`/api/v1/tax/returns/{id}/checklist` and `/documents`) return
  **404** for a return outside the caller's scope. **PASS**
- Reviewer with `tax.document.review` but no record scope → **PermissionError /
  403** on accept. **PASS**
- Reassign to a target return the reviewer is **not** authorized for → **blocked**
  (authorization is required for both the current and the target return). **PASS**
- Review queue is scoped via `list_engagements`; an outsider sees **0**. **PASS**
- Middleware maps document-review action paths to `tax.document.review` (carve-out
  before the generic tax rule), so a reviewer-only role is not shadowed by a
  `tax.write` demand (the H4 lesson was applied). **PASS**

## 5. Audit integrity (PASS)

- `tax_document_review_events` rejects **UPDATE** and **DELETE** (append-only
  trigger). **PASS**
- Reviewer actions and auto-matches write immutable audit and review-event rows;
  denials are audited via the 0.9.7 `audit_denied` pattern. **PASS**

## 6. Migration & rollback (PASS)

- Clean base→head; v0.9.7→head upgrade → downgrade → re-upgrade. **PASS**
- Downgrade removes all four `tax_document_*` tables, the `tax.document.review`
  capability, the four review queues, the append-only triggers, and the
  Microsoft-rule CHECK constraint. **PASS**
- Exactly one Alembic head (`k1b92e0d9c8a`) throughout. **PASS**
- Full regression suite: **111 passed**, zero failures. **PASS**
- *Note (not a failure):* downgrade does not re-activate the legacy Microsoft
  matching rules it deactivated on upgrade — a documented one-way data change,
  consistent with the platform's prior normalization migrations.

## 7. Orphan / duplicate prevention (PASS for the constraint; see C1/C2)

- The partial unique index `uq_tax_document_link_accepted (document_id,
  tax_engagement_return_id) WHERE status='accepted'` **correctly prevents** two
  accepted links for the same document/return pair — verified because a replay
  attempt hit it. The DB-level orphan/duplicate guard works. **PASS**
- The *graceful handling* of that collision is a concern — see C2.

---

## CONCERNS

None of the following is a security bypass, a cross-client disclosure, or an
API-reachable data-corruption path. They are correctness, completeness, and
robustness gaps.

### C1 — `ingest_document` unmatched path can crash (NOT NULL) — CONCERN
An unmatched document whose owning person has **zero (or ≠ one)** tax returns
raises `NotNullViolation` on `tax_document_links.tax_engagement_return_id`
(the fallback `_any_return` returns `None`, but the column is `NOT NULL`). The
design specifies an "unmatched queue" for exactly these documents, but the schema
cannot persist an unmatched link with no return.
- **Reachability:** latent — `ingest_document` has **no caller in `app/`** (see
  C5), so this is not reachable via any endpoint today.
- **Fix direction:** make `tax_engagement_return_id` nullable for
  unmatched/proposed links (and adjust the unmatched-queue query), or model
  unmatched documents without a link row.

### C2 — `ingest_document` replay is an unhandled 500 — CONCERN
Re-ingesting an already-accepted `(document, return)` pair raises
`UniqueViolation` (the C7 constraint firing) instead of a graceful idempotent
no-op. The constraint protecting against duplicate accepted links is *correct*;
the lack of idempotency/handling is the concern.
- **Reachability:** latent — no wired caller (C5). Must be fixed before ingest is
  wired to a producer.
- **Fix direction:** check for an existing accepted link (or catch the
  `IntegrityError`) and return idempotently.

### C3 — Reviewer actions have no current-status guard (stale decisions) — CONCERN
`review_action` applies `accept`/`reject`/etc. regardless of the link's current
status, so a link already **accepted** by one reviewer can be **rejected** by
another (confirmed: status flipped to `rejected`). Authorization is still
enforced on every action and the append-only ledger preserves the full sequence,
so there is no privilege bypass or history loss — but a stale decision can
silently override a fresh one.
- **Reachability:** **API-reachable** via
  `POST /api/v1/tax/documents/{link}/{action}` (two reviewers, or one clicking
  twice with no optimistic-concurrency check).
- **Fix direction:** guard on the expected current status (e.g. only act on
  `proposed`), or use optimistic concurrency / row locking.

### C4 — `accept`/`reassign` do not re-validate document-owner vs return-owner — CONCERN (defense-in-depth)
`review_action` accept trusts the link's existing return binding and does not
re-check that the document's canonical owner is consistent with the return's
client. A pre-existing cross-owner *proposed* link, if accepted, would attach one
client's document to another client's checklist.
- **Reachability / severity:** **not exploitable for disclosure** — every action
  requires the reviewer be authorized for the target return, so a reviewer can
  only ever attach documents to returns already within their authorized scope; no
  privilege escalation occurs. It is also **not reachable via any wired producer**
  — `ingest_document` derives the return from the document's own signals (never
  cross-owner) and `reassign` requires authorization for both returns. It is a
  data-quality / defense-in-depth gap only.
- **Fix direction:** re-validate `validate_ownership(document_owner, return)` on
  accept, or flag cross-owner attachments for explicit confirmation.

### C5 — Document-intelligence ingestion is not wired to any producer — CONCERN (functional completeness)
`ingest_document` — the single entry point that runs the engine and creates
`tax_document_links` rows — has **no caller anywhere in `app/`**. Neither the
portal-upload path nor the Microsoft document sync calls it. Consequently, in
production, `tax_document_links` receives no rows, the review queue
(`/api/v1/tax/documents/review`, `/tax/documents`) is **empty**, and the missing
-information engine's document-link inputs never populate. The PR body describes
an end-to-end flow ("documents … flow through one matching engine") that is not
actually connected.
- **Important distinction:** the **H13 security fix is separately wired and live**
  at `match_drive_item` (the Microsoft sync assigns `microsoft_documents.person_id`
  by exact identifiers only). So substring matching is eliminated at the real
  enforcement point regardless of C5. What is *not* wired is the richer
  `tax_document_links` review/classification/missing-info pipeline built this
  sprint.
- **Fix direction:** wire portal upload confirmation and the Microsoft sync (or a
  reconciliation job) to call `ingest_document`, after C1–C4 are resolved.

### C6 — New-test coverage gaps — CONCERN
The 14 new tests cover the happy paths and the core authorization/append-only
guarantees well, but do **not** lock in the edge cases RC11 exercised:
- no test for the unmatched-owner-0-returns crash (C1);
- no test for replay of an accepted document (C2);
- no test for stale review decisions / status guard (C3);
- no test for cross-owner accept (C4);
- no test that reassign requires **target**-return authorization (verified here, untested in suite);
- H13-dataset regression is thin in the committed suite (one folder-name case in
  the Microsoft-sync tests plus a source-scan assertion; the parent-path,
  filename, partial-email, display-name, and alias datasets exercised here are not
  in the suite).
- **Fix direction:** add the above as regression tests (several map directly to
  the RC11 harness cases).

---

## RETEST REQUIRED

After C1–C5 are addressed — specifically: wire `ingest_document` to the portal
and Microsoft producers with idempotent replay handling (C2) and persistable
unmatched links (C1), add a review-action status guard (C3), re-validate
document/return ownership on accept (C4), and add the C6 regression tests — a
full RC11 re-run is required to confirm the end-to-end pipeline and that no new
cross-client or authorization gap is introduced by the wiring.

---

## Findings summary

| Area | Result |
|---|---|
| H13 recreation — 9 adversarial datasets | **PASS** |
| Substring constructs absent (source scan) | **PASS** |
| Engine thresholds / confidence / ambiguity | **PASS** |
| Portal provenance / duplicate / email-ambiguity ingestion | **PASS** |
| Record authorization (read endpoints 404) | **PASS** |
| Reviewer authorization (accept / reassign target) | **PASS** |
| Queue authorization (scoped) | **PASS** |
| Audit integrity (append-only UPDATE/DELETE blocked) | **PASS** |
| Orphan/duplicate prevention (unique partial index) | **PASS** |
| Migration up/down/re-up, single head, full suite (111) | **PASS** |
| `ingest_document` unmatched → NOT NULL 500 (latent) | **CONCERN (C1)** |
| `ingest_document` replay → UniqueViolation 500 (latent) | **CONCERN (C2)** |
| Reviewer actions lack status guard (stale decisions, API-reachable) | **CONCERN (C3)** |
| accept/reassign don't re-validate doc/return owner (defense-in-depth) | **CONCERN (C4)** |
| Ingestion pipeline not wired to any producer (feature incomplete) | **CONCERN (C5)** |
| New-test coverage gaps | **CONCERN (C6)** |
| End-to-end retest after wiring | **RETEST REQUIRED** |

No **FAIL** findings. No security bypass, cross-client disclosure, or
API-reachable data-corruption path was found.

---

## Recommendation

# DO NOT MERGE

The security core of this sprint is sound: substring ownership matching is
eliminated and cannot be recreated through any of the tested vectors, every
authorization boundary holds, the audit ledger is immutable, and the migration is
reversible with a single head and a green regression suite. **There is no
security FAIL.**

The recommendation to withhold merge is on **correctness and completeness**
grounds, not security:

1. **C5 — the sprint's primary deliverable is not wired.** `ingest_document` has
   no producer, so the `tax_document_links` review/classification/missing-info
   pipeline is dormant in production and the PR's described end-to-end flow does
   not occur. (The H13 fix itself is live at `match_drive_item` and would survive
   independently.)
2. **C3 — an API-reachable stale-review-decision gap** with no status guard.
3. **C1/C2 — two latent unhandled-500 paths in `ingest_document`** that must be
   fixed before it is wired to any producer.

Resolve C1–C5, add the C6 regression tests, and re-run RC11 (RETEST REQUIRED). If
the team prefers, the wired-and-verified H13 change at `match_drive_item` could be
merged on its own as a targeted security fix while the `tax_document_links`
pipeline is completed — but the PR as submitted should not merge as a complete
feature.

---

*RC11 conducted as an independent adversarial review. No application code was
modified and nothing was committed as part of this validation.*

---

## Remediation Appendix (post-RC11)

Remediation was implemented on `feature/tax-document-intelligence` (new migration
`l2c03f1e0d9b`, head advanced from `k1b92e0d9c8a`). Every RC11 CONCERN is
addressed below; the independent re-validation is `docs/RC11_RETEST.md`.

| RC11 | Remediation | Where |
|---|---|---|
| **C1** unmatched → NOT NULL crash | `tax_engagement_return_id` made nullable; unmatched links persist with a NULL return and **no fabricated ownership** (`_any_return` removed). | migration `l2c03f1e0d9b`, `_ingest` |
| **C2** replay → 500 | Idempotent ingestion: `_existing_link` returns the existing non-rejected link on replay, with an `IntegrityError` backstop for concurrent races. Microsoft re-sync skips already-linked documents. | `_ingest`, `_existing_link`, `bridge_microsoft_documents_to_tax` |
| **C3** stale review decisions | `ALLOWED_FROM` status guard; incompatible actions raise `StaleReviewError` → **HTTP 409**. Unmatched links cannot be accepted until reassigned to a return. | `review_action`, `app/routes/tax_documents.py` |
| **C4** cross-owner accept/reassign | `accept` and `reassign` re-validate the document's canonical owner against the target return's client/household; a mismatch is denied (**403**) with an immutable `owner_mismatch_denied` audit event, even when the reviewer is authorized for the return. | `review_action` |
| **C5** ingestion not wired | Portal uploads wired via `tax_intake.sync_documents → ingest_document`; Microsoft documents wired via `bridge_microsoft_documents_to_tax → ingest_microsoft_document`. Links now reference **either** a canonical document or a Microsoft document (dual-source model), so no binary is duplicated. Unmatched documents are recorded reviewably (visible to firm-wide reviewers), never silently discarded. | `tax_intake.py`, `microsoft_document_sync.py`, migration `l2c03f1e0d9b` |
| **C6** test coverage | Added `tests/test_tax_document_remediation.py` (25 tests): portal/Microsoft ingestion invocation, zero/multiple candidate returns, replay of unmatched and accepted documents, Microsoft-bridge idempotency, duplicate review prevention, stale accept/reject/reassign (409), cross-owner accept/reassign denial (403), reviewer target authorization, unmatched firm-wide visibility, a parametrized multi-dataset H13 suite, and a full producer → ingest → accept → missing-recompute end-to-end test. | tests |

**Preserved:** the already-verified deterministic `match_drive_item` behavior is
unchanged; substring ownership matching remains eliminated. Least privilege,
immutable append-only audit, and record-level authorization are preserved. The
AI classifier remains an inert interface. Exactly one Alembic head
(`l2c03f1e0d9b`); migrations are fully reversible with sentinel preservation.

**Validation after remediation:** 136 automated tests pass (was 111);
compilation, startup/shutdown, 178-route OpenAPI, template render, clean
base→head, v0.9.7 upgrade/downgrade/re-upgrade with byte-identical sentinel data,
and `git diff --check` all clean.
