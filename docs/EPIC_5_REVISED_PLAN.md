# Epic 5 — Revised Plan (post-v0.9.7)

**Purpose:** reassess the remainder of Epic 5 against the code that actually
shipped (Releases v0.9.4–v0.9.7), rather than the original nine-sprint design in
`EPIC_5_TAX_PRACTICE_PLATFORM.md`. The as-built platform diverged from the
original plan in two material ways, and the RC8/RC9 architecture review plus the
0.9.7 security hardening changed what is prudent to build next.

**Scope of this document:** planning only. No application code is written. This
supersedes the sprint ordering in the original Epic 5 design; the original
design remains the reference for detailed per-area requirements.

---

## 1. What actually shipped vs. the original plan (the two divergences)

**Divergence A — Sprints 5.2 and 5.3 were swapped.** The original design ordered
Foundation → *Return Lifecycle* → *Intake*. The team shipped Foundation →
*Intake* → *Return Lifecycle*:

| As-built | Release | Content | = Original design's… |
|---|---|---|---|
| Sprint 5.1 | v0.9.4 | Tax domain, offices, jurisdictions, deadlines, workflow launch | Sprint 5.1 |
| Sprint 5.2 | v0.9.5 | Engagement intake: organizers, questionnaires, engagement letters, checklists, missing-item scaffolding | **original Sprint 5.3** |
| Sprint 5.3 | v0.9.6 | Return lifecycle, review routing, client approvals, filing events, delivery, production queues/dashboards | **original Sprint 5.2 + most of original Sprint 5.6** |
| — | v0.9.7 | Security hardening (RC8/RC9 authorization, record-scope, workflow-permission fixes) | (not in original plan) |

The as-built numbering (5.1=foundation, 5.2=intake, 5.3=lifecycle) is
authoritative going forward.

**Divergence B — Sprint 5.3 absorbed most of original Sprint 5.6.** The shipped
lifecycle already implements preparer/manager/partner review cycles
(`tax_return_reviews`, `tax_review_corrections`), client approvals
(`tax_client_approvals`: return approval / e-file authorization / delivery
acknowledgement), a provider-neutral filing state machine and event ledger
(`tax_filing_events`), delivery events, and production queues/dashboards. What
original Sprint 5.6 still leaves open is the *external e-file provider
integration itself*, retention/legal-hold, and compliance evidence — a much
smaller residue than the original plan implies.

---

## 2. Completed work (v0.9.4–v0.9.7)

**Schema (31 tax tables, single linear history, head `j0a81f9c8d7e`):**
`tax_firms`, `tax_offices`, `tax_office_memberships`, `tax_years`,
`tax_seasons`, `tax_calendars`, `filing_jurisdictions`, `tax_return_types`,
`tax_filing_statuses`, `tax_deadline_rules`, `tax_deadlines`, `tax_engagements`,
`tax_engagement_returns`, `tax_workflow_links`; intake:
`engagement_letter_templates`, `tax_engagement_letters`,
`tax_organizer_templates`, `tax_organizers`, `tax_questionnaire_templates`,
`tax_questionnaire_questions`, `tax_questionnaires`, `tax_questionnaire_answers`,
`tax_checklist_templates`, `tax_checklist_template_items`, `tax_checklist_items`,
`tax_missing_items`; lifecycle: `tax_return_lifecycle_events`,
`tax_return_reviews`, `tax_review_corrections`, `tax_client_approvals`,
`tax_filing_events`.

**Services:** `tax_domain.py` (engagements, deadlines, reference data, scope
filter), `tax_intake.py` (organizers/questionnaires/letters/checklists/missing
items, reminders), `tax_return_lifecycle.py` (15-state machine, reviews,
corrections, client decisions, filing, workflow sync, dashboards),
`tax_filing_providers.py` (provider abstraction — **currently orphaned**, see §9).

**Capabilities & security:** `tax.read`, `tax.write`, `tax.review`,
`tax.deadline.manage`, `tax.intake.read/write` composed into roles; office +
record scope via `_scope_filter`/`list_engagements`; canonical record-scope
authorization service (`app/security/authorization.py`, added in 0.9.7);
immutable audit and denial events; append-only lifecycle/filing ledgers.

**Platform reuse (no duplicate engines):** workflow templates/instances,
`work_approvals` (segregation-of-duty), `record_assignments`, `work_queues`,
portal grants/notifications, canonical documents, timeline, immutable audit.

**Validation posture:** 94 automated tests; RC8 architecture review, RC9
verification, RC10 independent adversarial validation (SAFE TO MERGE).

