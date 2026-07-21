# Phase D.7 — Compliance Review and Decision Evidence Ledger

A durable, auditable, **human-controlled** review layer for governed Advisor
Recommendations. D.7 does **not** automate compliance, determine suitability, certify
regulatory compliance, or let an advisor/engineer/business owner substitute for an
appropriately authorized compliance reviewer. Implemented on `release/0.13.0`.

## Accountable reviewer — status
No repository record establishes an authorized/licensed compliance principal for any
regulated rule. **No authorized compliance reviewer is currently known, so final
regulated approval remains `blocked_pending_authorized_reviewer`.** The
`reviewer_authorities` catalog is seeded **empty**; no reviewer is fabricated or
auto-assigned (Michael Shelton is the business/product owner and is **not** recorded
as a compliance principal). Operational review (comments, returned/declined) may be
recorded by a `compliance.review.decide`-capable principal **without** representing it
as regulatory certification.

## Architecture & dependency direction
```
Advisor Intelligence  (execution layer — unchanged)
        │  get_client_signals()  (one-way read, to snapshot a recommendation)
        ▼
D.6 Rule Catalog      (RuleCatalog — reused for rule/version validation)
        ▼
Compliance Review Service  (app/services/compliance/reviews.py)
        ▼
Decision Evidence Ledger   (compliance_decisions — append-only)
        ▼
Read-only history + authorized review UI  (/compliance/reviews)
```
The dependency direction is strictly one-way (compliance → advisor_intelligence,
never the reverse — enforced by a test). Recommendation production does **not** depend
on review status; compliance review does **not** change the deterministic
recommendation result. There is no generalized workflow engine — the lifecycle is a
small, explicit set of allowed transitions.

## Review eligibility
Only **Advisor Recommendations** (`category == 'recommendation'`) carrying governance
metadata are eligible — initially the three governed types: Annual Portfolio Review,
Insurance Review, Beneficiary Review. Operational signals, opportunities, and
ungoverned records are never placed in the queue. Submission enforces person
record-scope first (an inaccessible client can never be submitted) and snapshots the
current recommendation from Advisor Intelligence.

## Persistence model
One focused migration (`e7c8o9m1p2q3`, down_revision `d4c5o6m7d8i9`) adds three
narrowly scoped tables (declared in `app/database/compliance_tables.py`, reflected in
`app.db`):
- **`compliance_reviews`** — one review per governed recommendation snapshot
  (recommendation_snapshot, evidence_snapshot, governing_rule, rule_version,
  policy_gate, status, submitter, assigned reviewer, timestamps). A **partial unique
  index** (`uq_open_compliance_review`) permits at most one OPEN review per
  (recommendation_id, governing_rule, rule_version, source record).
- **`compliance_decisions`** — the **append-only** decision ledger (a trigger blocks
  UPDATE/DELETE); a correction/reconsideration inserts a NEW row referencing the prior
  via `supersedes_decision_id`. Each decision snapshots governing_rule, rule_version,
  and evidence.
- **`reviewer_authorities`** — the authority catalog (seeded **empty**).

Snapshots (recommendation, evidence, governing rule, rule version) are stored so a
decision is reproducible from immutable data — never from a mutable live reference.

## Status lifecycle (explicit transitions)
`pending_submission → pending_assignment` (submit); `pending_assignment |
blocked_pending_authorized_reviewer → pending_review` (assign an **authorized**
reviewer) or `→ blocked_pending_authorized_reviewer` (unauthorized / none);
`pending_review → approved | approved_with_conditions` (requires authority + catalog
match, else blocked); `pending_review | blocked_… → returned | declined`; a
reconsideration (supersedes a prior decision) may also proceed from a decided state;
`returned | declined → closed`; a materially new rule version marks the prior review
`superseded`. Status never changes merely by rendering/reading a page.

## Decision model
Decision types: `approved`, `approved_with_conditions`, `returned`, `declined`. A
decision records reviewer, reviewer role, date, scope reviewed, governing rule, rule
version, and comments/exceptions. Required fields: `approved_with_conditions` ⇒
comments or exceptions; `returned` / `declined` ⇒ comments.

## Reviewer authority & authorized-reviewer blocking
`ReviewerAuthority` records (principal_id, reviewer_role, reviewer_name,
authority_scope, effective/expiration_date, status, source_reference) establish whether
a principal may make a **final** decision. **Final approval double-gates** on: the
`compliance.review.decide` capability **and** reviewer assignment **and** a recognized
reviewer role **and** a non-null recorded authority **and** a Rule-Catalog version
match. Because the catalog is empty, approvals are blocked: the review is moved to
`blocked_pending_authorized_reviewer`, **no approval decision is recorded**, and the UI
explains why. Authorization is never inferred from a job-title string. Authority
administration is out of scope this phase (static/seeded-empty catalog).

