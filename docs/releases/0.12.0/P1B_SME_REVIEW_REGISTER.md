# Release 0.12.0 — P1B SME Review Register

_Consolidated open questions for SME confirmation across the P1B (Client-Platform Operations) authored
pages. Every listed page remains `needs_review` until its items are resolved. **Michael Shelton** may
serve as the **business/operational** reviewer where noted — this is operational confirmation, **not**
regulatory certification (no AD-5 subjects appear here). Nothing was invented; unverified facts are
marked `SME CONFIRMATION REQUIRED` inline in each page._

| # | Doc ID | Title | Source page | Fact requiring confirmation | Why | Recommended reviewer | Operational effect if unresolved | Priority |
|---|---|---|---|---|---|---|---|---|
| 1 | WLTH-SOP-01 | Schwab Account Opening | SOP-006 (`24772609`) | Is **Schwab Advisor Center** the current account-opening system? | Platform names drift; must not present a stale tool as current | Ops SME (M. Shelton) | Staff may look in the wrong system | High |
| 2 | WLTH-SOP-01 | Schwab Account Opening | SOP-006 | Approval/compliance authority for new registrations | Governs sign-off before submission | Ops/Compliance (M. Shelton, business) | Unclear approval gate | Med |
| 3 | WLTH-SOP-01 | Schwab Account Opening | SOP-006 | Do **MoneyLink / ACAT** remain current downstream steps? | Related tasks reference them | Ops SME | Follow-up tasks may be wrong | Med |
| 4 | WLTH-SOP-02 | Schwab Portfolio Connect Billing | SOP-009 (`24870913`) | Is **Portfolio Connect** the current fee-transmission system of record? | Central to the billing control | Ops SME (M. Shelton) | Billing done in the wrong tool | **High** |
| 5 | WLTH-SOP-02 | Schwab Portfolio Connect Billing | SOP-009 | Billing-**calendar dates** for each quarter | Timing not in source; must not invent | Ops SME | Missed/early billing | High |
| 6 | WLTH-SOP-02 | Schwab Portfolio Connect Billing | SOP-009 | Fee-schedule specifics & approval/compliance authority for transmission | Accuracy + audit trail | Ops/Compliance (M. Shelton, business) | Fee errors; weak audit trail | High |
| 7 | TAXOPS-SOP-01 | TaxDome Client Intake | SOP-016 (`23920691`) | Current **organizer/questionnaire templates** & minimum-required-document list | Year-specific; not in source | Tax SME (M. Shelton) | Incomplete intake | High |
| 8 | TAXOPS-SOP-01 | TaxDome Client Intake | SOP-016 | Is **TaxDome** the current intake platform? | Confirm current tooling | Tax SME | Wrong-system guidance | Med |
| 9 | TAXOPS-SOP-02 | 1040 Preparation (Drake) | SOP-017 (`23920712`) | **Drake** current deployment (server/workstation) & version | Server-dependent; drives access model | Tax/IT SME (M. Shelton) | Access/prep confusion | **High** |
| 10 | TAXOPS-SOP-02 | 1040 Preparation (Drake) | SOP-017 | Is there any **live Client360↔Drake / e-file integration** in production? | Must **not** imply connectivity that doesn't exist | Tax/IT SME | False capability claim | **High** |
| 11 | TAXOPS-SOP-02 | 1040 Preparation (Drake) | SOP-017 | E-file authorization & acknowledgement process (SOP-019/020, not yet adapted) | Downstream procedure | Tax SME | Incomplete lifecycle | Med |
| 12 | WLTH-SOP-03 | AssetMark Account Opening | SOP-013 (`24838166`) | Current **AssetMark platform** name/portal ("eWealthManager"?) & opening workflow | Platform naming may be stale | Ops SME (M. Shelton) | Wrong-system guidance | High |
| 13 | WLTH-SOP-03 | AssetMark Account Opening | SOP-013 | Fee-schedule specifics & model-approval authority | Accuracy of opening | Ops SME | Fee/model errors | Med |
| 14 | WLTH-SOP-04 | AssetMark Proposal Generation | SOP-011 (`25133057`) | Current proposal workflow & **required disclosures** | Compliance-sensitive delivery | Ops/Compliance (M. Shelton, business) | Missing disclosure | High |
| 15 | WLTH-SOP-04 | AssetMark Proposal Generation | SOP-011 | Fee-assumption specifics | Proposal accuracy | Ops SME | Incorrect proposal | Med |

## Notes
- **No AD-5 subjects** appear in this batch. Regulated insurance rule sets (suitability, replacement/
  1035, licensing, CE) are **not** in scope and remain gated/unpublished.
- Michael Shelton's confirmations here are **business/operational** only — not regulatory certification.
- Resolution flow: SME confirms → the page's `SME CONFIRMATION REQUIRED` items are cleared and
  `sme_verification` set to `verified` → the page can progress toward `published` (P3 quality review),
  **not** during P1B.