---

## 3. Remaining Epic 5 functionality (reassessed)

Grouped by capability, with as-built status:

1. **Document intelligence & missing information** — *not started.* Intake (5.2)
   created checklist items, missing-item records, and portal document requests,
   but nothing classifies or matches *received* documents against those
   checklist items. The only document-matching today is the Microsoft sync's
   substring heuristic, which RC8 **H13** confirmed can mis-assign one client's
   document to another. This is the natural next capability **and** it remediates
   a live security finding.
2. **Tax exceptions** — *not started.* Extensions, estimated payments, notices,
   amendments. No tables/services exist.
3. **Filing / delivery / compliance completion** — *partially done in 5.3.*
   Remaining: wire the orphaned filing-provider port into `record_filing`, real
   e-file provider adapter(s), retention classes / legal hold, delivery-package
   generation, compliance evidence packs, and either wire or retire the dead
   `portal/signatures.py` e-signature module for e-file authorization.
4. **Secure tax portal completion** — *partially done in 5.2/5.3.* Basic portal
   pages exist (`/portal/tax-intake`, `/portal/tax-returns`). Remaining: the
   unified tax portal journey, delivery center, notification/consent
   preferences, and the production identity/MFA/session-device/rate-limit gates
   (which overlap the Release 1.0 portal launch gates).
5. **Production reporting & capacity** — *foundational dashboards exist.* The
   tax and production dashboards exist but do in-Python aggregation over full row
   sets (RC8 H11/H16 — the metric bugs are fixed in 0.9.7; the N+1 scaling
   remains a 0.9.8 item). Remaining: reconciled reporting, deadline calendar,
   productivity, and capacity views with scope-aware drill-down.
6. **External provider & transcript acquisition** — *not started;* only a manual
   filing stub exists. **Recommended to move to Epic 6** (see §10).
7. **Governed AI extensions & seasonal capacity forecasting** — *not started.*
   **Recommended to move to Epic 6** (see §10).

**Cross-cutting deferred debt (already scheduled by RC9, feeds these sprints):**
- **Release 0.9.8:** Microsoft 365 OAuth token encryption + refresh (H10);
  performance / N+1 / index work on the tax and portal dashboards (H15–H20).
- **Release 1.0:** database CHECK/constraint hardening (H21); full three-way
  record-scope consolidation; portal production-security gates.

---

## 4. Revised sprint breakdown

Five remaining Epic 5 sprints (down from six in the original numbering, because
original 5.6 largely shipped inside 5.3 and original 5.8 + AI move to Epic 6),
interleaved with the two already-scheduled debt releases.

| Sprint / Release | Title | Origin | Change from original |
|---|---|---|---|
| **Release 0.9.8** | Performance & Integration-Security Debt | RC9 | Prerequisite; not a tax feature sprint |
| **Sprint 5.4 → v0.9.9** | Tax Document Intelligence & Missing Information | original 5.4 | Re-scoped; folds in H13 matching remediation; AI = interface-only |
| **Sprint 5.5 → v0.9.10** | Tax Exceptions: Extensions, Estimates, Notices, Amendments | original 5.5 | Largely unchanged |
| **Sprint 5.6 → v0.9.11** | Filing, Delivery & Compliance Completion | original 5.6 (residue) | Substantially smaller — review/approval/filing base already shipped in 5.3 |
| **Sprint 5.7 → v0.9.12** | Secure Tax Portal Completion | original 5.7 | Unchanged scope; gated on Release 1.0 identity provider |
| **Sprint 5.8 → v0.9.13** | Tax Production Reporting & Capacity | original 5.9 (minus AI/forecast) | AI recommendations and seasonal forecasting removed to Epic 6 |
| **Release 1.0** | Epic 5 release readiness | original 5.9 tail | Production-scale, recovery, accessibility, security evidence |

Moved out of Epic 5 → **Epic 6:** original Sprint 5.8 (Drake/UltraTax/Lacerte/CCH
provider + IRS transcript integration) and the governed-AI / capacity-forecasting
extensions.

---

## 5. Recommended optimal Sprint 5.4 scope (based on the current codebase)

**Recommendation: Sprint 5.4 = Tax Document Intelligence & Missing Information**,
re-grounded to what the shipped code enables and needs — not a verbatim copy of
the original 5.4.

