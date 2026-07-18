---
title: "Tax Operations — SOP: IRS & State Notice Handling"
page_id: "TAXOPS-SOP-06"
area: "TAXOPS"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/tax/irs-notice-handling.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "annual"
next_review: "TBD"
related: ["TAXOPS-SOP-04", "TAXOPS-SOP-05", "TAXOPS-SOP-07"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "24084501"
source_title: "SOP-021 - IRS Notice Workflow"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/24084501"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["24084501 (SOP-021)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-021; 14 concerns separated. Notice codes, response periods, appeal rights, mailing requirements, and legal consequences are NOT interpreted here — the notice is preserved exactly and interpretation is escalated to the responsible tax professional. Deadlines are captured from the actual notice, not hard-coded. No live Client360-IRS / Client360-state integration implied."
---

# Tax Operations — IRS & State Notice Handling

## Purpose & scope
Process IRS and state tax notices consistently so that **deadlines, taxpayer identification, response
documentation, and evidence** are controlled — while the **meaning** of the notice is interpreted only
by the responsible tax professional.
**In scope:** any IRS or state tax **notice** received by, or provided to, 360 for a client. **Out of
scope:** original return preparation (`TAXOPS-SOP-02/03`), review/delivery (`TAXOPS-SOP-04`), e-file
acknowledgements (`TAXOPS-SOP-05`), and extensions (`TAXOPS-SOP-07`).

> ⚠️ **CAUTION — no live integration implied.** Notices are handled manually via **TaxDome**,
> **Drake** (server-based), and **Microsoft 365**. This SOP is the **current manual** workflow; it does
> **not** imply any live Client360↔IRS, Client360↔state-authority, or Client360↔Drake integration.

> 🛑 **Do not interpret the notice.** Do **not** infer or explain the meaning of **notice codes**,
> **response periods**, **appeal rights**, **mailing/delivery requirements**, or **legal
> consequences**. **Preserve the notice exactly as received** and escalate interpretation to the
> **responsible tax professional**. Any date used operationally is **captured from the actual notice**
> — do **not** hard-code recurring statutory dates.

## Audience
Client Service (captures, files, tracks, submits); Preparer/Reviewer (analysis, response preparation);
Responsible Tax Professional (interpretation, sign-off, escalation).

## The fourteen concerns (kept explicitly separate)

### 1. Notice receipt & date capture
Obtain the **full** notice (all pages). Record the **date received by the client** and the **date
received by 360** as distinct dates. Capture dates **as printed on the notice / as reported**; do not
assume or compute them.

### 2. Taxpayer & tax-year identification
Identify the **taxpayer** and the **tax year(s)** the notice concerns, and confirm they match the
correct client file before any work proceeds.

### 3. Federal or state authority identification
Identify the **issuing authority** (IRS or the specific **state** authority). Federal and state notices
are tracked **separately**; a client may receive both.

### 4. Response or payment deadline capture
Capture the **response/payment deadline exactly as stated on the notice**. Record it as a tracked
follow-up. `SME CONFIRMATION REQUIRED`: where notice deadlines are tracked (system of record). Do
**not** infer the length of any response period — use the date on the notice.

### 5. Document collection
Save the notice to the **client file** and collect the return, account records, or correspondence
connected to it (by reference — never copy PII or return data into this page).

### 6. Issue analysis & preparer assignment
Assign the notice to a **preparer/reviewer** and connect it to the related return or account issue.
Analysis identifies **what the notice concerns operationally**; it does **not** interpret codes,
appeal rights, or legal consequences — those are escalated (below).

### 7. Response preparation
Prepare the response or payment instruction as directed by the responsible tax professional. `SME
CONFIRMATION REQUIRED`: standard response formats/templates, if any.

### 8. Client approval or signature where required
Where a response requires **client approval or signature**, obtain it before submission. `SME
CONFIRMATION REQUIRED`: which response types require client signature/authorization (externally
governed — not defined here).

### 9. Submission or mailing
Submit the response using the **approved method** (e.g., mail, portal, or authority-specified channel).
`SME CONFIRMATION REQUIRED`: the approved submission/mailing method per authority — **mailing
requirements are externally governed and are not inferred here**.

### 10. Delivery or transmission evidence
Save **proof of submission** (mailing/tracking receipt, portal confirmation, or transmission record) as
evidence, by reference.

### 11. Follow-up & authority correspondence
Keep a **follow-up task open** and track all subsequent authority correspondence until the matter is
resolved. Any new deadline from later correspondence is captured **from that correspondence**.

### 12. Final resolution & evidence retention
Record the **final resolution** and retain the notice, response, submission proof, and resolution
evidence (by reference). `SME CONFIRMATION REQUIRED`: retention **period/rules** (externally
governed — not invented).

### 13. Externally governed response requirements
**No advisory-fee policy is involved.** Notice responses are governed by **external federal and state
tax authorities and applicable professional requirements** — response periods, appeal rights,
mailing/delivery requirements, authorization/signature rules, record-retention, and the legal
consequences of a notice — which are **not defined, summarized, interpreted, or inferred** here.
**Follow the currently effective IRS and applicable state requirements** and the instructions on the
**actual notice**. *(Controlled citations will be added during the Compliance Validation milestone.)*

### 14. Unresolved operational & platform details (controlled placeholders)
Held as visible placeholders — not guessed:
- Where notices and **deadlines** are tracked (system of record). `SME CONFIRMATION REQUIRED`
- Response **templates/formats**, if any. `SME CONFIRMATION REQUIRED`
- Which responses require **client signature/authorization**. `SME CONFIRMATION REQUIRED`
- Approved **submission/mailing method** per authority. `SME CONFIRMATION REQUIRED`
- Retention **period/rules** for notice evidence. `SME CONFIRMATION REQUIRED`

## Expected results
A notice that is captured in full with both receipt dates, matched to the correct taxpayer and tax
year, attributed to the correct authority, with its **stated deadline** tracked, a response prepared and
(where required) client-approved, submitted with retained proof, followed up until resolved, and
retained as evidence — with **interpretation performed only by the responsible tax professional**.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Deadline missed or unclear | Deadline not captured from notice | Capture the date exactly as printed; open a tracked task | Deadline is imminent or passed |
| Staff explains a notice code to the client | Interpretation attempted | Preserve the notice; escalate meaning to the tax professional | Any interpretation was communicated |
| Federal and state matters merged | Authorities not separated | Track IRS and each state notice separately | — |
| No proof of submission | Evidence step skipped | Retain mailing/portal/transmission proof | Response cannot be evidenced |

## Escalation
Escalate all **interpretation** (notice meaning, codes, appeal rights, legal consequences) and any
imminent/passed deadline to the **responsible tax professional / Lead** (Michael Shelton).

## Related
**Existing operational dependencies:** `TAXOPS-SOP-04` — Review & Delivery · `TAXOPS-SOP-05` — E-file Authorization & Acknowledgements · `TAXOPS-SOP-07` — Tax Extensions
**Planned (not yet authored):** CHK-018 (IRS Notice Checklist) · POL-007 (Tax Documentation Policy)
**Deferred:** Controlled citations for governing sources (Form ADV Part 2A / Advisory Agreement, IRS / state requirements) — Compliance Validation milestone.
**External (referenced, not owned):** IRS & applicable state tax authorities; the e-file provider; Drake (server-based) & TaxDome — externally owned/governed.

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-021 - IRS Notice Workflow |
| Source identifier | Confluence `24084501` (CAP-004) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | TaxDome / Drake / M365 — **needs confirmation**; no integration implied |
| Duplication | overlaps CHK-018 (checklist) + POL-007 (policy) — split/linked |
| Contradictions | none identified |
| Facts verified | receive→identify→capture-deadline→respond→evidence→follow-up→resolve flow; deadline-capture and proof-of-submission controls |
| Facts awaiting confirmation | system of record for deadlines; response templates; signature-required responses; submission/mailing method; retention rules (externally governed) |
| Disposition recommendation | **replace** SOP-021 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-18 | Claude (0.12 Tax) | Initial adaptation from Atlas SOP-021; 14-way separation; notice preserved exactly and interpretation escalated (no codes/response-periods/appeal-rights/mailing/legal-consequences inferred); deadlines captured from the actual notice, not hard-coded; tax requirements externally governed; no live integration implied; `needs_review`. |
| 0.2 | 2026-07-18 | Claude (0.12 Stabilization) | Editorial stabilization: standardized externally-governed heading pattern; categorized Related into existing/planned/deferred/external; bidirectional cross-references; no operational change; still `needs_review`. |
