---
title: "Tax Operations — SOP: 1040 Individual Return Preparation (Drake)"
page_id: "TAXOPS-SOP-02"
area: "TAXOPS"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/tax/tax-1040-return-workflow.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "annual"
next_review: "TBD"
related: ["TAXOPS-SOP-01", "TAXOPS-POL-01"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "23920712"
source_title: "SOP-017 - 1040 Tax Return Workflow"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/23920712"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["23920712 (SOP-017)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-017; Drake is a server-based application — no live Client360-Drake integration or e-file integration is implied."
---

# Tax Operations — 1040 Individual Return Preparation (Drake)

## Purpose & scope
Prepare individual income tax returns consistently while identifying planning opportunities to refer
into the 360 advisory process.
**In scope:** Form 1040 individual returns, preparation through hand-off to review. **Out of scope:**
business returns (SOP-018), review & delivery (SOP-019), and e-file authorization/acknowledgements
(SOP-020) — queued as separate SOPs.

> ⚠️ **CAUTION — no live integration implied.** Preparation is performed in **Drake Tax**, a
> **server-based** application. This SOP describes the **current manual** workflow. It does **not**
> imply any live Client360↔Drake integration or automated e-file connectivity. `SME CONFIRMATION
> REQUIRED`: confirm Drake's current deployment (server/workstation) and whether any Client360
> integration is in production before describing it as available.

## Audience
Tax Preparer (prepares from source documents); Reviewer (accuracy + planning); Client Service (tracks
documents, delivery, e-file authorization).

## Prerequisites
- Intake is ready (`TAXOPS-SOP-01`); prior-year return and current-year source documents are available.

## Required systems & permissions
- **Drake Tax** (return preparation) and **TaxDome** (documents/workflow). `SME CONFIRMATION REQUIRED`:
  confirm current Drake version/deployment and access model.

## Procedure
1. Confirm engagement and intake status.
2. Review the prior-year return.
3. Review current-year source documents.
4. Identify missing documents **before** preparation.
5. Prepare the return in **Drake**.
6. Compare current year to prior year for material changes.
7. Note unusual items, missing items, and planning opportunities.
8. Check for estimated-tax needs.
9. Check for retirement, investment, business, or tax-planning opportunities.
10. Move the return to review.
11. Resolve reviewer notes.
12. Move the return to the delivery workflow (see queued SOP-019/020).

## Expected results
A prepared 1040 with a completed prior-year comparison, documented planning opportunities, and resolved
reviewer notes, ready for delivery/e-file authorization.

## Validation & evidence
Prior-year comparison completed; missing items documented; reviewer notes resolved; planning
opportunities captured. Never copy return data, SSNs, or client PII into this page.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Return prepared without prior-year comparison | Step skipped | Require the prior-year review step | Material variance found |
| Planning opportunity missed | No planning note step | Add a planning note to the review | Advisory hand-off needed |
| Missing documents found late | Intake review incomplete | Complete intake review before preparation | Deadline at risk |

## Escalation
Escalate scope/planning hand-offs to the **Lead / Advisor** (Michael Shelton). Create an advisory
hand-off task when a planning opportunity is identified.

## Operational unknowns (controlled placeholders)
Held as visible placeholders — not guessed:
- **Business-confirmed:** Drake is on the **office server**; **no live Client360↔Drake / e-file
  integration** exists (Client360 not yet on that server). *(Do not present any integration as available.)*
- Drake **version** on the server. `SME CONFIRMATION REQUIRED`
- **E-file authorization & acknowledgement** process (downstream — Atlas SOP-020; next Tax batch). `SME CONFIRMATION REQUIRED`

## Externally governed — tax requirements
**No advisory-fee policy is involved.** However, return preparation is governed by **external federal
and state tax authorities and applicable professional requirements** — filing rules, filing/payment
deadlines, electronic-signature and authorization requirements, IRS and state e-file rules, preparer
obligations, record-retention, and amendment/rejection rules. These are **not defined, summarized, or
inferred** in this SOP. **Follow the currently effective IRS and applicable state requirements** and the
**currently effective e-file provider and taxing-authority requirements**. *(Controlled citations will
be added during the Compliance Validation milestone.)*

## Related
- `TAXOPS-SOP-01` — TaxDome Client Intake
- Queued (not yet adapted): Review & Delivery (SOP-019), E-file Authorization & Acknowledgements
  (SOP-020), Business Return (SOP-018), IRS Notice (SOP-021)
- Software facet: `docs/EPIC_5_TAX_PRACTICE_PLATFORM.md`, `docs/TAX_RETURN_LIFECYCLE.md` (Client360 —
  distinct; not a live Drake connector)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-017 - 1040 Tax Return Workflow |
| Source identifier | Confluence `23920712` (CAP-004) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | Drake (server-based) / TaxDome — **needs confirmation**; no integration implied |
| Duplication | overlaps CHK-014 (1040 checklist) — split into a checklist |
| Contradictions | none identified |
| Facts verified | the preparation → review → delivery procedural flow and QC structure |
| Facts awaiting confirmation | Drake deployment/version; e-file process specifics (SOP-020); any Client360 integration status |
| Disposition recommendation | **replace** SOP-017 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1B) | Initial adaptation from Atlas SOP-017; added the "no live integration implied" caution; `needs_review`. |
| 0.2 | 2026-07-17 | Claude (0.12 Tax QR) | Quality-review pass: consolidated operational unknowns as controlled placeholders; restated business-confirmed Drake-on-server / no-live-integration facts; added "Externally governed — tax requirements" (no advisory-fee policy, but tax-law/preparer/filing/authorization/e-file/retention requirements externally governed, referenced not defined); still `needs_review`. |