**Why this is the optimal next sprint against the current codebase:**
- **Every dependency is already in place.** Canonical documents + versions,
  Microsoft document sync, portal document requests, and — critically — the
  checklist/missing-item scaffolding built in Sprint 5.2. Sprint 5.2 *creates*
  checklist items and missing-item records but nothing *resolves* them from
  received documents; 5.4 closes that loop. It is the highest-value tax
  capability with the least new external dependency.
- **It remediates a live, confirmed security finding.** RC8 **H13** confirmed
  the Microsoft document sync auto-assigns documents by unguarded substring
  containment, risking cross-client document exposure in a wealth/tax context.
  Building the tax document-matching pipeline in 5.4 is the correct place to
  replace that heuristic with boundary-safe, checklist-aware matching and a
  human-review gate. Doing document work *without* fixing H13 would be
  negligent; doing it here fixes it in context.
- **It does not depend on external vendors.** Deterministic classification and
  checklist matching need no provider integration. The AI classifier stays an
  **interface-only port** (contract + tests, no vendor), so the sprint is fully
  deliverable in-house and the real AI work moves cleanly to Epic 6.

**Explicit prerequisite:** Release **0.9.8** (tax/portal dashboard N+1 fixes +
index work, H15–H20) should land **before or at the start of** Sprint 5.4, so the
new document workspace/dashboard is built on the bulk-query pattern
`production_dashboard()` already demonstrates rather than inheriting the
`staff_dashboard()`/portal N+1. If 0.9.8 slips, Sprint 5.4's new read paths must
still be written with SQL-side aggregation from the start.

**In scope for Sprint 5.4:**
- Return-type/year checklist generation from the existing
  `tax_checklist_templates` (reuse; do not duplicate).
- Deterministic document→checklist-item and document→return matching using
  **token/boundary-safe** rules and existing document hashes (replaces H13
  substring matching); single-candidate auto-match only above a confidence gate,
  everything else to a human-review queue.
- Missing-information calculation that drives existing portal document requests
  and blocks preparation workflow steps.
- Duplicate detection via existing `documents.sha256`.
- Classification review / override UI and queues.
- AI classifier **port only** (no vendor), with contract tests.

**Explicitly out of scope for 5.4 (→ later sprints / Epic 6):** real AI
classification vendor, OCR/extraction of tax facts, transcript facts satisfying
checklists (needs Epic 6 transcript integration), and bulk historical document
backfill (separate resumable job).

---

## 6. Dependencies and implementation order

**Dependency graph (remaining Epic 5):**

```
0.9.8 (perf/token debt) ──► 5.4 Document Intelligence ──► 5.5 Exceptions
                                    │                          │
                                    └──────────┬───────────────┘
                                               ▼
                              5.6 Filing/Delivery/Compliance completion
                                               │
                                               ▼
        Release 1.0 identity gates ──► 5.7 Secure Tax Portal completion
                                               │
                                               ▼
                              5.8 Reporting & Capacity ──► Release 1.0 readiness
```

- **5.4** depends on: canonical documents/versions, Microsoft document sync,
  portal requests, 5.2 checklist/missing-item schema, workflow/queue/assignment
  services. Soft-depends on 0.9.8 (perf).
- **5.5** depends on: 5.1 deadlines/jurisdictions, 5.3 lifecycle, documents
  (notice attachments), workflows/queues/approvals. Independent of 5.4 but
  sequenced after it to avoid parallel document-model churn.
- **5.6** depends on: 5.3 filing/review base, the filing-provider port,
  documents (retention/evidence), portal signatures (wire or retire).
- **5.7** depends on: 5.4–5.6 tax services being stable, **and Release 1.0
  production identity/MFA/security gates** (hard dependency — do not launch the
  external tax portal before those gates exist).
- **5.8** depends on: all prior operational facts existing so reported counts
  reconcile.

**Recommended order:** 0.9.8 → 5.4 → 5.5 → 5.6 → (Release 1.0 identity gates) →
5.7 → 5.8 → Release 1.0 readiness. Rationale: harden performance before adding
tax read surface; resolve documents/missing-info before exceptions (exceptions
attach documents); complete filing/compliance before exposing the full client
journey; portal completion waits on production identity; reporting last so it
reconciles to complete operational data.

---

## 7. Database changes (per remaining sprint)

All additive, single linear head per sprint, parent = prior head, with
clean-install + prior-release upgrade/downgrade/re-upgrade + sentinel validation.
Newer tables should adopt the CHECK-constraint/lookup-table discipline the
0.9.4-era tax tables established (and that RC9 H21 recommends extending).

