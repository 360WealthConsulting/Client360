---
title: "Tax Operations — SOP: Quarterly Estimated Payments"
page_id: "TAXOPS-SOP-08"
area: "TAXOPS"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/tax/quarterly-estimated-payments.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "annual"
next_review: "TBD"
related: ["TAXOPS-SOP-02", "TAXOPS-SOP-03", "TAXOPS-SOP-07"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "24051753"
source_title: "SOP-024 - Quarterly Estimate Workflow"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/24051753"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["24051753 (SOP-024)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-024; 12 concerns separated. Safe-harbor percentages, due dates, penalty rules, payment methods, and state-specific requirements are externally governed and are NOT invented here. Due dates used operationally are captured from an authoritative source, not hard-coded. No live Client360-Drake / Client360-IRS / Client360-state integration implied."
---

# Tax Operations — Quarterly Estimated Payments

## Purpose & scope
Prepare, communicate, and track quarterly estimated tax payment **recommendations** so clients receive
documented instructions and payments are followed up — without this SOP defining the underlying tax
calculation or deadlines.
**In scope:** federal and state **estimated tax payment** recommendations for individual and business
clients. **Out of scope:** return preparation (`TAXOPS-SOP-02/03`) and extensions (`TAXOPS-SOP-07`).

> ⚠️ **CAUTION — no live integration implied.** Estimates are prepared via **Drake** (server-based) and
> communicated/tracked via **TaxDome**. This SOP is the **current manual** workflow; it does **not**
> imply any live Client360↔Drake, Client360↔IRS, or Client360↔state integration.

> 🛑 **Do not invent calculation or deadline facts.** Do **not** state **safe-harbor percentages**,
> **due dates**, **penalty rules**, **payment methods**, or **state-specific requirements** from
> memory. These are **externally governed**. Any **due date** used operationally is **captured from an
> authoritative source** — do **not** hard-code recurring statutory dates.

## Audience
Preparer (calculates); Reviewer/Advisor (approves); Client Service (delivers instructions, tracks
confirmation and follow-up).

## The twelve concerns (kept explicitly separate)

### 1. Calculation request
Identify the client needing estimates and open a **calculation request** (e.g., triggered by prior-year
liability, current-year change, or an advisory hand-off).

### 2. Source-data collection
Collect the inputs — prior-year tax liability, current-year changes, and (where relevant) business
income, withholding, distributions, capital gains, and retirement income — by reference. Never copy
return data or client PII into this page.

### 3. Preparer calculation
The preparer produces the **estimate recommendation** in the tax software. This SOP does **not** define
the calculation method, safe-harbor thresholds, or amounts — those are externally governed (below).
Document the **basis** of the calculation.

### 4. Reviewer approval
Where needed, the **advisor or reviewer** reviews and approves the recommendation before it is
communicated to the client. `SME CONFIRMATION REQUIRED`: which estimates require reviewer/advisor
approval.

### 5. Client delivery
Deliver the recommendation to the client through the **approved method**, and save the estimate
instructions to the client file (by reference). `SME CONFIRMATION REQUIRED`: the approved delivery
method.

### 6. Federal & state payment instructions
Provide the **federal and state payment instructions** operationally — captured from the recommendation
and the applicable authority instructions. The **amounts, due dates, methods, and any state-specific
requirements are externally governed** (below) and are **not** set here; federal and each state are
tracked **separately**.

### 7. Payment confirmation
Track whether the client **made** each payment and retain **payment confirmation** evidence (by
reference). `SME CONFIRMATION REQUIRED`: where payment confirmations are recorded (system of record).

### 8. Missed or changed-payment handling
Handle a **missed** or **changed** payment operationally — flag it, notify the responsible party, and
route for any recalculation. Do **not** state penalty consequences (externally governed — escalate).

### 9. Recalculation triggers
Recognize **recalculation triggers** operationally — material income/withholding change, a missed
payment, or a new advisory hand-off — and re-run the calculation request when one occurs. `SME
CONFIRMATION REQUIRED`: the defined recalculation triggers/thresholds.

### 10. Evidence retention
Retain the calculation basis, delivered instructions, and payment confirmations (by reference).
Retention **periods/rules are externally governed** (below).

### 11. Externally governed calculation, deadline, penalty & payment requirements
**No advisory-fee policy is involved.** Estimated payments are governed by **external federal and state
tax authorities and applicable professional requirements** — the calculation and safe-harbor rules,
due dates, underpayment/penalty rules, approved payment methods/channels, state-specific requirements,
preparer obligations, and record-retention — which are **not defined, summarized, or inferred** here.
**Follow the currently effective IRS and applicable state requirements** and the **currently effective
taxing-authority payment instructions**. *(Controlled citations will be added during the Compliance
Validation milestone.)*

### 12. Unresolved operational & platform details (controlled placeholders)
Held as visible placeholders — not guessed:
- Which estimates require **reviewer/advisor approval**. `SME CONFIRMATION REQUIRED`
- Approved client **delivery method**. `SME CONFIRMATION REQUIRED`
- Where **payment confirmations** and estimate status are recorded (system of record). `SME CONFIRMATION REQUIRED`
- Defined **recalculation triggers/thresholds**. `SME CONFIRMATION REQUIRED`
- Approved **payment methods/channels** (externally governed). `SME CONFIRMATION REQUIRED`

## Expected results
An estimate recommendation with a documented basis, reviewer-approved where required, delivered with
federal and state payment instructions, with each payment confirmation tracked, missed/changed payments
handled and recalculated when triggered, and evidence retained — **without** any invented percentages,
due dates, penalty rules, payment methods, or state requirements.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| A due date or safe-harbor % is stated from memory | Invented deadline/rule | Capture the date from an authoritative source; do not state safe-harbor rules | Any figure was communicated to a client |
| Payment not confirmed | Confirmation step skipped | Track and retain each payment confirmation | Payment cannot be evidenced |
| Federal and state merged | Instructions not separated | Track federal and each state separately | — |
| Income changed, estimate not updated | Recalculation trigger missed | Re-run the calculation request | Materially higher liability |

## Escalation
Escalate penalty/consequence questions, missed payments, and material recalculations to the
**responsible tax professional / Advisor / Lead** (Michael Shelton).

## Related
**Existing operational dependencies:** `TAXOPS-SOP-02` — 1040 Preparation · `TAXOPS-SOP-03` — Business Return Preparation · `TAXOPS-SOP-07` — Tax Extensions
**Planned (not yet authored):** CHK-020 (Quarterly Estimate Checklist) · SOP-022 (Tax Planning Opportunity)
**Deferred:** Controlled citations for governing sources (Form ADV Part 2A / Advisory Agreement, IRS / state requirements) — Compliance Validation milestone.
**External (referenced, not owned):** IRS & applicable state tax authorities; the e-file provider; Drake (server-based) & TaxDome — externally owned/governed.

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-024 - Quarterly Estimate Workflow |
| Source identifier | Confluence `24051753` (CAP-004) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | TaxDome / Drake — **needs confirmation**; no integration implied |
| Duplication | overlaps CHK-020 (checklist) + SOP-022 (planning) — split/linked |
| Contradictions | none identified |
| Facts verified | request→collect→calculate→approve→deliver→confirm→follow-up flow; calculation-basis and payment-confirmation controls |
| Facts awaiting confirmation | approval scope; delivery method; confirmation system of record; recalculation triggers; payment methods (externally governed) |
| Disposition recommendation | **replace** SOP-024 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-18 | Claude (0.12 Tax) | Initial adaptation from Atlas SOP-024; 12-way separation; no invented safe-harbor %/due-dates/penalty-rules/payment-methods/state-requirements; due dates captured from authoritative sources, not hard-coded; federal + each state tracked separately; no live integration implied; `needs_review`. |
| 0.2 | 2026-07-18 | Claude (0.12 Stabilization) | Editorial stabilization: standardized externally-governed heading pattern; categorized Related into existing/planned/deferred/external; bidirectional cross-references; no operational change; still `needs_review`. |
