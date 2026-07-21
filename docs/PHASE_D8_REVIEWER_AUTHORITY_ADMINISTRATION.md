# Phase D.8 — Reviewer Authority and Compliance Administration

Controlled administration of the `ReviewerAuthority` records the D.7 compliance-review
framework requires. D.8 does **not** automate compliance decisions, determine whether
anyone is legally qualified, or fabricate licensing/registration/appointment/
supervisory/regulatory authority. It records authority **only** when an appropriately
authorized administrator provides factual supporting evidence. Implemented on
`release/0.13.0`.

## Known reviewer / authority status
The `reviewer_authorities` catalog is **empty**; no factual record establishes any
person — **including Michael Shelton** — as an authorized compliance principal. Business
ownership, advisor status, product/engineering/operational approval, the administrator
role, and application permissions are **not** treated as proof of authority. **No
ReviewerAuthority record is seeded (verified by test). Final regulated approval remains
`blocked_pending_authorized_reviewer` until factual authority is recorded.** No authority
is fabricated; Michael is not auto-created.

## Architecture & dependency direction
`Advisor Intelligence → Rule Catalog → Compliance Review Service → Reviewer Authority
Service → Administrative Authority UI`. Advisor Intelligence never imports compliance;
rule production is independent of authority records; reviewer authority affects **only**
whether D.7 permits a final human approval. No generalized identity-governance or
licensing engine — the lifecycle is a small, explicit set of transitions.

## Authority model
D.8 **extends** the D.7 `reviewer_authorities` table (no competing table). The existing
`authority_scope` (jsonb list of governed rule ids and/or policy-gate categories) is
**the single scope mechanism** — no separate `governing_rule_ids`/`policy_gate_scopes`
columns. Added fields: `evidence_description`, `recorded_by`, `recorded_at`,
`suspended_at`, `revoked_at`, `revocation_reason`, `supersedes_authority_id` (self-FK), a
status **CHECK** (`draft/active/suspended/expired/revoked/superseded`), and a new
append-only **`reviewer_authority_events`** ledger (`event_type, prior_status,
new_status, actor_principal_id, occurred_at, reason, evidence_snapshot`, trigger-blocked
UPDATE/DELETE).

## Scope semantics
A record confers authority for a rule only if its `authority_scope` contains that
governed rule id, the recommendation's policy-gate category, or the wildcard `"*"`. An
empty/ambiguous scope confers **nothing** (activation requires a non-empty scope, and an
empty scope matches no rule). Authority is **never** inferred from `reviewer_role` alone,
and a title such as "compliance officer" is never sufficient evidence.

## Lifecycle
Explicit transitions (no generic state machine): `draft → active` (activate),
`active → suspended`, `suspended → active` (restore), `{draft, active, suspended} →
revoked`, `{active, suspended} → superseded` (via creating a new active version).
**`expired` is computed** by date at lookup/display time — an active record past its
`expiration_date` reads as expired and confers no authority; history is **never mutated
because a page was viewed**. Every action passes an `expected_status` and locks the row
(`SELECT … FOR UPDATE`); a stale submission fails clearly rather than overwriting.

## Evidence requirements
Activation (and a superseding version) requires: reviewer identity (principal), reviewer
role, non-empty authority scope, effective date, source reference, and evidence
description — plus the administrator identity (`recorded_by`) and recorded date. Suspend
and revoke require a reason. The system **records** the supplied reference/description; it
does **not** validate external licensing databases, parse documents, or claim the
evidence is independently verified.

## Administration authorization
Two distinct capabilities: **`compliance.authority.read`** (view records — granted to
`administrator` + `compliance`) and **`compliance.authority.manage`** (record/maintain —
granted to `administrator` only). Reading authority records and making compliance
decisions are separate functions: `audit.read`, `compliance.review.decide`, and
administrator-role-membership-alone are **not** used as authority-management permission.
Holding `manage` permits maintaining factual records only — it does **not** make the
administrator a compliance reviewer and never confers approval authority. All write
routes enforce `compliance.authority.manage` server-side.

