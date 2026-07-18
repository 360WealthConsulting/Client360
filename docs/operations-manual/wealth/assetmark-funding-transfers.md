---
title: "Wealth Management — SOP: AssetMark Funding & Transfers"
page_id: "WLTH-SOP-09"
area: "WLTH"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/wealth/assetmark-funding-transfers.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "quarterly"
next_review: "TBD"
related: ["WLTH-SOP-03", "WLTH-SOP-06", "WLTH-SOP-10"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "25198593"
source_title: "SOP-014 - AssetMark Funding & Transfers"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/25198593"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["25198593 (SOP-014)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-014; operational money movement; no statement data/account numbers stored here."
---

# Wealth Management — AssetMark Funding & Transfers

## Purpose & scope
Track AssetMark funding and transfer activity until assets are received, invested, and documented.
**In scope:** new money, transfers, rollovers, or account funding for AssetMark accounts. **Out of
scope:** Schwab ACAT (`WLTH-SOP-06`) except where an AssetMark account is also funded.

## Audience
Operations (submits/tracks funding-transfer workflow); Lead Advisor (confirms source + timing); Client
(authorization + source account information).

## Prerequisites
- The AssetMark account is open; funding/transfer source, cash amount or asset list, and timing are known.

> ⚠️ **CAUTION — sensitive data.** Handle transfer statements, source-account details, and
> authorizations only by approved secure means. **Never** record account numbers, balances, or holdings
> in this SOP.

## Required systems & permissions
- **AssetMark eWealthManager**, Wealthbox, and **Charles Schwab Advisor Center** (where the source is
  Schwab). `SME CONFIRMATION REQUIRED`: confirm the current AssetMark funding/transfer workflow.

## Procedure
1. Confirm the AssetMark account is open.
2. Confirm the funding or transfer source.
3. Confirm funding type — cash, securities, rollover, or transfer.
4. Review the source-account registration.
5. Confirm client authorization.
6. Submit the funding/transfer request through the approved workflow.
7. Create a tracking task.
8. **Review status at least weekly** until complete.
9. Document any rejection or delay.
10. Confirm assets arrive.
11. **Confirm investment-implementation status.**
12. Update Wealthbox and notify the advisor.

## Expected results
Assets received into the AssetMark account, invested per the approved model, tracked to completion, and
the advisor notified.

## Validation & evidence
Funding source documented; authorization saved (securely, by reference); status tracked; arrival +
investment implementation verified; advisor notified.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Transfer submitted without statement | Statement not obtained | Require statement before transfer | Client unresponsive |
| Status not tracked | No recurring task | Create recurring follow-up | Beyond expected timeframe |
| Funded but not invested | Implementation step missed | Confirm investment implementation | Cash left uninvested |

## Escalation
Escalate registration incompatibilities and stalled transfers to the **Lead Advisor** (Michael Shelton).

## Operational unknowns (controlled placeholders)
- Current AssetMark **funding/transfer workflow**. `SME CONFIRMATION REQUIRED`
- Expected **timeframe** / when to escalate; investment-implementation confirmation method. `SME CONFIRMATION REQUIRED`

## Related
- `WLTH-SOP-03` — AssetMark Account Opening · `WLTH-SOP-06` — Schwab ACAT Transfer In
- `WLTH-SOP-10` — AssetMark Billing Review · Queued: CHK-011 (checklist)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-014 - AssetMark Funding & Transfers |
| Source identifier | Confluence `25198593` (CAP-003) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | AssetMark eWealthManager / Wealthbox / Schwab — **needs confirmation** |
| Duplication | overlaps CHK-011 (checklist) — split into a checklist |
| Contradictions | none identified |
| Facts verified | the funding/transfer flow + weekly-tracking / arrival-and-implementation controls |
| Facts awaiting confirmation | current workflow; timeframe/escalation; implementation-confirmation method |
| Disposition recommendation | **replace** SOP-014 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1D) | Initial adaptation from Atlas SOP-014; operational only; controlled placeholders; `needs_review`. |
