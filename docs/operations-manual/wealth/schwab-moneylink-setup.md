---
title: "Wealth Management — SOP: Schwab MoneyLink Setup"
page_id: "WLTH-SOP-05"
area: "WLTH"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/wealth/schwab-moneylink-setup.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "quarterly"
next_review: "TBD"
related: ["WLTH-SOP-01", "WLTH-SOP-06"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "24805377"
source_title: "SOP-007 - Schwab MoneyLink Setup"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/24805377"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["24805377 (SOP-007)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-007; money-movement authorization follows the custodian process. No bank/account numbers stored here."
---

# Wealth Management — Schwab MoneyLink Setup

## Purpose & scope
Set up external bank instructions for a Schwab account so money movement can be handled accurately,
securely, and with proper authorization.
**In scope:** linking an external bank account to a Schwab account for ACH / recurring money movement.
**Out of scope:** wires, journals, or standing instructions that require a different Schwab process.

## Audience
Operations (collects bank info, prepares the request, tracks approval); Lead Advisor (confirms purpose);
Client (provides correct bank ownership/account information).

## Prerequisites
- The Schwab account to be linked is identified; the business reason for MoneyLink is confirmed.
- Client authorization is obtained. *(Authorization and account-linking requirements follow the
  **custodian's** process/agreement — this SOP documents the operational execution only, not the
  custodian's terms.)*

## Required systems & permissions
- **Charles Schwab Advisor Center** (or the approved Schwab MoneyLink workflow); Wealthbox.
  `SME CONFIRMATION REQUIRED`: confirm the current Schwab MoneyLink workflow/tool.

> ⚠️ **CAUTION — sensitive data.** Collect bank routing/account details only via the **approved secure
> method**. **Never** record routing numbers, bank account numbers, or client authorization values in
> this SOP or any documentation page — reference them by process only.

## Procedure
1. Confirm the business reason for MoneyLink.
2. Confirm the Schwab account that will be linked.
3. Confirm the external bank account **ownership matches** Schwab requirements.
4. Collect required bank information using the **approved secure method** (never stored here).
5. Prepare the MoneyLink request in Schwab Advisor Center (or the approved workflow).
6. **Review routing number, account number, account type, and ownership before submission** (second review).
7. Submit the request.
8. Track status until approved or rejected.
9. If rejected, review the reason and correct the issue before resubmitting.
10. Document approval in Wealthbox and the client file (by reference).
11. Confirm whether a one-time or recurring transfer should be scheduled.

## Expected results
An approved external bank link on the correct Schwab account, with authorization documented and any
transfer scheduled as intended.

## Validation & evidence
Bank ownership verified; routing/account reviewed before submission; authorization and approval/
rejection recorded in the client file (by reference — never the values).

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| MoneyLink rejected | Ownership mismatch / bad routing or account number | Review rejection reason; correct; resubmit | Repeated rejection |
| Ownership mismatch | Bank title ≠ Schwab account owner | Escalate before submission | Ownership cannot be reconciled |
| Request not tracked | No follow-up task | Create a follow-up task with a due date | — |

## Escalation
Escalate ownership mismatches and repeated rejections to the **Lead Advisor** (Michael Shelton) before
resubmitting.

## Operational unknowns (controlled placeholders)
Held as visible placeholders — not guessed; they do not block the workflow and close in the P3 pass:
- Current Schwab **MoneyLink workflow/tool**. `SME CONFIRMATION REQUIRED`
- The **approved secure method** for collecting bank information. `SME CONFIRMATION REQUIRED`
- Any second-reviewer / authorization-retention requirement. `SME CONFIRMATION REQUIRED`

## Related
- `WLTH-SOP-01` — Schwab Account Opening
- `WLTH-SOP-06` — Schwab ACAT Transfer In
- Queued: Schwab MoneyLink Checklist (CHK-005)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-007 - Schwab MoneyLink Setup |
| Source identifier | Confluence `24805377` (CAP-002) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | Schwab Advisor Center / Wealthbox — **needs confirmation** |
| Duplication | overlaps CHK-005 (checklist) — split into a checklist |
| Contradictions | none identified |
| Facts verified | the MoneyLink procedural flow + ownership/second-review controls |
| Facts awaiting confirmation | current workflow/tool; approved secure collection method; authorization-retention requirement |
| Disposition recommendation | **replace** SOP-007 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1D) | Initial adaptation from Atlas SOP-007 into the framework; operational only; controlled placeholders; `needs_review`. |