## Segregation of duties & self-administration prohibition
`recorded_by`/actor **must not equal** `principal_id`. The subject of an authority record
may **view** it (with the read capability) but may **never** create, activate, expand,
suspend, restore, revoke, or supersede their own authority — every path raises
`SelfAdministrationError`. Self-recording is **blocked**, not silently allowed; if the
business later requires a documented self-recording exception, it remains an **unresolved
governance decision** and is left as an explicit future extension point (no bypass ships).

## Append-only history
Every material change appends a `reviewer_authority_events` row (actor, timestamp,
reason, evidence snapshot); the ledger is trigger-protected against UPDATE/DELETE. Changes
to scope/role/evidence/dates are made by **superseding** — a new active version references
the prior via `supersedes_authority_id` and the prior becomes `superseded` (never
overwritten). Circular supersedes are prevented (a superseded record cannot be superseded
again). Suspension/revocation retain actor, timestamp, and reason.

## D.7 integration & final-approval behavior
The D.7 `reviewer_authority()` lookup is updated to require: `status='active'`, the
principal's **user is active**, effective date reached, expiration not passed, and scope
match. Final D.7 approval is blocked when authority is absent, draft, suspended, expired,
revoked, superseded, out of governing-rule scope, out of policy-gate scope, not yet
effective, expired by date, assigned to another principal, or lacking required evidence —
with a clear reason. Creating authority does **not** auto-approve a previously blocked
review, does **not** auto-reassign, and does **not** silently fall back to another
reviewer: a human must return to the review, re-assign, and explicitly decide.

## UI
`/compliance/authorities` (Oversight nav): a read-only **list** (reviewer, role, scope,
effective/expiration, status, source, recorded by/at; search/filter/sort/pagination), a
**detail** view (full record, scope, evidence, lifecycle dates, superseded/successor
links, complete append-only event history), and **administrative forms** (create draft;
and on detail: activate, suspend, restore, revoke, supersede) rendered only to
`compliance.authority.manage` holders with explicit confirmation. No inline actions from
the list, no bulk changes, no deletion, no editing of historical records.

## Reviewer identity
`principal_id` references an existing user and is the authoritative identity; a
`reviewer_name` may be snapshotted for readability. An inactive principal's authority
does not confer approval (the D.7 lookup requires an active user), while the historic
record is preserved.

## Source references
Metadata only (compliance manual, licensing/registration/supervisory/appointment/
broker-dealer/internal/regulatory reference). The system does not fetch, parse, validate,
or interpret referenced material; a controlled reference string plus evidence description
is sufficient. No upload system is added.

## Concurrency & integrity
`expected_status` + row locking on every write; append-only events; no deletion of
historical records; conflicting-active prevention (no two active authorities for the same
principal with overlapping scope); no circular supersedes; no self-administration; no
activation of incomplete records. A stale form submission fails clearly (HTTP 409) rather
than overwriting later changes.

## Migration
`f8a9u1t2h3r4` (down `e7c8o9m1p2q3`) — ALTERs `reviewer_authorities` (adds fields + status
CHECK, default status → `draft`), creates the append-only `reviewer_authority_events`
table, and seeds the two capabilities into the `administrator` (both) and `compliance`
(read) roles. Downgrade drops the events table/trigger, the added columns/CHECK, and the
caps. Upgrade/downgrade verified. **No authority record is seeded.**

## Exclusions honored
No automated licensing verification; no FINRA/SEC/state/carrier/broker-dealer API; no
license-status scraping/credential parsing/OCR/document parsing; no electronic
signatures/regulatory certification; no automated reviewer assignment/approval; no
suitability/replacement/1035/CE logic; no workflow engine; no notifications/email/Slack/
tasks/client communications/trade/recommendation execution; no AI/ML/predictive/
embeddings/vector search; no bulk administration; no authority deletion; no fabricated
reviewer records.

## Limitations & future integration
- The `reviewer_authorities`/event schema is the seam for future licensing-record
  linkage; external verification is out of scope.
- A documented self-recording exception (if ever approved) is an unresolved governance
  decision, not implemented.
- Conflicting-active prevention is enforced transactionally at the service layer
  (jsonb-scope overlap is not expressible as a simple DB constraint); a stricter DB-level
  guard is future work.
- These records are **factual administrative metadata — not electronic signatures, not
  independent licensing verification, not regulatory certification. The system does not
  independently determine legal qualification.**
