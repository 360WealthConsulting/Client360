---
title: "Wealth Management — SOP: AssetMark Household Setup"
page_id: "WLTH-SOP-07"
area: "WLTH"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/wealth/assetmark-household-setup.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "quarterly"
next_review: "TBD"
related: ["WLTH-SOP-03", "WLTH-SOP-04", "WLTH-SOP-08"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "25100289"
source_title: "SOP-010 - AssetMark Household Setup"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/25100289"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["25100289 (SOP-010)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-010; operational only; no client PII stored here."
---

# Wealth Management — AssetMark Household Setup

## Purpose & scope
Create or update an AssetMark household so proposals, accounts, reports, and service activity are
connected to the correct client relationship.
**In scope:** household setup **before** an AssetMark proposal or account opening. **Out of scope:**
proposal generation (`WLTH-SOP-04`) and account opening (`WLTH-SOP-03`).

## Audience
Operations (creates/updates the household); Lead Advisor (confirms members + advisory relationship).

## Prerequisites
- A client/prospect needs an AssetMark proposal, account, or report; the Wealthbox record exists.

## Required systems & permissions
- **AssetMark eWealthManager**; Wealthbox. `SME CONFIRMATION REQUIRED`: confirm the current AssetMark
  platform name/portal.

## Procedure
1. Confirm whether the client already has an AssetMark household (search first).
2. Compare AssetMark household information to Wealthbox.
3. Create a new household if none exists.
4. Add the client and related household members.
5. Confirm spelling, legal names, email addresses, phone numbers, and mailing address (match Wealthbox).
6. Assign the appropriate advisor.
7. Link or document related accounts when applicable.
8. Save confirmation/notes to Wealthbox (by reference).
9. Create the next task (proposal, account opening, or reporting).

> ⚠️ **CAUTION.** Search AssetMark **before** creating to avoid a **duplicate household**.

## Expected results
A single correct AssetMark household matching Wealthbox, with the advisor assigned and the next task created.

## Validation & evidence
Household not duplicated; names/contact match Wealthbox; advisor assignment correct; next task exists.
Do not store client PII in this page.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Duplicate household | Not searched first | Search + merge/void per AssetMark process | Merge not possible |
| Wrong members | Relationship unclear | Confirm structure with advisor | Structure disputed |
| Contact mismatch | Not compared to Wealthbox | Compare + correct before saving | — |

## Escalation
Escalate unclear household structure to the **Lead Advisor** (Michael Shelton).

## Operational unknowns (controlled placeholders)
- Current AssetMark **platform name/portal**. `SME CONFIRMATION REQUIRED`
- AssetMark **duplicate-household** handling (merge/void) mechanics. `SME CONFIRMATION REQUIRED`

## Related
- `WLTH-SOP-03` — AssetMark Account Opening · `WLTH-SOP-04` — AssetMark Proposal Generation
- `WLTH-SOP-08` — AssetMark Model Selection · Queued: CHK-008 (checklist)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-010 - AssetMark Household Setup |
| Source identifier | Confluence `25100289` (CAP-003) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | AssetMark eWealthManager / Wealthbox — **needs confirmation** |
| Duplication | overlaps CHK-008 (checklist) — split into a checklist |
| Contradictions | none identified |
| Facts verified | the household setup flow + duplicate-avoidance / Wealthbox-match controls |
| Facts awaiting confirmation | current platform name; duplicate-handling mechanics |
| Disposition recommendation | **replace** SOP-010 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1D) | Initial adaptation from Atlas SOP-010; operational only; controlled placeholders; `needs_review`. |
