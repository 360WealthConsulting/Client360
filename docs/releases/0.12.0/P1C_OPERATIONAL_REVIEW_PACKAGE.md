# Release 0.12.0 — Phase P1C Operational SME Review Package

_A concise, decision-ready review of the open questions on the six P1B Client-Platform Operations
documents, for **Michael Shelton** (business/operational reviewer). Every item shows the Atlas source
statement, the current Git wording, the exact confirmation/correction needed, a recommended
evidence-backed answer, allowed responses, and the operational consequence. **This phase prepares the
questions — it does not silently resolve them.** No document changed status; all six remain
`needs_review`; no `SME CONFIRMATION REQUIRED` marker was removed; no Confluence change; no
reconciliation; no AD-5 content; D1–D10 & A1–A4 unchanged; `v0.11.0` immutable._

## How to use this package
Answer each **Section 1** row with one of the **Allowed responses** (choose "Correct to: …" and give the
correction where the recommendation is wrong). Section 2 items can be confirmed later during normal
operations. Section 3 items should stay as visible placeholders until an authority is available. The
**Recommended response** column already applies the **approved known facts** (below) as business-provided
evidence — you are confirming or correcting, not recreating anything from memory.

**Approved known facts incorporated** (from the phase instruction): Schwab = custodial; **Portfolio
Connect = advisory billing** (8-step lock/verify/transmit sequence); **TaxDome** = client portal /
document collection / tax workflow; **Drake** = tax-preparation system, **presently on the office
server**; **Client360 is not yet deployed on that server → no live Client360↔Drake / e-file / IRS-ack /
filing-evidence access**; **AssetMark** = investment-platform workflow source. Screen-/permission-level
details remain to confirm where access is unverified.

---

## Section 1 — Decisions Required Now
_Facts that materially affect whether the SOP is accurate or usable._

| Review ID | Doc | Topic | Atlas source statement | Current proposed wording | Confirmation requested | Recommended response | Allowed responses | Operational effect | Priority | SME-Reg # |
|---|---|---|---|---|---|---|---|---|---|---|
| RV-01 | WLTH-SOP-02 | Billing system of record | SOP-009 "Systems: Schwab Portfolio Connect" | "Open the approved Schwab billing workflow (Portfolio Connect)"; flagged `SME CONFIRMATION REQUIRED` | Is **Portfolio Connect** the current system used to transmit advisory fees? | **Confirm as written** (known fact: Portfolio Connect = advisory billing) | Confirm as written · Correct to: `[system]` · No longer used | Billing performed in the wrong tool | High | 4 |
| RV-02 | WLTH-SOP-02 | Fee-lock & transmit sequence | SOP-009 steps 11–16 (lock → clear filters → verify worksheet → approve → transmit → save) | Procedure steps 11–17 with the LL-001 retained-filter caution | Does the sequence — **select accounts → Actions: lock fees → verify no filters remain → confirm worksheet not hidden → review → transmit (Actions / right-side control) → retain evidence** — remain current? | **Confirm as written.** *Proposed enhancement (flagged, not yet applied):* name the **Actions menu** for locking and the **right-side transmission control**, per the known 8-step sequence | Confirm as written · Confirm with the proposed enhancement · Correct to: `[sequence]` | Fee errors / weak audit trail | **High** | 4 |
| RV-03 | WLTH-SOP-01 | Schwab account-opening platform | SOP-006 "Systems: Charles Schwab Advisor Center" | "Prepare the account-opening workflow in Schwab Advisor Center (or approved process)"; flagged | Is **Schwab Advisor Center** the current account-opening tool? | **Confirm as written** (Schwab = custodial); confirm the specific opening tool name | Confirm as written · Correct to: `[tool]` · Defer pending system access | Staff look in the wrong system | High | 1 |
| RV-04 | WLTH-SOP-03 | AssetMark opening platform | SOP-013 "Systems: AssetMark eWealthManager" | "Prepare the account-opening request in AssetMark (eWealthManager)"; flagged | Is **eWealthManager** the current AssetMark portal/workflow for account opening? | **Confirm as written** (AssetMark = investment-platform workflow); confirm the portal name | Confirm as written · Correct to: `[portal]` · Defer pending system access | Wrong-system guidance | High | 12 |
| RV-05 | WLTH-SOP-04 | AssetMark proposal platform | SOP-011 "Systems: AssetMark eWealthManager" | "Enter proposal assumptions into AssetMark"; flagged | Is AssetMark the current proposal-generation platform/workflow? | **Confirm as written**; confirm the proposal-tool name | Confirm as written · Correct to: `[tool]` · Defer pending system access | Proposal built in the wrong tool | Med | 14 |
| RV-06 | TAXOPS-SOP-02 | Drake install location | SOP-017 "Systems: Drake Tax" | "Preparation is performed in Drake Tax, a **server-based** application"; flagged | Where is **Drake** installed/operated today? | **Confirm as written: Drake is on the office server** (known fact) | Confirm as written · Correct to: `[location]` | Access/prep confusion | **High** | 9 |
| RV-07 | TAXOPS-SOP-02 | Drake server-dependence | SOP-017 (implied) | "server-based application … current manual workflow" | Is Drake **presently server-dependent**? | **Confirm as written** (known fact) | Confirm as written · Correct to: `[detail]` | Misstated access model | High | 9 |
| RV-08 | TAXOPS-SOP-02 | Client360↔Drake / e-file integration | (Atlas implied a manual workflow; no integration stated) | "It does **not** imply any live Client360↔Drake integration or automated e-file connectivity" | Does any **live Client360↔Drake / e-file / IRS-ack / filing-evidence** access currently exist? | **Confirm as written: NO live integration** (known fact: Client360 not yet on the server) | Confirm as written · Correct to: `[if any exists]` | False capability claim if wrong | **High** | 10 |
| RV-09 | TAXOPS-SOP-01 | Intake platform | SOP-016 "Systems: TaxDome" | "Create or verify the TaxDome account …"; flagged | Is **TaxDome** the current intake / document-collection platform? | **Confirm as written** (known fact) | Confirm as written · Correct to: `[platform]` · No longer used | Wrong-system guidance | High | 8 |

