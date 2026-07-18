---
title: "Tax Operations — SOP: Business Return Preparation"
page_id: "TAXOPS-SOP-03"
area: "TAXOPS"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/tax/business-return-workflow.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "annual"
next_review: "TBD"
related: ["TAXOPS-SOP-01", "TAXOPS-SOP-02", "TAXOPS-SOP-04", "TAXOPS-SOP-05", "TAXOPS-SOP-07", "TAXOPS-SOP-08"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "23986218"
source_title: "SOP-018 - Business Tax Return Workflow"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/23986218"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["23986218 (SOP-018)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-018; entity-type branches described operationally; no tax-law requirements restated. No live Client360-Drake integration implied."
---

# Tax Operations — Business Return Preparation

## Purpose & scope
Prepare business tax returns while using the return as a diagnostic tool for business health, cash
flow, debt, owner compensation, and planning opportunities.
**In scope:** business return workflows (e.g., 1120S, 1065, Schedule C support) prepared or reviewed by
360. **Out of scope:** individual 1040 preparation (`TAXOPS-SOP-02`); review & delivery (`TAXOPS-SOP-04`).

> ⚠️ **CAUTION — no live integration implied.** Preparation is in **Drake** (server-based). This SOP is
> the **current manual** workflow; it does **not** imply any live Client360↔Drake integration.

## Audience
Tax Preparer (prepares, documents questions); Reviewer (accuracy + planning); Advisor (planning issues).

## Prerequisites
- Business-return intake is ready; prior-year business return and current-year books are available.

## Required systems & permissions
- **Drake Tax** (preparation) and **TaxDome** (documents/workflow). `SME CONFIRMATION REQUIRED`:
  confirm current Drake deployment/version.

## Procedure
1. Confirm engagement and intake status.
2. Review the prior-year business return.
3. Review the current-year P&L.
4. Review the balance sheet.
5. Review depreciation and fixed-asset activity.
6. Review officer compensation and employee wages.
7. Review debt and loan activity.
8. Prepare the return in **Drake**.
9. Compare revenue, expenses, depreciation, wages, and profit to prior year.
10. Identify planning opportunities.
11. Identify missing documents or unresolved bookkeeping issues.
12. Move the return to review (`TAXOPS-SOP-04`).
13. Resolve reviewer notes.
14. Create advisory hand-off tasks for material opportunities.

## Entity-type branches (operational)
Handle these **operationally**; the applicable tax-law treatment is externally governed (below), not
restated here:
- **1120S (S-corporation):** review **officer compensation** and shareholder K-1 outputs.
- **1065 (partnership):** review partner allocations and K-1 outputs.
- **Schedule C (sole proprietor):** prepared with the owner's individual return (`TAXOPS-SOP-02`).
- Other business types: `SME CONFIRMATION REQUIRED` — confirm the in-scope entity types and any
  operational differences.

## Expected results
A prepared business return with a completed prior-year comparison, documented planning opportunities,
and resolved bookkeeping questions, ready for review.

## Validation & evidence
P&L / balance sheet / depreciation / officer compensation reviewed; debt & cash-flow issues noted;
planning opportunities assigned. Never copy return data or client PII into this page.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Prepared from incomplete books | P&L/balance sheet not ready | Confirm book readiness first | Books cannot be reconciled |
| Owner-compensation issue missed | Step skipped | Review officer compensation every time | Material compensation concern |
| Planning issue stays in tax notes | No hand-off | Create advisory hand-off task | — |

## Escalation
Escalate business-health concerns and material planning issues to the **Advisor / Lead** (Michael Shelton).

## Externally governed — tax requirements
**No advisory-fee policy is involved.** However, business-return preparation is governed by **external
federal and state tax authorities and applicable professional requirements** — filing rules, deadlines,
electronic-signature/authorization, IRS and state e-file rules, preparer obligations, record-retention,
and amendment/rejection rules — which are **not defined, summarized, or inferred** here. **Follow the
currently effective IRS and applicable state requirements** and the **currently effective e-file
provider and taxing-authority requirements**. *(Controlled citations will be added during the Compliance
Validation milestone.)*

## Operational unknowns (controlled placeholders)
- In-scope **entity types** and their operational differences. `SME CONFIRMATION REQUIRED`
- Drake **version**; bookkeeping-readiness threshold. `SME CONFIRMATION REQUIRED`

## Related
**Existing operational dependencies:** `TAXOPS-SOP-01` — TaxDome Client Intake · `TAXOPS-SOP-02` — 1040 Preparation · `TAXOPS-SOP-04` — Review & Delivery · `TAXOPS-SOP-05` — E-file Authorization & Acknowledgements · `TAXOPS-SOP-07` — Tax Extensions · `TAXOPS-SOP-08` — Quarterly Estimated Payments
**Planned (not yet authored):** CHK-015 (Business Return Checklist) · SOP-022 (Tax Planning Opportunity)
**Deferred:** Controlled citations for governing sources (Form ADV Part 2A / Advisory Agreement, IRS / state requirements) — Compliance Validation milestone.
**External (referenced, not owned):** IRS & applicable state tax authorities; the e-file provider; Drake (server-based) & TaxDome — externally owned/governed.

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-018 - Business Tax Return Workflow |
| Source identifier | Confluence `23986218` (CAP-004) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | Drake / TaxDome — **needs confirmation**; no integration implied |
| Duplication | overlaps CHK-015 (checklist) + SOP-022 (planning) — split/linked |
| Contradictions | none identified |
| Facts verified | the business-return prep flow + prior-year comparison / officer-comp / planning-handoff controls |
| Facts awaiting confirmation | in-scope entity types; Drake version; bookkeeping-readiness threshold |
| Disposition recommendation | **replace** SOP-018 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 Tax) | Initial adaptation from Atlas SOP-018; operational entity branches; tax requirements externally governed; `needs_review`. |
| 0.2 | 2026-07-18 | Claude (0.12 Stabilization) | Editorial stabilization: standardized externally-governed heading pattern; categorized Related into existing/planned/deferred/external; bidirectional cross-references; no operational change; still `needs_review`. |
