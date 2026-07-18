---
title: "Tax Operations — SOP: Tax Return Review & Delivery"
page_id: "TAXOPS-SOP-04"
area: "TAXOPS"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/tax/tax-review-and-delivery.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "annual"
next_review: "TBD"
related: ["TAXOPS-SOP-02", "TAXOPS-SOP-03", "TAXOPS-SOP-05"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "23953450"
source_title: "SOP-019 - Tax Return Review & Delivery"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/23953450"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["23953450 (SOP-019)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-019; operational review/delivery stages separated; tax requirements externally governed."
---

# Tax Operations — Tax Return Review & Delivery

## Purpose & scope
Ensure tax returns are reviewed, questions are resolved, and delivery is documented **before** e-file
authorization and filing.
**In scope:** individual and business returns prepared by 360, from preparation completion through
hand-off to e-file authorization (`TAXOPS-SOP-05`). **Out of scope:** preparation (`TAXOPS-SOP-02` /
`-03`) and e-file transmission/acknowledgement (`TAXOPS-SOP-05`).

## Audience
Preparer (completes and resolves notes); Reviewer (approves); Client Service (delivers, tracks
questions).

## Operational stages (kept distinct)

### 1. Preparer completion
Confirm preparation is complete before assigning for review.

### 2. Reviewer approval
Assign a reviewer; review for accuracy and completeness; return notes to the preparer when corrections
are needed; confirm all reviewer notes are resolved. *(Reviewer independent of the preparer where
possible.)*

### 3. Unresolved diagnostics
Review software diagnostics, prior-year comparison, and unusual items; **do not deliver** a return with
material unresolved diagnostics. `SME CONFIRMATION REQUIRED`: the threshold/definition of a
"must-resolve" diagnostic.

### 4. Client delivery
Prepare the client delivery package and deliver through the **approved method**; document the delivery
method and date. `SME CONFIRMATION REQUIRED`: the current approved delivery method.

### 5. Signature / authorization status
Track whether the client has provided any required **signature or e-file authorization** — this SOP
**hands off** authorization to `TAXOPS-SOP-05`; a delivered return is **not** an authorized/filed return.

### 6. Filing readiness
Confirm the return is review-complete and delivered, so it is **ready to enter** the e-file
authorization workflow — readiness here is **not** "filed."

### 7. Payment / estimated-payment instructions
Provide the client the payment, refund, or estimated-payment **next-step instructions** operationally.
The **amounts, deadlines, and payment rules are externally governed** (see below) and are **not** set
by this SOP.

### 8. Evidence retained
Retain the delivery record, resolved-review evidence, and client instructions (by reference). Retention
**periods/rules are externally governed** (see below).

## Expected results
A reviewed, corrected, delivered return with documented delivery, client instructions provided, and the
return ready to enter e-file authorization — with clear status that it is **not yet filed**.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Return delivered with open diagnostics | Diagnostics not cleared | Resolve or document before delivery | Material diagnostic unresolved |
| Delivery not documented | Step skipped | Record delivery method + date | — |
| Treated as filed after delivery | Status confusion | Delivery ≠ filed; hand off to `TAXOPS-SOP-05` | — |

## Escalation
Escalate unresolved material diagnostics and client disputes to the **Reviewer / Lead** (Michael Shelton).

## Externally governed — tax requirements
**No advisory-fee policy is involved.** Return review/delivery is governed by **external federal and
state tax authorities and applicable professional requirements** — filing rules, filing/payment
deadlines, electronic-signature/authorization, IRS and state e-file rules, preparer obligations,
record-retention, and amendment/rejection rules — which are **not defined, summarized, or inferred**
here. **Follow the currently effective IRS and applicable state requirements** and the **currently
effective e-file provider and taxing-authority requirements**. *(Controlled citations will be added
during the Compliance Validation milestone.)*

## Operational unknowns (controlled placeholders)
- Approved **client-delivery method**. `SME CONFIRMATION REQUIRED`
- "Must-resolve" **diagnostic** threshold. `SME CONFIRMATION REQUIRED`
- Where **payment/estimate instructions** are recorded/tracked. `SME CONFIRMATION REQUIRED`

## Related
- `TAXOPS-SOP-02` — 1040 Preparation · `TAXOPS-SOP-03` — Business Return Preparation
- `TAXOPS-SOP-05` — E-file Authorization & Acknowledgements · Queued: CHK-016 (checklist)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-019 - Tax Return Review & Delivery |
| Source identifier | Confluence `23953450` (CAP-004) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | TaxDome / Drake — **needs confirmation** |
| Duplication | overlaps CHK-016 (checklist) — split into a checklist |
| Contradictions | none identified |
| Facts verified | the review→delivery→hand-off flow and the delivery-≠-filed distinction |
| Facts awaiting confirmation | approved delivery method; diagnostic threshold; payment-instruction tracking |
| Disposition recommendation | **replace** SOP-019 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 Tax) | Initial adaptation from Atlas SOP-019; separated review/delivery stages; tax requirements externally governed; delivery-≠-filed distinction; `needs_review`. |