## Section 2 — Confirm During Normal Operations
_Details verifiable later without blocking the SOP structure._

| Review ID | Doc | Topic | Atlas source statement | Current proposed wording | Confirmation requested | Recommended response | Allowed responses | Operational effect | Priority | SME-Reg # |
|---|---|---|---|---|---|---|---|---|---|---|
| RV-10 | WLTH-SOP-01 | Opening approval authority | SOP-006 "Compliance confirms documentation" | "Escalate ambiguous registrations to the Lead Advisor"; flagged | Who approves a new registration before submission (role)? | Recommend **Lead Advisor** (M. Shelton) as approver | Confirm as written · Correct to: `[role]` · Defer pending system access | Unclear sign-off gate | Med | 2 |
| RV-11 | WLTH-SOP-01 | MoneyLink / ACAT | SOP-006 "Create follow-up tasks for MoneyLink, ACAT…" | Related/queued: SOP-007 MoneyLink, SOP-008 ACAT | Are MoneyLink & ACAT still current downstream steps? | **Confirm as written** | Confirm as written · No longer used · Correct to: `[detail]` | Wrong follow-up tasks | Med | 3 |
| RV-12 | TAXOPS-SOP-01 | Organizer templates | SOP-016 "Assign the correct organizer/questionnaire" | "Assign the correct organizer, questionnaire, or document request"; flagged | Which organizer/questionnaire templates are current for this tax year? | Confirm during operations | Confirm as written · Correct to: `[templates]` · Defer pending system access | Incomplete intake | Med | 7 |
| RV-13 | TAXOPS-SOP-02 | E-file & acknowledgements | SOP-019/020 (not yet adapted) | Related/queued: SOP-020 E-file & Acknowledgements | Confirm the e-file authorization & ack process for the next batch | Adapt SOP-019/020 next; **no live e-file integration** (RV-08) | Confirm as written · Correct to: `[process]` · Defer pending system access | Incomplete lifecycle | Med | 11 |
| RV-14 | WLTH-SOP-03/04 | Model-approval authority + screens | SOP-011/013 "Lead Advisor approves model" | "documented model approval"; "advisor review before delivery" | Confirm model-approval authority and AssetMark screen/permission labels | Recommend **Lead Advisor** approval; confirm screens later | Confirm as written · Correct to: `[detail]` · Defer pending system access | Model/opening errors | Med | 12, 13 |
| RV-15 | WLTH-SOP-04 | Proposal disclosures | SOP-011 (not explicit) | "confirm any required proposal disclosures"; flagged | Which disclosures are required before proposal delivery? | Confirm during operations (business/operational, not AD-5) | Confirm as written · Correct to: `[disclosures]` · Defer pending system access | Missing disclosure | Med | 14 |
| RV-16 | WLTH-SOP-02/03/04 | Fee-schedule specifics | SOP-009/011/013 "fee schedule" | "review fee rates against signed agreements" (kept generic) | Confirm the fee-schedule reference/source (no rates stored here) | Confirm during operations; **do not store rates in the page** | Confirm as written · Correct to: `[reference]` · Defer pending system access | Fee mismatch | Med | 6, 13, 15 |

