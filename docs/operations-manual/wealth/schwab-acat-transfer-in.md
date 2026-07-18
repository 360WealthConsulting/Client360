---
title: "Wealth Management — SOP: Schwab ACAT Transfer In"
page_id: "WLTH-SOP-06"
area: "WLTH"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/wealth/schwab-acat-transfer-in.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "quarterly"
next_review: "TBD"
related: ["WLTH-SOP-01", "WLTH-SOP-05"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "24838145"
source_title: "SOP-008 - Schwab ACAT Transfer In"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/24838145"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["24838145 (SOP-008)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-008; no client statement data or account numbers stored here."
---

# Wealth Management — Schwab ACAT Transfer In

## Purpose & scope
Transfer client assets into Schwab using a controlled process that reduces rejection risk and keeps the
client informed.
**In scope:** full or partial ACAT transfers into Schwab from another custodian. **Out of scope:**
non-ACAT transfers, direct rollovers, wire transfers, and journals.

## Audience
Operations (reviews statement, prepares/tracks the transfer); Lead Advisor (confirms assets and any
exclusions); Client (provides the current statement and authorization).

## Prerequisites
- The receiving Schwab account is open and correctly registered.
- A current statement for the delivering account is obtained; client authorization is in place.

> ⚠️ **CAUTION — sensitive data.** Handle the client statement and account numbers only via approved
> secure means. **Never** record account numbers, balances, or holdings in this SOP — reference by process.

## Required systems & permissions
- **Charles Schwab Advisor Center** (or the approved Schwab ACAT process); Wealthbox.
  `SME CONFIRMATION REQUIRED`: confirm the current Schwab ACAT workflow.

## Procedure
1. Confirm the receiving Schwab account is open and correctly registered.
2. Obtain a current statement for the delivering account.
3. **Compare delivering-account registration to the Schwab receiving-account registration.**
4. Confirm full or partial transfer instruction.
5. Confirm whether any assets should be excluded.
6. **Review likely transfer issues** — proprietary funds, annuities, alternative investments, margin,
   or unsettled trades.
7. Prepare the ACAT request using the approved Schwab process.
8. Review account numbers and transfer type before submission (second review).
9. Submit the ACAT request.
10. Create a transfer-tracking task.
11. **Review transfer status at least weekly** until complete.
12. If rejected, document the rejection reason and corrective action.
13. Confirm assets arrived; track cost-basis status when applicable.
14. Update Wealthbox and notify the advisor.

## Expected results
Assets transferred into the correctly registered Schwab account, with rejections resolved, arrival
confirmed, and the advisor notified.

## Validation & evidence
Current statement saved (securely, by reference); registrations reviewed before submission; transfer
type confirmed; rejection reasons documented; completion verified.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| ACAT rejected | Registration mismatch / non-transferable assets | Compare statement to Schwab account; correct; resubmit | Repeated rejection |
| Partial transfer unclear | No explicit asset instruction | Get explicit asset + cash instructions | Instructions conflict |
| Transfer stalls | Not monitored | Recurring follow-up until complete | Beyond expected timeframe |

## Escalation
Escalate registration mismatches, non-transferable assets, and stalled transfers to the **Lead Advisor**
(Michael Shelton).

## Operational unknowns (controlled placeholders)
Held as visible placeholders — not guessed:
- Current Schwab **ACAT workflow/tool**. `SME CONFIRMATION REQUIRED`
- **Cost-basis tracking** method/expectations. `SME CONFIRMATION REQUIRED`
- Expected transfer **timeframe** / when to escalate a stall. `SME CONFIRMATION REQUIRED`

## Related
- `WLTH-SOP-01` — Schwab Account Opening
- `WLTH-SOP-05` — Schwab MoneyLink Setup
- Queued: Schwab ACAT Transfer Checklist (CHK-006)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-008 - Schwab ACAT Transfer In |
| Source identifier | Confluence `24838145` (CAP-002) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | Schwab Advisor Center / Wealthbox — **needs confirmation** |
| Duplication | overlaps CHK-006 (checklist) — split into a checklist |
| Contradictions | none identified |
| Facts verified | the ACAT procedural flow + registration-match / transfer-issue-review / weekly-tracking controls |
| Facts awaiting confirmation | current workflow/tool; cost-basis tracking; expected timeframe/escalation |
| Disposition recommendation | **replace** SOP-008 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1D) | Initial adaptation from Atlas SOP-008 into the framework; operational only; controlled placeholders; `needs_review`. |
