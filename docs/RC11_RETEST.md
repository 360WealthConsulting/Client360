# RC11 Retest — Independent Adversarial Re-Validation of Sprint 5.4

**Subject:** Draft PR #18 (`feature/tax-document-intelligence`) after RC11
remediation — commit `1aa0037`, Alembic head `l2c03f1e0d9b`.

**Mandate:** independent adversarial retest. Confirm every RC11 CONCERN is
resolved, that the remediation introduced no new gap, and that the core H13
security property still holds. No application code modified.

**Method:** (1) re-ran the **original RC11 adversarial harness** unchanged
(H13 datasets, engine, ingestion, authorization, audit, orphan/replay/stale)
against a fresh migrated database; (2) ran a **new remediation-surface harness**
attacking the dual-source constraint, route-level 409/403 mapping, idempotency
correctness, unmatched handling, and producer wiring; (3) re-verified migration
up/down/re-up with sentinel preservation and the full regression suite. The
refactored service, routes, and both migrations were re-read.

**Result:** **43 of 43 adversarial checks pass (0 FAIL, 0 CONCERN)** across the
two harnesses; the full regression suite passes (136); the migration is fully
reversible with one head and byte-identical sentinel data. **All six RC11
CONCERNs are resolved and no new defect was found.**

---

## 1. RC11 CONCERNs — re-verified resolved

| RC11 | Prior finding | Retest result |
|---|---|---|
| **C1** | Unmatched doc (owner 0/≠1 returns) → NOT NULL 500 | **PASS** — `ingest_document(doc, [])` for an owner with zero returns now persists a `proposed` link with **NULL return** (no fabricated ownership); no crash. |
| **C2** | Replay → unhandled `IntegrityError` 500 | **PASS** — replay of an accepted document returns the **same** link id; a distinct document with the same signal correctly gets a **distinct** link (idempotency keys on the source, not the signal — no false merge). |
| **C3** | Stale review decisions (no status guard) | **PASS** — `accept` then `reject` on the same link raises `StaleReviewError` → **HTTP 409** at the route; accept-after-reject and reassign-after-accept likewise guarded. |
| **C4** | accept/reassign don't re-validate owner vs return | **PASS** — a cross-owner link (client A's document pointed at client B's return) is **denied 403** on accept even by a reviewer authorized for B, and an immutable `tax.document.owner_mismatch_denied` audit event is written; cross-owner reassign likewise denied. |
| **C5** | Ingestion not wired to any producer | **PASS** — portal upload → `sync_documents` → `ingest_document` produces an accepted link and resolves the checklist; Microsoft matched document → `bridge_microsoft_documents_to_tax` → accepted link; Microsoft **unmatched** document → `proposed` NULL-return link (**not discarded**). |
| **C6** | Test-coverage gaps | **PASS** — `tests/test_tax_document_remediation.py` (25 tests) covers all gaps; combined suite is green. |

## 2. Core H13 security property — still holds

The original harness's nine H13-recreation datasets all remain blocked:
folder name, parent path (Fred/Ed Munson collision), filename, partial email,
uploader display name, alias/partial identifier, legacy substring rule,
ambiguous exact email, plus the exact-email positive control. The source scan
confirms **no substring/containment constructs** in the service or the Microsoft
sync. The deterministic `match_drive_item` behavior is unchanged. **PASS.**

## 3. New remediation-surface probes — all pass

- **Dual-source constraint integrity:** a link with **both** `document_id` and
  `microsoft_document_id`, or with **neither**, is rejected by the
  `ck_tax_document_link_one_source` CHECK. **PASS.**
- **Idempotency correctness:** two distinct documents sharing a signal get two
  distinct links (no over-merge); replay of one returns its existing link. **PASS.**
- **Route 409/403 mapping:** stale action → 409; cross-owner accept → 403 with a
  denied audit event. **PASS.**