## Section 3 — Keep as Controlled Placeholders
_Facts that should stay visibly marked rather than guessed._

| Review ID | Doc | Topic | Atlas source statement | Current proposed wording | Confirmation requested | Recommended response | Allowed responses | Operational effect | Priority | SME-Reg # |
|---|---|---|---|---|---|---|---|---|---|---|
| RV-17 | WLTH-SOP-02 | Quarterly billing dates | SOP-009 "Confirm the billing calendar" (no dates) | "Confirm the billing calendar and billing period" (no dates stated); flagged | Quarterly billing dates/deadlines | **Unknown — retain placeholder** until an authoritative calendar exists | Unknown—retain placeholder · Correct to: `[dates]` | Missed/early billing | Med | 5 |
| RV-18 | TAXOPS-SOP-01 | Minimum-required-document list | SOP-016 "minimum required documents" (unspecified) | "Mark intake ready only when the minimum required documents are received"; flagged | The minimum-required-document list | **Unknown — retain placeholder** until defined | Unknown—retain placeholder · Correct to: `[list]` | Incomplete intake | Med | 7 |
| RV-19 | WLTH-SOP-02 / -01 | Escalation / approval ownership | SOP-006/009 "advisor approval" | "Escalate to the Lead Advisor (Michael Shelton)" | Confirm the standing escalation/approval owner(s) | Recommend M. Shelton (operational); **retain placeholder** where a distinct owner is intended | Confirm as written · Correct to: `[owner]` · Unknown—retain placeholder | Unclear ownership | Low | 2, 6 |
| RV-20 | all six | Retention / SLA periods | (none stated in sources) | (no retention/SLA periods asserted) | Any evidence-retention or service-level periods to state | **Unknown — retain placeholder**; do not invent periods | Unknown—retain placeholder · Correct to: `[periods]` | Undefined retention/SLA | Low | (new, derived) |

---

## Corrections made from approved known facts

**None.** The six SOPs were checked against the approved known facts and contain **no clear factual
defect**: none claims live Client360↔Drake/e-file integration (they explicitly deny it), Drake is
described as server-based, Portfolio Connect is named for billing, and the fee-lock/transmit control
matches the known sequence. Therefore **no SOP was modified** in P1C; all `SME CONFIRMATION REQUIRED`
markers remain in place pending Michael's answers. The known facts are surfaced here as **recommended
responses**, not silently applied to the documents.

## Validation

| Check | Result |
|---|---|
| All 15 existing SME questions represented | ✅ mapped to RV-01…RV-20 (SME-Reg # column); RV-20 is a derived retention/SLA placeholder |
| Duplicates consolidated with traceability | ✅ e.g. fee-schedule (SME #6/13/15) → RV-16; SME-Reg # preserved on every row |
| Each material question includes source statement + proposed wording | ✅ (Section 1 especially) |
| Known business facts incorporated | ✅ Portfolio Connect, Drake-on-server, no integration, TaxDome, AssetMark, canonical platforms |
| No unresolved fact silently treated as confirmed | ✅ recommendations shown as *proposed*; markers retained; nothing applied to SOPs |
| No AD-5 content | ✅ none (all business/operational; disclosures are operational, not AD-5) |
| No client PII / secrets | ✅ none; fee rates deliberately not stored |
| No Confluence changes | ✅ read-only |
| No legacy reconciliation | ✅ none |
| All six SOPs remain `needs_review` | ✅ verified |
| D1–D10 & A1–A4 unchanged; `v0.11.0` immutable | ✅ |

## Readiness recommendation

**After Michael answers the Section 1 (RV-01…RV-09) decisions, five to six of the six documents will be
ready for P3 quality review.** The Section 1 items are the only ones that materially affect accuracy/
usability, and most already have strong recommended answers from the approved known facts (Portfolio
Connect billing, Drake-on-server, no live integration, TaxDome). Section 2 items can be confirmed during
normal operations without blocking; Section 3 items stay as controlled placeholders. Once Section 1 is
confirmed (and any "Correct to:" edits applied in the next phase), the six SOPs can move toward
`published` via P3 — **not** during P1C.

---

**Stopping after this Operational SME Review Package.** Awaiting Michael's answers before applying broad
SME-driven corrections, beginning legacy reconciliation, modifying Confluence, or starting the next
authoring batch.
