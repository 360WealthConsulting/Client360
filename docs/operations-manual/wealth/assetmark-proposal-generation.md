---
title: "Wealth Management — SOP: AssetMark Proposal Generation"
page_id: "WLTH-SOP-04"
area: "WLTH"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/wealth/assetmark-proposal-generation.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "quarterly"
next_review: "TBD"
related: ["WLTH-SOP-03", "WLTH-SOP-07", "WLTH-SOP-08", "WLTH-POL-01"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "25133057"
source_title: "SOP-011 - AssetMark Proposal Generation"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/25133057"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["25133057 (SOP-011)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-011; the advisor-approval-before-delivery control is retained. Not investment advice; procedural only."
---

# Wealth Management — AssetMark Proposal Generation

## Purpose & scope
Create an AssetMark proposal that reflects the client's objectives, risk tolerance, time horizon,
income needs, tax considerations, and **advisor-approved** investment direction.
**In scope:** preparing/generating an AssetMark proposal for a prospect or client. **Out of scope:**
model selection (SOP-012, queued) and account opening (`WLTH-SOP-03`). This is an **operational**
procedure — it is not investment advice and asserts no specific allocation.

## Audience
Operations (prepares inputs, generates the proposal); Lead Advisor (approves risk profile, model
direction, assumptions).

## Prerequisites
- Household setup complete; discovery notes and a documented risk-tolerance discussion; investment
  objective and account type known.

_Fee assumptions, the fee schedule, and any required proposal disclosures are **externally governed** —
see current **Advisory Agreement** / **Form ADV Part 2A**; this SOP does not define them (operational
procedure only)._

## Required systems & permissions
- **AssetMark eWealthManager** (proposal tooling) and Wealthbox. `SME CONFIRMATION REQUIRED`: confirm
  the current AssetMark proposal workflow and any required proposal disclosures.

## Procedure
1. Confirm household setup is complete.
2. Review discovery notes and client objectives.
3. Confirm the proposal account type.
4. Confirm risk tolerance, income needs, and time horizon.
5. Confirm whether tax efficiency is a material consideration.
6. Enter proposal assumptions into AssetMark.
7. Select the **advisor-approved** strategy or model candidate.
8. Review allocation, risk profile, fees, and assumptions.
9. Generate the proposal.
10. Save the proposal to the client file.
11. Send the proposal to the **advisor for review before client delivery**.

> ⚠️ **CAUTION.** Do not deliver a proposal to a client before **advisor review**. Do not generate a
> proposal before risk tolerance and objective are documented.

## Expected results
A generated AssetMark proposal consistent with documented objectives and the advisor-approved model,
saved to the client file, and reviewed by the advisor before any client delivery.

## Validation & evidence
Documented risk profile and objective; advisor review recorded before delivery; fee assumptions
reviewed. No client PII/portfolio values in this page.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Proposal generated before risk discussion | Discovery not documented | Require discovery notes first | Advisor unavailable |
| Wrong account type | Registration not confirmed | Confirm registration before proposal | Ambiguous registration |
| Fee assumptions incorrect | Not reviewed vs schedule | Review fee schedule before approval | Terms unclear |

## Escalation
Escalate proposals that do not match advisor intent to the **Lead Advisor** (Michael Shelton) before
client delivery. Per LL-002, **model selection requires advisor confirmation**.

## Related
**Existing operational dependencies:** `WLTH-SOP-03` — AssetMark Account Opening · `WLTH-SOP-07` — AssetMark Household Setup · `WLTH-SOP-08` — AssetMark Model Selection
**Planned (not yet authored):** CHK-009 (Proposal Checklist) · POL-006 (Model-Selection Review Policy) · LL-002 (advisor-confirmation lesson)
**Deferred:** Controlled citations for governing sources (Form ADV Part 2A / Advisory Agreement, IRS / state requirements) — Compliance Validation milestone.
**External (referenced, not owned):** AssetMark (investment platform / eWealthManager) & custodians — externally owned; Form ADV Part 2A / Advisory Agreement — externally governed.

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-011 - AssetMark Proposal Generation |
| Source identifier | Confluence `25133057` (CAP-003); control corroborated by LL-002 `24870934` |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | AssetMark eWealthManager — **needs confirmation** |
| Duplication | overlaps CHK-009 (checklist) and SOP-012 (model selection) — to be split/linked |
| Contradictions | none identified |
| Facts verified | the proposal flow and the advisor-approval-before-delivery control (LL-002) |
| Facts awaiting confirmation | current AssetMark proposal workflow; required disclosures; fee-schedule specifics |
| Disposition recommendation | **replace** SOP-011 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1B) | Initial adaptation from Atlas SOP-011 into the framework; `needs_review`. |
| 0.2 | 2026-07-18 | Claude (0.12 Stabilization) | Editorial stabilization: standardized externally-governed heading pattern; categorized Related into existing/planned/deferred/external; bidirectional cross-references; no operational change; still `needs_review`. |