- **5.4:** `tax_document_classifications` (label, confidence, source, reviewer,
  provenance), `tax_document_links` (document ↔ return/checklist-item with
  year/form/category), classification-review/resolution events, duplicate-group
  references. No new binary storage; reuse `documents`/versions. Add the FK
  indexes that RC9 flagged missing on `tax_missing_items.tax_engagement_return_id`.
- **5.5:** `tax_extensions`, `tax_estimated_payments`, `tax_notices`,
  `tax_notice_actions`, `tax_amendments` (with original-return links, affected
  jurisdictions, fixed-precision financial impact, CHECK-constrained status/type).
- **5.6:** retention classes / legal-hold columns (if not made platform-wide
  earlier), `tax_efile_submissions` provider linkage (or extend
  `tax_filing_events`), compliance-evidence links. Wire `tax_filing_providers`
  into `record_filing`. E-file events remain append-only.
- **5.7:** prefer none beyond portal preference/consent/tax-notification and
  explicit signing-authority extensions. No tax records duplicated in portal
  tables.
- **5.8:** prefer indexed views/live queries; add snapshot/report-catalog tables
  only where measurement proves live queries insufficient.

---

## 8. APIs, UI, testing strategy, acceptance criteria (per remaining sprint)

Conventions apply to all: versioned `/api/v1/tax/*`, capability + office + record
+ household + portal scope on every query, masked identifiers, append-only audit,
and the response-envelope/pagination consistency RC8 flagged (adopt the shared
convention rather than a per-router shape).

**Sprint 5.4 — Document Intelligence**
- *APIs:* checklist, classification, missing-item, review, bulk-classify,
  request, resolve, exception endpoints under `/api/v1/tax` with
  confidence/provenance and safe download links.
- *UI:* tax document workspace, checklist progress, classification review,
  unmatched-document queue, missing-information panel, portal request status.
  (Reuse the `base.html` staff shell; ensure the tax dashboards' CSS classes are
  actually styled — an open RC8 gap.)
- *Testing:* boundary-safe matching (explicit anti-H13 cross-client test),
  confidence thresholds, duplicates via hash, missing-item/resolution, portal
  isolation, workflow blocking, quarantine, classifier-port contract, migration,
  regression.
- *Acceptance:* every active return generates an explainable checklist; received
  documents are classified or queued for review; **no substring/boundary
  collision can auto-assign one client's document to another**; missing info
  drives authorized client/staff work; no binary duplication.

**Sprint 5.5 — Exceptions**
- *APIs:* extensions, estimates, payments, notices, actions, amendments,
  deadlines, approvals, portal-safe status.
- *UI:* extension dashboard, estimate calendar, notice workspace + scanned-notice
  viewer, amendment comparison, due-date risk, escalation.
- *Testing:* federal/state deadline rules, payment schedules, notice triage,
  amendment links, workflows/approvals, masking, portal isolation, migration,
  edge dates.
- *Acceptance:* every extension/estimate/notice/amendment has an owner, statutory
  deadline, workflow, document evidence, authorized client status, and immutable
  history.

**Sprint 5.6 — Filing/Delivery/Compliance completion**
- *APIs:* e-file submission/events/status wired to a real provider port; delivery
  package retrieval; retention/legal-hold; compliance evidence.
- *UI:* e-file monitor, rejection repair, delivery center, compliance evidence
  view. (Review/approval UI mostly exists from 5.3.)
- *Testing:* provider contract, duplicate/out-of-order e-file events,
  rejection/retry, delivery, retention/hold enforcement, signature authority,
  segregation, migration, regression.
- *Acceptance:* no return transmits without required reviews/client
  authorization; the filing-provider abstraction is actually invoked (no orphaned
  code); acknowledgements reconcile; delivery and compliance evidence complete;
  `portal/signatures.py` is either wired into e-file authorization or removed.

**Sprint 5.7 — Secure Tax Portal completion**
- *APIs:* extend `/api/v1/portal/tax` for the full journey (engagements,
  organizers, requests, tasks, payments, notices, approvals, signatures, filing
  status, delivery, preferences).
- *UI:* tax portal landing, annual checklist, secure tax messages, document
  requests/uploads, estimates, notice status, approval/signature, filing status,
  delivery center, settings; accessible/responsive.
- *Testing:* self/joint/trusted/delegated authority matrix, staff/portal token
  isolation, internal-note exclusion, quarantine, rate-limit/MFA gates,
  accessibility, migration.