**How an authorized reviewer will later be assigned:** an administratively-maintained
`reviewer_authorities` row (principal_id + reviewer_role + reviewer_name +
authority_scope covering the governed rule id or policy-gate category + a
`source_reference` to the licensure/authorization record) must be recorded for the
accountable, appropriately-licensed compliance principal. This administration is provided
by **Phase D.8 — Reviewer Authority and Compliance Administration**
(`docs/PHASE_D8_REVIEWER_AUTHORITY_ADMINISTRATION.md`): an authorized administrator
records a draft from documented facts and activates it (with segregation of duties — no
self-administration, actor ≠ subject, append-only history). Until such a factual **active,
in-scope** record exists (and the D.8 lookup checks it — active status, active user,
effective/expiration dates, scope match), approval stays blocked. D.8 does not
auto-approve any previously blocked review; a human must return to the review, re-assign,
and explicitly decide.

## Recommendation snapshots & deduplication
Creating a review is idempotent — an existing OPEN review for the same
(recommendation_id, governing_rule, rule_version, source record) is returned unchanged
(DB partial-unique index is the backstop). A materially new rule version yields a new
review; closed/superseded reviews remain in history. Deterministic recommendation IDs
from Advisor Intelligence are preserved and never altered.

## Rule Catalog integration & version validation
Rule governance metadata is retrieved through the D.6 `RuleCatalog` (no duplicated
parsing/semver/mapping; version equality uses `rule_catalog.compare_versions`). The
review detail shows the exact catalog rule + version for the snapshot. A missing
catalog rule or a version mismatch **blocks final approval** with a clear validation
result — the latest rule version is never silently substituted.

## Authorization
Four new capabilities (composed into the `compliance` and `administrator` roles):
`compliance.review.read` (queue/detail), `compliance.review.submit`,
`compliance.review.assign`, `compliance.review.decide`. Viewing governance metadata
(`audit.read`, Rule Catalog) is **not** broadened into decision authority. All
endpoints are gated server-side by `require_capability`. `/compliance` is deliberately
not a firm-wide collection (the queue is book-scoped via `accessible_person_ids`) and
not given a middleware `.read` rule (the `.read→.write` inference would demand a
nonexistent `compliance.review.write`); route-level capabilities are the enforcement.
Holding `decide` never confers approval authority — that requires a `ReviewerAuthority`.

## UI
`/compliance/reviews` (Oversight nav): a read-only **queue** (recommendation, client/
household, governing rule, version, policy gate, assigned reviewer, review status,
submitted date; with search, status/gate filtering, sorting, pagination) and a
**review detail** (recommendation snapshot, evidence, explainability, source record,
governing rule + version, policy gate, Rule-Catalog validation, reviewer assignment,
and the full append-only decision history). The **decision form renders only** to a
principal holding `compliance.review.decide` and requires explicit confirmation. No
bulk approvals, no inline approval from the queue, no approval from the Advisor
Workspace, no silent status changes.

## Advisor-facing behavior
Advisor-facing recommendation rendering is **unchanged** in this phase. Exposing review
status in the Advisor Workspace would alter the deterministic advisor-panel HTML that
the D.5E golden regression pins, so advisor-facing status display is **deferred**
(documented future integration). Recommendations are not hidden, rewritten, reordered,
or suppressed based on a compliance decision; an approved review is not treated as
client advice; no execution buttons, tasks, communications, trades, or client
instructions are created.

## Append-only history & concurrency
The decision ledger is append-only (DB trigger); prior decisions are never modified or
deleted; corrections create a new superseding decision. Concurrency: decision/assign
actions pass an `expected_status`; the review row is locked (`SELECT … FOR UPDATE`) and
a mismatch fails loudly (`StaleReviewError`, HTTP 409) rather than overwriting the
first decision. Timestamps are application timestamps. **This is not cryptographic
non-repudiation and not an electronic signature.**

## Sign-off artifact definition
A compliance sign-off artifact recorded by this system is a `compliance_decisions` row
capturing: **rule-set version** (governing_rule + rule_version), **reviewer** and
**reviewer role**, **date** (decided_at), **scope reviewed**, **approval status**
(the decision), and **comments / exceptions**. **This artifact records a human review
decision. It is not an electronic signature and not an independent regulatory
certification.**

## Exclusions honored
No automated compliance approval, suitability determinations, replacement/1035 analysis,
licensing determinations, CE calculations, regulatory certification, electronic
signatures, notifications/email/Slack, task generation, trade execution, account
changes, client communications, recommendation execution, AI/ML/predictive/embeddings/
vector search, document parsing, generalized workflow engine, bulk approvals, or
retroactive fabrication of reviewers/decisions.

## Limitations & future integration
- Final regulated approval is blocked until a factual `ReviewerAuthority` is recorded
  for an appropriately-licensed compliance principal (business/compliance decision).
- Reviewer-authority administration UI is deferred.
- Advisor-facing review-status display is deferred (to avoid D.5 golden drift).
- Segregation of duties between submit and decide is enforced only by capability
  separation this phase; a stronger submit≠decide control is future work.
- Notifications of pending reviews are out of scope (no notification delivery in D.7).
