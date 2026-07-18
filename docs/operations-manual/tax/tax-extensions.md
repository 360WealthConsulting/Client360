---
title: "Tax Operations — SOP: Tax Extensions"
page_id: "TAXOPS-SOP-07"
area: "TAXOPS"
profile: "hybrid"
doc_type: "SOP"
canonical_source: "git"
git_source: "docs/operations-manual/tax/tax-extensions.md"
confluence_page_id: "TBD"
owner: "Michael Shelton (business owner)"
reviewer: "Michael Shelton (business/operational reviewer)"
status: "needs_review"
effective_or_release: "v0.12.0 (draft)"
last_reviewed: "TBD"
review_cycle: "annual"
next_review: "TBD"
related: ["TAXOPS-SOP-02", "TAXOPS-SOP-03", "TAXOPS-SOP-05", "TAXOPS-SOP-08"]
compliance_gate: "none"
source_system: "360os_atlas"
source_page_id: "23789643"
source_title: "SOP-023 - Extension Workflow"
source_link: "https://360wealthconsulting.atlassian.net/wiki/spaces/3WCO/pages/23789643"
source_status: "current (Atlas draft v0.1, 2026-07-09)"
supersedes: ["23789643 (SOP-023)"]
sme_verification: "partial"
sme_verified_by: "UNCONFIRMED"
provenance_notes: "Adapted from Atlas SOP-023; 12 concerns separated. This SOP does NOT state that an extension extends the time to pay — that relationship is externally governed and left to the responsible tax professional / controlled sources. Federal and each state acknowledgement tracked separately; transmitted != accepted. Deadlines captured from authoritative sources, not hard-coded. No live Client360-Drake / Client360-IRS / Client360-state integration implied."
---

# Tax Operations — Tax Extensions

## Purpose & scope
File and document tax extensions consistently, tracking each **acknowledgement** and each **payment
instruction** so an extension is not assumed accepted on transmission and payment obligations are not
mishandled.
**In scope:** individual and business return **extensions**. **Out of scope:** return preparation
(`TAXOPS-SOP-02/03`), e-file acknowledgements for the return itself (`TAXOPS-SOP-05`), and quarterly
estimated payments (`TAXOPS-SOP-08`).

> ⚠️ **CAUTION — no live integration implied.** Extensions are prepared/transmitted via **Drake**
> (server-based) and **TaxDome**. This SOP is the **current manual** workflow; it does **not** imply any
> live Client360↔Drake, Client360↔IRS, or Client360↔state integration.

> 🛑 **Do not assert the pay-vs-file relationship.** This SOP does **not** state that an extension of
> time to **file** does or does not extend the time to **pay**. That relationship is **externally
> governed**; state it to a client only when supported by a **controlled governing source** or the
> **responsible tax professional**. Any deadline used operationally is **captured from an authoritative
> source** — do **not** hard-code recurring statutory dates.

## Audience
Preparer (prepares/transmits the extension); Reviewer/Advisor (decision, estimate review); Client
Service (client approval, payment instructions, follow-up).

## The twelve concerns (kept explicitly separate)

### 1. Extension eligibility or decision
Identify the return requiring an extension and record the **client approval or firm decision** to
extend. `SME CONFIRMATION REQUIRED`: any operational eligibility criteria (externally governed rules
are not defined here).

### 2. Information available vs missing
Record what return information is **available** versus **missing**, since this determines whether a
liability estimate can be made and what the client must still provide.

### 3. Estimated tax-liability calculation workflow
Where information is available, run the **estimate workflow** to produce a liability figure to inform
any payment. The **calculation rules, safe-harbor thresholds, and payment amounts are externally
governed** (below) and are **not** defined here; unavailable-information cases are flagged, not guessed.

### 4. Client approval
Obtain the client's **approval** of the extension (and of any recommended payment) before transmission.

### 5. Extension transmission
Prepare and **transmit** the extension via the approved Drake/e-file process by the applicable
deadline. Record the transmission. `SME CONFIRMATION REQUIRED`: the current transmission steps/tool.

### 6. Federal acknowledgement
Monitor for and record the **federal acknowledgement** (accepted or rejected). **Transmitted is not
accepted** — do not treat a transmitted extension as accepted until acknowledged. `SME CONFIRMATION
REQUIRED`: expected acknowledgement timing (externally governed — not invented).

### 7. State acknowledgement(s)
For **each applicable state**, monitor and record the **state acknowledgement** (accepted or rejected)
**separately** from the federal result and from any other state.

