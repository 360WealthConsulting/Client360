---
title: "Wealth Management — SOP: Schwab Portfolio Connect Quarterly Billing & Fee Locking"
page_id: "WLTH-SOP-02"
area: "WLTH"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/wealth/schwab-portfolio-connect-billing.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "quarterly"
next_review: "TBD"
related: ["WLTH-SOP-01", "WLTH-POL-01"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "24870913"
source_title: "SOP-009 - Quarterly Schwab Billing"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/24870913"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["24870913 (SOP-009)", "24707083 (POL-004, in part)", "24674305 (LL-001 Portfolio Connect Filter Lesson)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-009; the fee-lock/filter control is corroborated by the operator-provided control and LL-001."
---

# Wealth Management — Schwab Portfolio Connect Quarterly Billing & Fee Locking

## Purpose & scope
Review, approve, and transmit quarterly Schwab advisory fees through a controlled process that
protects billing accuracy and produces a clear audit trail.
**In scope:** quarterly Schwab advisory billing via Portfolio Connect (or the approved Schwab billing
workflow). **Out of scope:** non-Schwab billing and manually invoiced planning engagements.

## Audience
Operations staff who prepare billing; the Lead Advisor who approves; Compliance who reviews records.

## Prerequisites
- The billing calendar and billing period are confirmed.
- The current fee worksheet is available.
- New-client billing instructions and the terminated-client list are on hand.

## Required systems & permissions
- Schwab **Portfolio Connect** (billing) — access to the fee worksheet and transmission.
  `SME CONFIRMATION REQUIRED`: confirm Portfolio Connect (vs. another Schwab billing tool) is the
  current system of record for advisory fee transmission.
- Wealthbox (client records). Reference credentials by name only — never store them here.

## Procedure
1. Confirm the billing calendar and billing period.
2. Open the approved Schwab billing workflow (Portfolio Connect).
3. Generate or access the current fee worksheet.
4. Review the accounts **included** in billing.
5. Review the accounts **excluded** from billing.
6. Confirm new clients are billed per their agreement (rate and start date).
7. Confirm terminated clients are excluded or prorated as appropriate.
8. Review fee rates against signed agreements.
9. Review cash availability where applicable.
10. Investigate and resolve exceptions **before** locking fees.
11. **Lock management fees** when the worksheet is ready.
12. **Clear all filters** after locking fees.
13. **Confirm the worksheet still displays the expected accounts after filters are cleared** — verify
    the worksheet was **not** unintentionally cleared by a retained filter.
14. Obtain **advisor approval** before transmission.
15. **Transmit** fees through the approved Schwab workflow.
16. Save the transmission confirmation and billing records (evidence).
17. Document exceptions and follow-up items.

> ⚠️ **CAUTION — retained-filter control (LL-001).** A filter left active after locking fees can make
> the worksheet **appear empty or partial**. Always clear filters after locking and re-verify that the
> expected accounts are visible **before** transmitting. This is the central control of this SOP.

## Expected results
- Fees are locked, reviewed, advisor-approved, and transmitted for exactly the intended accounts.
- A saved transmission confirmation and billing worksheet evidence exist for the period.

## Validation & evidence
- Retain: the transmission confirmation, the final fee worksheet, and a record of advisor approval.
  Store by reference in the client/billing file — never paste account numbers or balances here.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Worksheet appears empty after locking | Retained filter (LL-001) | Clear filters; re-verify expected accounts | Accounts still missing after clearing |
| New client billed incorrectly | Wrong rate/start date | Review agreement and start date; correct before transmit | Agreement terms unclear |
| Terminated client billed | Termination not applied | Review termination list; exclude/prorate | Termination date disputed |
| Fees transmitted without approval | Approval step skipped | Halt; document; obtain approval retroactively per policy | Any transmission error |

## Escalation
Escalate billing exceptions and any transmission error to the **Lead Advisor** (Michael Shelton) before
proceeding. `SME CONFIRMATION REQUIRED`: confirm the approval authority and any compliance-review
requirement for fee transmission.

## Related
- `WLTH-SOP-01` — Schwab Account Opening
- `WLTH-POL-01` — Schwab Documentation & Billing-Review Policy *(to be adapted from POL-003/POL-004)*
- Software facet: `docs/SCHWAB_PORTFOLIO_ENGINE.md` (Client360 Schwab portfolio integration — distinct)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-009 - Quarterly Schwab Billing |
| Source identifier | Confluence `24870913` (CAP-002); control corroborated by LL-001 `24674305` |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | Portfolio Connect / Wealthbox — **needs confirmation as current** |
| Duplication | overlaps POL-004 (billing-review policy) and CHK-007 (billing checklist) — to be split into a policy + checklist |
| Contradictions | none identified |
| Facts verified | the fee-lock → clear-filters → verify-worksheet → approve → transmit → retain-evidence control (operator-corroborated) |
| Facts awaiting confirmation | current billing platform; billing-calendar dates; approval/compliance authority; fee-schedule specifics |
| Disposition recommendation | **replace** SOP-009 after quality review; supersede LL-001 (control folded in) |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1B) | Initial adaptation from Atlas SOP-009 (+LL-001 control) into the framework; `needs_review`. |
