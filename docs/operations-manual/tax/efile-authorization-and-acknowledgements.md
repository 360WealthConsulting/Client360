---
title: "Tax Operations — SOP: E-file Authorization & Acknowledgements"
page_id: "TAXOPS-SOP-05"
area: "TAXOPS"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/tax/efile-authorization-and-acknowledgements.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "annual"
next_review: "TBD"
related: ["TAXOPS-SOP-02", "TAXOPS-SOP-03", "TAXOPS-SOP-04", "TAXOPS-SOP-06", "TAXOPS-SOP-07"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "24182792"
source_title: "SOP-020 - E-file Authorization Tracking"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/24182792"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["24182792 (SOP-020)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-020; transmitted != accepted; acknowledgement timing/forms/retention/deadlines/rejection codes/correction rules are externally governed and NOT invented here. No live Client360-Drake / Client360-e-file integration implied."
---

# Tax Operations — E-file Authorization & Acknowledgements

## Purpose & scope
Track e-file **authorization** and **acknowledgements** so that (a) returns are not filed before client
approval, (b) approved returns are not left unfiled, and (c) **"transmitted" is never treated as
"accepted."**
**In scope:** returns requiring signed e-file authorization, from authorization collection through
retained filing evidence. **Out of scope:** preparation and review/delivery (`TAXOPS-SOP-02/03/04`).

> ⚠️ **CAUTION — no live integration implied.** E-filing is performed via **Drake** (server-based) and
> the applicable e-file provider/taxing authorities. This SOP is the **current manual** tracking
> workflow; it does **not** imply any live Client360↔Drake or Client360↔e-file integration.

## Operational status model (a return moves through these — do not conflate them)
1. **Ready for transmission** — authorized, readiness-reviewed, not yet sent.
2. **Transmitted** — submitted to the e-file provider. **This is NOT acceptance.**
3. **IRS accepted** *or* **IRS rejected**.
4. **State accepted** *or* **State rejected** — tracked **per applicable state**, independently of IRS.
5. **Correction pending** — after any rejection, until corrected and re-transmitted.
6. **Completed** — all applicable federal and state acceptances received **and** filing evidence retained.

> A return is **not "filed/complete" at "Transmitted."** Completion requires the applicable
> **acceptances** and retained evidence.

## The nine concerns (kept explicitly separate)

### 1. Authorization collection
Confirm the final return was delivered (`TAXOPS-SOP-04`). Send the required e-file **authorization
form(s)**; create a follow-up task for any unsigned authorization; confirm the signed authorization is
complete; save it to the client file (by reference). **Do not release for filing before authorization.**
`SME CONFIRMATION REQUIRED`: the specific authorization form(s) required (externally governed — not
named here).

### 2. Readiness review before transmission
Confirm the return is review-complete, authorization is signed, and no open diagnostics remain, so the
status is **Ready for transmission**.

### 3. Transmission procedure
Release the return for filing and **transmit** via the approved Drake/e-file-provider process; record
the transmission and set status **Transmitted**. `SME CONFIRMATION REQUIRED`: the current transmission
steps/tool.

### 4. IRS acknowledgement
Monitor for the **IRS acknowledgement**; record **IRS accepted** or **IRS rejected**. Do **not** mark
the return filed on transmission alone. `SME CONFIRMATION REQUIRED`: expected acknowledgement timing
(externally governed — not invented).

### 5. State acknowledgement(s)
For **each applicable state**, monitor and record **State accepted** or **State rejected**
**separately** from the IRS result (a return can be IRS-accepted and state-rejected, or vice versa).

### 6. Rejection & correction handling
On any IRS or state **rejection**, set **Correction pending**; document the rejection and the corrective
action; correct and **re-transmit**; re-track acknowledgements. `SME CONFIRMATION REQUIRED`: rejection
**codes** and **correction rules** (externally governed — not defined here).

### 7. Filing-history & evidence retention
Retain the signed authorization, the transmission record, and the **acceptance confirmation(s)** (IRS +
each state) as filing evidence (by reference). `SME CONFIRMATION REQUIRED`: retention **period/rules**
(externally governed — not invented).

### 8. Externally governed filing requirements
**No advisory-fee policy is involved.** E-filing is governed by **external federal and state tax
authorities and the e-file provider** — authorization/electronic-signature rules, IRS and state e-file
rules, acknowledgement handling, filing/payment **deadlines**, rejection/correction rules, preparer
obligations, and record-retention — which are **not defined, summarized, or inferred** here. **Follow
the currently effective IRS and applicable state requirements** and the **currently effective e-file
provider and taxing-authority requirements**. *(Controlled citations will be added during the Compliance
Validation milestone.)*

### 9. Unresolved operational & platform details (controlled placeholders)
Held as visible placeholders — not guessed:
- Required **authorization form(s)**. `SME CONFIRMATION REQUIRED`
- Current **transmission** steps/tool (Drake/e-file provider). `SME CONFIRMATION REQUIRED`
- **Acknowledgement timing** (IRS + state). `SME CONFIRMATION REQUIRED`
- **Rejection codes / correction rules**. `SME CONFIRMATION REQUIRED`
- **Retention period/rules** for filing evidence. `SME CONFIRMATION REQUIRED`
- Where **filing status/history** is tracked. `SME CONFIRMATION REQUIRED`

## Audience
Client Service / Preparer (collects authorization, transmits, tracks status); Reviewer/Lead (resolves
rejections).

## Expected results
A return that is authorized before filing, transmitted, **acknowledged** (IRS + each applicable state),
with any rejection corrected and re-transmitted, and **completed** only when all applicable acceptances
are received and filing evidence is retained.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Return filed before authorization | Auth step skipped | Do not release before signed authorization | Any unauthorized filing |
| "Transmitted" treated as done | Status confusion | Wait for IRS + state acceptance; then complete | Acceptance not received in expected time |
| IRS accepted but state rejected | States tracked together | Track each state separately; correct the state filing | Repeated state rejection |

## Escalation
Escalate unauthorized-filing risks and unresolved rejections to the **Reviewer / Lead** (Michael Shelton).

## Related
**Existing operational dependencies:** `TAXOPS-SOP-02` — 1040 Preparation · `TAXOPS-SOP-03` — Business Return Preparation · `TAXOPS-SOP-04` — Review & Delivery · `TAXOPS-SOP-06` — IRS & State Notice Handling · `TAXOPS-SOP-07` — Tax Extensions
**Planned (not yet authored):** CHK-017 (E-file Authorization Checklist) · POL-009 (E-file Authorization Policy)
**Deferred:** Controlled citations for governing sources (Form ADV Part 2A / Advisory Agreement, IRS / state requirements) — Compliance Validation milestone.
**External (referenced, not owned):** IRS & applicable state tax authorities; the e-file provider; Drake (server-based) & TaxDome — externally owned/governed.

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-020 - E-file Authorization Tracking |
| Source identifier | Confluence `24182792` (CAP-004) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | Drake / TaxDome / e-file provider — **needs confirmation**; no integration implied |
| Duplication | overlaps CHK-017 (checklist) + POL-009 (policy) — split/linked |
| Contradictions | none identified |
| Facts verified | authorization-before-filing control; transmitted-≠-accepted; rejection-tracking |
| Facts awaiting confirmation | authorization forms; transmission steps; ack timing; rejection codes/correction rules; retention rules (all externally governed) |
| Disposition recommendation | **replace** SOP-020 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-17 | Claude (0.12 Tax) | Initial adaptation from Atlas SOP-020; 9-way separation + operational status ladder (transmitted ≠ accepted; per-state acks); no invented forms/timing/codes/retention; tax requirements externally governed; `needs_review`. |
| 0.2 | 2026-07-18 | Claude (0.12 Stabilization) | Editorial stabilization: standardized externally-governed heading pattern; categorized Related into existing/planned/deferred/external; bidirectional cross-references; no operational change; still `needs_review`. |
