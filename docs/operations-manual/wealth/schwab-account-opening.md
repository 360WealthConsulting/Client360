---
title: "Wealth Management — SOP: Schwab Account Opening"
page_id: "WLTH-SOP-01"
area: "WLTH"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/wealth/schwab-account-opening.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "quarterly"
next_review: "TBD"
related: ["WLTH-SOP-02", "WLTH-POL-01"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "24772609"
source_title: "SOP-006 - Schwab Account Opening"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/24772609"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["24772609 (SOP-006)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-006; procedural structure retained, specifics flagged for SME confirmation."
---

# Wealth Management — Schwab Account Opening

## Purpose & scope
Open the correct Schwab account registration for a client using complete documentation, advisor-approved
account instructions, and quality control before submission.
**In scope:** new Schwab accounts opened during onboarding, maintenance, or implementation. **Out of
scope:** AssetMark-only accounts (unless a Schwab account is also required).

## Audience
Operations staff who prepare/submit account openings; the Lead Advisor who approves registration.

## Prerequisites
- Signed advisory agreement (if advisory services apply); ADV and privacy delivery documented (if applicable).
- Client identity and contact information verified; account type approved by the advisor; beneficiary
  information requested where applicable.

## Required systems & permissions
- **Charles Schwab Advisor Center** (or the approved Schwab account-opening process). `SME CONFIRMATION
  REQUIRED`: confirm Advisor Center is the current account-opening system.
- Wealthbox (client records); Microsoft 365 (client file).

## Procedure
1. Confirm the request is tied to an approved engagement or service request.
2. Confirm account type and registration with the advisor.
3. Confirm registration: individual, joint, trust, IRA, Roth IRA, SEP IRA, SIMPLE IRA, business, or estate.
4. Review required client information for completeness.
5. Confirm beneficiary requirements for retirement and transfer-on-death registrations.
6. Confirm trusted-contact instructions where appropriate.
7. Prepare the account-opening workflow in Schwab Advisor Center (or approved process).
8. Review account title, registration, tax ID, address, and contact information before submission.
9. Submit the account-opening request.
10. Save the confirmation and account number to the client file once available (by reference — never inline).
11. Update Wealthbox with the new account information.
12. Create follow-up tasks for funding, MoneyLink, ACAT, or billing setup as needed.

> ⚠️ **CAUTION.** Escalate to the advisor **before** opening accounts for trusts, estates, business
> entities, inherited retirement accounts, or any registration that does not match the client's source
> documents.

## Expected results
The correct Schwab registration is opened, the account number is saved to the client file, Wealthbox is
updated, and downstream funding/transfer/billing tasks exist.

## Validation & evidence
Retain the account-opening confirmation and account number (by reference). Verify registration matches
advisor instruction and source documents before completion.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Wrong registration submitted | Advisor approval not confirmed | Advisor approves type before submission | Already submitted incorrectly |
| Missing beneficiary | Skipped beneficiary step | Use the account-opening checklist before closing | Retirement/TOD account affected |
| Client file not updated | Confirmation not saved | Save confirmation + update Wealthbox before completion | — |

## Escalation
Escalate ambiguous registrations to the **Lead Advisor** (Michael Shelton). `SME CONFIRMATION REQUIRED`:
confirm approval authority and any compliance documentation requirement.

## Related
- `WLTH-SOP-02` — Schwab Portfolio Connect Quarterly Billing & Fee Locking
- Queued (not yet adapted): Schwab MoneyLink Setup (SOP-007), Schwab ACAT Transfer In (SOP-008)
- Software facet: `docs/SCHWAB_PORTFOLIO_ENGINE.md` (Client360 Schwab integration — distinct)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-006 - Schwab Account Opening |
| Source identifier | Confluence `24772609` (CAP-002) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | Schwab Advisor Center / Wealthbox — **needs confirmation** |
| Duplication | overlaps CHK-004 (checklist) — split into a checklist page |
| Contradictions | none identified |
| Facts verified | the account-opening procedural flow and QC/exception structure |
| Facts awaiting confirmation | current account-opening platform; approval/compliance authority; whether MoneyLink/ACAT remain current |
| Disposition recommendation | **replace** SOP-006 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1B) | Initial adaptation from Atlas SOP-006 into the framework; `needs_review`. |