### 8. Payment instructions & payment evidence
Provide the client the **payment instructions** operationally and retain **payment evidence** when a
payment is made (by reference). The **amount, method, and deadline are externally governed** (below).
`SME CONFIRMATION REQUIRED`: approved payment methods/channels.

### 9. Filing-status tracking
Track the extension's operational status: decision → transmitted → federal accepted/rejected → state
accepted/rejected (per state) → correction (if rejected) → acknowledged. `SME CONFIRMATION REQUIRED`:
where extension status is tracked (system of record).

### 10. Final-return follow-up
Create a **post-extension preparation task** so the extended return is completed and filed within the
extended period. The extended-period deadline is **captured from an authoritative source**, not
hard-coded.

### 11. Externally governed extension & payment rules
**No advisory-fee policy is involved.** Extensions are governed by **external federal and state tax
authorities and applicable professional requirements** — extension eligibility and deadlines, the
relationship between an extension to file and the obligation to pay, payment rules and deadlines,
e-file/acknowledgement rules, preparer obligations, and record-retention — which are **not defined,
summarized, or inferred** here. **Follow the currently effective IRS and applicable state
requirements** and the **currently effective e-file provider and taxing-authority requirements**.
*(Controlled citations will be added during the Compliance Validation milestone.)*

### 12. Unresolved operational & platform details (controlled placeholders)
Held as visible placeholders — not guessed:
- Current **transmission** steps/tool (Drake/e-file). `SME CONFIRMATION REQUIRED`
- **Acknowledgement timing** (federal + state). `SME CONFIRMATION REQUIRED`
- Approved **payment methods/channels**. `SME CONFIRMATION REQUIRED`
- Where **extension status** is tracked (system of record). `SME CONFIRMATION REQUIRED`
- Any operational **eligibility criteria**. `SME CONFIRMATION REQUIRED`

## Expected results
An extension that is decided/approved, transmitted by the applicable deadline, acknowledged (federal +
each applicable state) rather than assumed accepted, with payment instructions provided and evidence
retained where a payment is made, and a follow-up task ensuring the extended return is completed.

## Troubleshooting
| Symptom | Likely cause | Resolution | Escalate if |
|---|---|---|---|
| Extension assumed accepted on transmission | Acknowledgement not tracked | Wait for federal + state acknowledgement | Acknowledgement not received in expected time |
| Client told extension "extends time to pay" | Unsupported assertion | Do not assert; escalate to tax professional / controlled source | Any such statement was made |
| Federal accepted, state missed | States tracked together | Track each state separately | Repeated state rejection |
| Extended return not completed | No follow-up task | Create post-extension preparation task | Extended deadline at risk |

## Escalation
Escalate the pay-vs-file question, unresolved rejections, and at-risk extended deadlines to the
**responsible tax professional / Lead** (Michael Shelton).

## Related
- `TAXOPS-SOP-02` — 1040 Preparation · `TAXOPS-SOP-03` — Business Return Preparation
- `TAXOPS-SOP-05` — E-file Authorization & Acknowledgements · `TAXOPS-SOP-08` — Quarterly Estimated Payments
- Queued: Extension Checklist (CHK-019), Tax Documentation Policy (POL-007)

## Source assessment
| Field | Content |
|---|---|
| Source page | SOP-023 - Extension Workflow |
| Source identifier | Confluence `23789643` (CAP-004) |
| Source status | current (Atlas draft v0.1) |
| Suspected age | authored 2026-07-09 |
| Current-system applicability | TaxDome / Drake — **needs confirmation**; no integration implied |
| Duplication | overlaps CHK-019 (checklist) + POL-007 (policy) — split/linked |
| Contradictions | source purpose asserts "extension to file is not an extension to pay"; **not reproduced** as an assertion here — treated as externally governed pending a controlled source |
| Facts verified | decide→estimate→approve→transmit→acknowledge→pay-instruct→follow-up flow; acknowledgement and payment-evidence controls |
| Facts awaiting confirmation | transmission steps; acknowledgement timing; payment methods; status system of record; eligibility criteria (externally governed) |
| Disposition recommendation | **replace** SOP-023 after quality review |

## Revision history
| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-18 | Claude (0.12 Tax) | Initial adaptation from Atlas SOP-023; 12-way separation; federal + per-state acknowledgements tracked separately (transmitted ≠ accepted); pay-vs-file relationship NOT asserted (externally governed / controlled source); deadlines captured from authoritative sources, not hard-coded; no live integration implied; `needs_review`. |
