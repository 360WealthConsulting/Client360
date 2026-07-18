---
title: "Wealth Management — SOP: AssetMark Billing Review"
page_id: "WLTH-SOP-10"
area: "WLTH"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/wealth/assetmark-billing-review.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "quarterly"
next_review: "TBD"
related: ["WLTH-SOP-03", "WLTH-SOP-08", "WLTH-SOP-02"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "25198614"
source_title: "SOP-015 - AssetMark Billing Review"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/25198614"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["25198614 (SOP-015)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-015; billing policy externally governed (not reproduced); AssetMark platform mechanics separated; no fee rates stored here."
---

# Wealth Management — AssetMark Billing Review

## Purpose & scope
Verify that AssetMark billing setup and records **conform to** the client's governing fee terms and
operational expectations.
**In scope:** setting up, reviewing, or auditing AssetMark billing **conformance**. **Out of scope:**
defining fee policy (externally governed — see below).

The four concerns below are kept deliberately separate.

## 1. Operational review & execution steps (this SOP)
1. Confirm the signed advisory agreement and fee schedule are on file (by reference).
2. Confirm the account is active.
3. Review the AssetMark billing setup.
4. **Confirm the fee rate matches the executed advisory agreement** (conformance check — this SOP does
   not set the rate).
5. Confirm the billing start date.
6. Review any excluded or special-billing accounts.
7. Document exceptions.
8. Obtain **advisor approval** for exceptions.
9. Save the billing-review notes to the client file (by reference).
10. Create a follow-up task if any issue remains unresolved.

**Expected result:** AssetMark billing setup verified to **conform** to the governing fee terms;
exceptions documented and approved; review evidence saved.

## 2. Externally governed advisory-fee policy (referenced, not reproduced)
The **fee schedule, billing frequency and basis, advance/arrears, valuation, proration, minimums,
household aggregation, excluded assets, and refund/correction terms** are **governed by the current
Form ADV Part 2A and the executed advisory agreement** — **not** defined by this SOP:
- **See current Form ADV Part 2A.**
- **See current Advisory Agreement.**

This SOP only **verifies conformance** to those governing documents; it must not restate or infer fee
policy. *(Controlled citations will be added during the Compliance Validation milestone.)*

## 3. AssetMark platform / custodian requirements (referenced)
The **mechanics** of AssetMark billing setup, fee calculation, and any **custodian fee-deduction
authorization** follow **AssetMark's / the custodian's** process — this SOP documents the firm's
**review** of that setup, not AssetMark's internal billing rules. Platform screen labels, billing-setup
fields, and deduction mechanics are AssetMark-specific and are flagged for confirmation (§4).

## 4. Unresolved operational practices (controlled placeholders)
Held as visible placeholders — not guessed; they close in the P3 / Compliance Validation pass:
- Current AssetMark **billing-setup workflow and screen labels**. `SME CONFIRMATION REQUIRED`
- **Who** performs vs. approves the review; exception-approval authority. `SME CONFIRMATION REQUIRED`
- **Billing-review cadence** (per account event vs. periodic audit). `SME CONFIRMATION REQUIRED`
- Custodian **fee-deduction authorization** handling (platform/custodian requirement). `SME CONFIRMATION REQUIRED`

## Audience
Operations (reviews billing setup, documents results); Lead Advisor (approves fee exceptions);
Compliance (reviews billing records as required).

## Required systems & permissions
- **AssetMark eWealthManager**; Wealthbox. `SME CONFIRMATION REQUIRED`: confirm the current AssetMark
  billing-review workflow.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Fee rate mismatch | Setup ≠ executed agreement | Correct to conform to the agreement (governing) | Agreement terms unclear |
| Wrong billing start date | Not verified vs. onboarding | Verify + correct | Start basis disputed |
| Exception undocumented | Notes skipped | Require exception notes + advisor approval | Recurring exception |

## Escalation
Escalate any billing that does **not conform** to the governing documents to the **Lead Advisor**
(Michael Shelton); compliance-sensitive items keep restricted handling.

## Related
- `WLTH-SOP-02` — Schwab Portfolio Connect Billing (parallel billing control) · `WLTH-SOP-03` —
  AssetMark Account Opening · `WLTH-SOP-08` — AssetMark Model Selection · Queued: CHK-012, POL-005

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-015 - AssetMark Billing Review |
| Source identifier | Confluence `25198614` (CAP-003) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | AssetMark eWealthManager / Wealthbox — **needs confirmation** |
| Duplication | overlaps CHK-012 (checklist) + POL-005 (policy) — split/linked |
| Contradictions | none identified |
| Facts verified | the billing-**conformance** review flow + exception-approval control |
| Facts awaiting confirmation | platform workflow/screens; review/approval authority; review cadence; deduction authorization mechanics |
| Disposition recommendation | **replace** SOP-015 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 P1D) | Initial adaptation from Atlas SOP-015; 4-way separation (operational / externally-governed fee policy / AssetMark-custodian requirements / unresolved practices); no fee rates stored; `needs_review`. |
