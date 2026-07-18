---
title: "Wealth Management — SOP: AssetMark Account Opening"
page_id: "WLTH-SOP-03"
area: "WLTH"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/wealth/assetmark-account-opening.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "quarterly"
next_review: "TBD"
related: ["WLTH-SOP-04", "WLTH-SOP-07", "WLTH-SOP-08", "WLTH-SOP-09", "WLTH-SOP-10", "WLTH-POL-01"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "24838166"
source_title: "SOP-013 - AssetMark Account Opening"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/24838166"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["24838166 (SOP-013)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-013; platform/screen specifics flagged for SME confirmation."
---

# Wealth Management — AssetMark Account Opening

## Purpose & scope
Open an AssetMark account with the correct registration, model, fee schedule, documentation, and
follow-up tasks.
**In scope:** opening a new AssetMark account. **Out of scope:** proposal generation (`WLTH-SOP-04`),
funding/transfers, and billing (queued).

## Audience
Operations (prepares/tracks the opening); Lead Advisor (approves account type, model, objective);
Compliance (documentation/disclosures).

## Prerequisites
- Signed advisory agreement; household setup complete; account type, approved model, and fee schedule
  confirmed; beneficiary and funding/transfer instructions as applicable.

_The advisory agreement, **fee schedule**, and billing terms are **externally governed** — see current
**Advisory Agreement** / **Form ADV Part 2A**; this SOP does not define them (operational procedure only)._

## Required systems & permissions
- **AssetMark eWealthManager** (account opening) and Wealthbox. `SME CONFIRMATION REQUIRED`: confirm
  "eWealthManager" is the current AssetMark platform name/portal and the current opening workflow.

## Procedure
1. Confirm engagement documents are signed.
2. Confirm household setup is complete.
3. Confirm account type and registration.
4. Confirm the approved model selection (documented advisor approval).
5. Confirm the fee schedule.
6. Confirm beneficiary requirements where applicable.
7. Prepare the account-opening request in AssetMark.
8. Review all account data before submission.
9. Submit the account-opening request.
10. Track status until complete.
11. Save the account confirmation to the client file (by reference).
12. Update Wealthbox with the account information.
13. Create funding, transfer, and billing tasks.

> ⚠️ **CAUTION.** Do not open the account before **documented model approval**; stop and obtain advisor
> approval if model selection is not documented.

## Expected results
The AssetMark account is opened with the correct registration and approved model, saved to the client
file, Wealthbox updated, and downstream tasks created.

## Validation & evidence
Signed agreement on file; documented model approval; correct registration and fee schedule; follow-up
tasks exist. No client PII/account numbers in this page.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Account opened before model approval | Approval not documented | Require documented approval before opening | Already opened |
| Fee-schedule mismatch | Not compared to agreement | Compare to signed agreement; correct | Terms unclear |
| No funding task created | Step skipped | Create funding task before closing the opening | — |

## Escalation
Escalate unclear registration or missing model approval to the **Lead Advisor** (Michael Shelton).

## Related
**Existing operational dependencies:** `WLTH-SOP-04` — AssetMark Proposal Generation · `WLTH-SOP-07` — AssetMark Household Setup · `WLTH-SOP-08` — AssetMark Model Selection · `WLTH-SOP-09` — AssetMark Funding & Transfers · `WLTH-SOP-10` — AssetMark Billing Review
**Planned (not yet authored):** `WLTH-POL-01` (Wealth Documentation Policy) · CHK-008–012 (AssetMark checklists) · POL-005/006 (AssetMark policies)
**Deferred:** Controlled citations for governing sources (Form ADV Part 2A / Advisory Agreement, IRS / state requirements) — Compliance Validation milestone.
**External (referenced, not owned):** AssetMark (investment platform / eWealthManager) & custodians — externally owned; Form ADV Part 2A / Advisory Agreement — externally governed.

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-013 - AssetMark Account Opening |
| Source identifier | Confluence `24838166` (CAP-003) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | AssetMark eWealthManager / Wealthbox — **needs confirmation** |
| Duplication | overlaps CHK-010 (checklist) — split into a checklist |
| Contradictions | none identified |
| Facts verified | the account-opening flow and the documented-model-approval control |
| Facts awaiting confirmation | current AssetMark platform name/workflow; fee-schedule specifics; approval authority |
| Disposition recommendation | **replace** SOP-013 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1B) | Initial adaptation from Atlas SOP-013 into the framework; `needs_review`. |
| 0.2 | 2026-07-18 | Claude (0.12 Stabilization) | Editorial stabilization: standardized externally-governed heading pattern; categorized Related into existing/planned/deferred/external; bidirectional cross-references; no operational change; still `needs_review`. |