- *Acceptance:* authorized clients complete the full tax journey without seeing
  internal/other-client data; signing authority explicit; Release 1.0 portal
  launch gates have evidence.

**Sprint 5.8 — Reporting & Capacity**
- *APIs:* dashboards, production funnel, deadlines, capacity, workload,
  productivity, exceptions, compliance under `/api/v1/tax/reporting`.
- *UI:* preparer/manager/partner/office/firm dashboards, deadline calendar,
  drill-down with scope-aware totals, export controls.
- *Testing:* metric-to-operational-row reconciliation, negative drill-down scope,
  deadline/capacity, exports, performance, accessibility, production-scale
  migration.
- *Acceptance:* operational and reported counts reconcile; every drill-down
  preserves scope; productivity definitions approved; no cross-client leakage.

---

## 9. Known debt to retire during the remaining sprints (from RC8/RC9)

These should be addressed in the sprint that touches the relevant surface, not
left to accumulate:

- **Orphaned filing-provider abstraction** (`tax_filing_providers.py` never
  imported) → wire into `record_filing` in **5.6**, or delete if the design
  changes.
- **Dead portal e-signature module** (`portal/signatures.py`) → wire into e-file
  authorization in **5.6**, or remove and update docs.
- **Unstyled tax dashboards** — the tax templates reference CSS classes
  (`page-header`, `stats-grid`, `stat-card`, `panel`, …) not defined in the
  loaded stylesheets → fix when the document workspace UI lands in **5.4**.
- **Dashboard N+1 / missing FK indexes** (H15–H20) → **Release 0.9.8**, before
  5.4 adds read surface.
- **Free-text status/type columns without CHECK/lookup enforcement** (H21) →
  apply the discipline to all *new* 5.4–5.8 tables and backfill older tax tables
  in **Release 1.0**.
- **In-Python dashboard aggregation** → new reporting in 5.8 must use SQL-side
  aggregation and reconcile to operational rows.

---

## 10. Features recommended to move into Epic 6

1. **Epic 6 — Tax Data Acquisition & Provider Integration** (was original Sprint
   5.8). Drake first adapter; UltraTax/Lacerte/CCH interface contracts; IRS
   transcript request/import; provider connections, import runs, normalized
   facts/provenance, reconciliation, transcript consent/artifacts. *Rationale:*
   this is a large external-integration program with vendor contracts, secrets
   management, transcript regulatory handling, and rate-limit/backoff concerns —
   materially different from the internal tax-operations work of Epic 5. It also
   depends on the filing-provider port being real (5.6) and on
   secrets-management maturity (H10 in 0.9.8). Bundling it into Epic 5 would
   stretch the epic and couple internal release readiness to vendor timelines.
2. **Epic 6 — AI-Assisted Tax Operations** (was original 5.4's optional AI
   classifier + 5.9's AI recommendation/evidence work). Governed AI
   classification, extraction, and recommendation ports with evidence capture and
   human-decision records. *Rationale:* AI governance (prompt/data policy,
   evidence, human-in-the-loop, no autonomous filing/reassignment) is a
   distinct capability with its own risk model; Epic 5 should ship the
   deterministic pipeline and an interface-only port, and Epic 6 should supply
   the governed implementation.
3. **Epic 6 — Seasonal capacity forecasting & workload balancing** (from original
   5.9). Demand forecasting and rebalancing recommendations are advanced
   analytics beyond the reconciled operational reporting that completes Epic 5;
   they build naturally on the AI-operations work.

Keeping these in Epic 6 lets Epic 5 close on a fully in-house, vendor-independent
tax operating platform with a documented Release 1.0 readiness gate, and lets the
external-integration and AI programs proceed on their own dependencies and risk
reviews.

---

## 11. Summary recommendation

- **Proceed with Sprint 5.4 = Tax Document Intelligence & Missing Information**,
  re-grounded to the current codebase, folding in the H13 document-matching
  remediation and keeping AI as an interface-only port.
- **Sequence Release 0.9.8 (performance + M365 token debt) first** so 5.4 is
  built on sound read paths.
- **Re-scope the remaining Epic 5 to five sprints (5.4–5.8)**, recognizing that
  the shipped Sprint 5.3 already delivered most of the original Sprint 5.6.
- **Move external provider/transcript integration and governed AI/forecasting to
  Epic 6.**
- **Retire the orphaned filing-provider and dead e-signature modules** in the
  sprints that touch them, and apply the deferred DB-constraint discipline to all
  new tables.

*Planning review only. No application code was modified and nothing was
committed. Sprint 5.4 has not started.*