- **Unmatched handling:** accepting a NULL-return link is blocked (409, "reassign
  first"); a non-firm-wide reviewer cannot act on an unmatched link (403); a
  firm-wide reviewer can. **PASS.**
- **Producer wiring E2E:** portal upload resolves the checklist to `received`;
  Microsoft matched/unmatched documents produce the correct link states; a second
  bridge run creates no duplicate Microsoft links. **PASS.**
- **Append-only ledger:** `tax_document_review_events` still rejects UPDATE. **PASS.**

## 4. Authorization, migration, and regression

- All record/reviewer/queue authorization checks from the original harness still
  pass (out-of-scope accept blocked, reassign-to-unauthorized-target blocked,
  queue scoped, read endpoints 404).
- Clean base→head; v0.9.7 upgrade → downgrade → re-upgrade removes and restores
  the tax document tables with **byte-identical sentinel data**; exactly one head
  (`l2c03f1e0d9b`); downgrade of the dual-source migration deletes source-relaxed
  rows before restoring NOT NULL, as documented.
- Full suite: **136 passed** (was 111). Compilation, startup/shutdown, 178-route
  OpenAPI, template render, and `git diff --check` all clean.

## 5. Did the remediation introduce any new gap? — No

- **Firm-wide unmatched visibility** (`record.read_all` sees NULL-return links) is
  not a disclosure: those principals are firm-wide by design and already see all
  records; non-firm-wide reviewers are correctly denied (403). Verified.
- **Dual-source model** cannot express an ambiguous/empty source (CHECK enforced),
  and Microsoft links reference `microsoft_documents` directly — no binary is
  duplicated into `documents`.
- **Idempotency** is keyed on the source document, so it never silently merges two
  different documents. Verified.

**Minor, non-blocking observation:** idempotency is first-link-wins — if a
document were first ingested with no signal (unmatched) and later re-ingested with
a deterministic signal, the existing unmatched link is returned rather than
auto-upgraded to accepted. In the wired flows the correct signal is supplied on
the first ingest, so this does not occur in practice, and a reviewer can resolve
such a link manually. Not a defect.

---

## Findings summary

| Area | Result |
|---|---|
| RC11 C1 unmatched orphan | **PASS (resolved)** |
| RC11 C2 replay idempotency | **PASS (resolved)** |
| RC11 C3 stale review guard (409) | **PASS (resolved)** |
| RC11 C4 cross-owner accept/reassign (403 + audit) | **PASS (resolved)** |
| RC11 C5 ingestion wiring (portal + Microsoft) | **PASS (resolved)** |
| RC11 C6 test coverage | **PASS (resolved)** |
| H13 recreation (9 datasets) + no-substring source scan | **PASS** |
| Dual-source constraint integrity | **PASS** |
| Idempotency correctness (no over-merge) | **PASS** |
| Route 409 / 403 mapping | **PASS** |
| Unmatched handling & firm-wide visibility | **PASS** |
| Producer wiring end-to-end | **PASS** |
| Authorization (record/reviewer/queue/read) | **PASS** |
| Append-only audit integrity | **PASS** |
| Migration up/down/re-up, one head, sentinel preserved | **PASS** |
| Full regression suite (136) | **PASS** |

No **FAIL**, no **CONCERN**, no **RETEST REQUIRED**. No security bypass,
cross-client disclosure, or unhandled-error path was found.

---

## Recommendation

# SAFE TO MERGE

The RC11 remediation fully resolves all six prior CONCERNs and introduces no new
defect. The document-intelligence pipeline is now wired end-to-end from both
producers (portal uploads and Microsoft documents) through the deterministic
engine to accepted links, mandatory review, and correctly-persisted unmatched
states — with ingestion idempotency, review-state guards (409), and cross-owner
ownership revalidation (403 + immutable audit). The central security property —
elimination of substring ownership matching (H13) — remains intact across all
nine adversarial datasets. Authorization, immutable audit, least privilege, the
inert AI port, migration reversibility, and a single Alembic head are all
preserved, and the full regression suite (136 tests) is green.

---

*RC11 retest conducted as an independent adversarial re-validation. No
application code was modified as part of this retest and nothing was committed by
it. Per instruction, PR #18 is not merged.*
