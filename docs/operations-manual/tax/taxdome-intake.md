---
title: "Tax Operations — SOP: TaxDome Client Intake"
page_id: "TAXOPS-SOP-01"
area: "TAXOPS"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/tax/taxdome-intake.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "annual"
next_review: "TBD"
related: ["TAXOPS-SOP-02", "TAXOPS-POL-01"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "23920691"
source_title: "SOP-016 - TaxDome Intake"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/23920691"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["23920691 (SOP-016)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-016; manual TaxDome intake — no Client360 integration implied."
---

# Tax Operations — TaxDome Client Intake

## Purpose & scope
Collect and organize tax documents through TaxDome so the tax team can prepare returns efficiently and
identify planning opportunities.
**In scope:** annual return intake, new tax clients, and prospects asked to upload documents before
discovery. **Out of scope:** return preparation (see `TAXOPS-SOP-02`).

## Audience
Client Service (creates access, requests documents); Tax Preparer (reviews intake completeness).

## Prerequisites
- A client or prospect record exists; return type is known; prior-year return requested if available.

## Required systems & permissions
- **TaxDome** (document collection / organizers). Microsoft 365 (client comms).
- `SME CONFIRMATION REQUIRED`: confirm TaxDome is the current intake platform and which organizer/
  questionnaire templates are in use for the current tax year.

## Procedure
1. Confirm the client or prospect record exists.
2. Create or verify the TaxDome account.
3. Confirm email address and phone number.
4. Assign the correct organizer, questionnaire, or document request.
5. Request the prior-year tax return if not already available.
6. Request current-year source documents.
7. For business owners, request the business return, P&L, balance sheet, payroll, and debt information
   when relevant.
8. Monitor uploads.
9. List missing documents.
10. Notify the client of missing items.
11. Mark intake **ready** only when the minimum required documents are received or exceptions are documented.

## Expected results
A complete, organized TaxDome document set (or a documented exception list) ready for preparation.

## Validation & evidence
Uploaded documents are visible in TaxDome; missing-item requests and exceptions are recorded. Do not
copy client tax data or PII into this page.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Client can't access TaxDome | Wrong email on invite | Confirm email; re-send invitation | Access still fails |
| Missing business financials | Business intake not used | Apply the business-owner intake checklist | Client unresponsive |
| Documents uploaded but not reviewed | No review task | Assign an intake-review task | — |

## Escalation
Escalate unresponsive clients or unclear return scope to the **Tax Preparer / Lead** (Michael Shelton).

## Related
- `TAXOPS-SOP-02` — 1040 Individual Return Preparation (Drake)
- Queued (not yet adapted): Business Return (SOP-018), Review & Delivery (SOP-019), E-file Authorization
  & Acknowledgements (SOP-020), IRS Notice (SOP-021), Extensions (SOP-023), Estimates (SOP-024)
- Software facet: `docs/EPIC_5_TAX_PRACTICE_PLATFORM.md` (Client360 Tax platform — distinct)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-016 - TaxDome Intake |
| Source identifier | Confluence `23920691` (CAP-004) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | TaxDome — **needs confirmation**; organizer templates unverified |
| Duplication | overlaps CHK-013 (intake checklist) — split into a checklist |
| Contradictions | none identified |
| Facts verified | the intake procedural flow (create access → assign organizer → request docs → track → mark ready) |
| Facts awaiting confirmation | current organizer/questionnaire templates; minimum-required-document list; current-year specifics |
| Disposition recommendation | **replace** SOP-016 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1B) | Initial adaptation from Atlas SOP-016 into the framework; `needs_review`. |
