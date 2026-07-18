# Release 0.12.0 — P1D Operational Validation Register

_Tracks operational validation of the Client-Platform SOPs and records the approved **external-
governance** decision. Branch `release/0.12.0`, 2026-07-17. No Confluence changes; no legacy pages
reconciled/archived; nothing published (all SOPs remain `needs_review`); no app/migration; no AD-5
content; no controlled-source placeholder records created. D1–D10 & A1–A4 unchanged; `v0.11.0`
immutable._

## Decision — Controlled Source Authority deferred; billing policy externally governed (approved)

- **Release 0.12 does not block on governing-document intake.** Client-facing billing **policy** is
  treated as **externally governed**: where a determination depends on the Form ADV Part 2A or the
  advisory agreement, the SOP inserts a **clearly identified external reference** ("See current Form
  ADV Part 2A." / "See current Advisory Agreement.") and **does not infer, restate, or create firm
  policy**. Workflow steps that depend on a governing document note that the operational procedure
  **must comply with the currently effective governing document without reproducing its regulatory
  language**.
- **Controlled Source Authority implementation is a future Compliance Validation milestone** (below).
  Per approval, **no placeholder controlled-source records were created.**

## Compliance Validation milestone (future — recorded, not started)

When reached, the project will:
1. **Onboard the governing documents** (current filed Form ADV Part 2A; advisory agreement master
   template) into the secure store.
2. **Establish the Controlled Source Register** (`docs/registers/controlled-sources.yml`) + the
   Controlled Source Authority Standard, per the approved architecture.
3. **Replace the temporary external references** in the SOPs with **controlled citations** (the
   controlled-source records + exact section/clause), once those records are established in this
   milestone.
4. **Validate all billing-related SOPs against the governing documents.**
5. **Complete compliance sign-off before final publication.** (Compliance validation of regulated
   content is a **CCO/compliance** function — distinct from AD-5, which separately gates regulated
   *insurance* rule sets and remains UNFILLED; Michael Shelton's role is business/operational only,
   not regulatory certification.)

## Per-workflow operational-validation status

| # | Workflow | Doc | Interview status | Policy treatment | Operational facts confirmed | Corrections applied | Unresolved (operational) | Source conflicts | Evidence requirements | Ready to finalize SOP? |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Schwab Portfolio Connect billing | `WLTH-SOP-02` | Reframed (external-governance applied); operational questions open | **Externally governed** (ADV 2A / Agreement refs); 8-step control retained | Confirmed 8-step lock/verify/transmit control; Portfolio Connect = billing (business-known) | v0.2: external-governance block + steps 6–8 reworded + source assessment split | Billing platform confirmation; billing-calendar dates; who prepares/reviews & approval authority; pre-lock checks; exception criteria; evidence retained/stored/naming; transmission success/failure handling; corrections process; quarter-complete definition (held as **controlled placeholders**) | none | Retain transmission confirmation + final worksheet + advisor-approval record (storage location = controlled placeholder) | **No** — `needs_review`; policy externally governed; operational placeholders pending |
| 2 | Schwab account opening | `WLTH-SOP-01` | **Operationally validated (doc-level)** | **Externally governed** note | Procedural flow (Atlas SOP-006); separation of concerns confirmed | v0.2: external-governance note + **operational-unknowns (controlled placeholders)** section | Current opening platform; approval authority; MoneyLink/ACAT currency | none | Account-opening confirmation (by reference; storage TBD) | Doc-level yes; `needs_review` (operational placeholders + Compliance Validation pending) |
| 3 | AssetMark account opening | `WLTH-SOP-03` | Policy reframed; operational validation pending | **Externally governed** note added | Procedural flow (from Atlas SOP-013) | External-governance note added | Current AssetMark platform/workflow; model-approval authority | none | Account confirmation (by reference) | No — `needs_review` |
| 4 | AssetMark proposal generation | `WLTH-SOP-04` | Policy reframed; operational validation pending | **Externally governed** note added | Procedural flow (from Atlas SOP-011) | External-governance note added | Current proposal workflow; required disclosures (externally governed) | none | Proposal saved + advisor review (by reference) | No — `needs_review` |
| 5 | TaxDome client intake | `TAXOPS-SOP-01` | Operational validation pending | n/a (no advisory-fee policy) | Intake flow (from Atlas SOP-016) | none | Current organizer templates; minimum-document list; TaxDome currency | none | Uploaded docs visible; missing-item log | No — `needs_review` |
| 6 | Drake 1040 preparation | `TAXOPS-SOP-02` | Operational validation pending | n/a (server-based; no live integration — stated) | Prep flow (from Atlas SOP-017); Drake on office server (business-known) | none | Drake deployment/version; e-file/ack process (SOP-020, queued) | none | Prior-year comparison; reviewer notes; planning notes | No — `needs_review` |

## Process refinements (approved)

- **No future controlled-source IDs inside operational SOPs.** Removed `SRC-ADV-2A` / `SRC-IAA` from
  `WLTH-SOP-02`; SOPs now say "Controlled citations will be added during the Compliance Validation
  milestone." (Those records do not yet exist.)
- **Approval gate for all future 0.12 documentation changes:** implement → validate → present the
  complete diff + validation summary → **wait for approval** → then commit. (This update is presented
  under that gate — not yet committed.)

## Applied changes this phase (P1D)

- `WLTH-SOP-02` → v0.2: added **"Billing policy — externally governed"** section; reworded procedure
  steps 6–8 to reference the governing documents; split the source assessment into **operational
  (controlled placeholder)** vs **policy (externally governed)**; kept the confirmed 8-step control.
- `WLTH-SOP-01 / -03 / -04` → added a short **externally-governed** note for advisory-agreement / fee-
  schedule / disclosure policy (operational procedure only).
- `TAXOPS-SOP-01 / -02` → unchanged (no advisory-fee policy dependency).
- **No controlled-source records created** (deferred). All six SOPs remain **`needs_review`**.

## Readiness

- **Policy** questions are resolved for 0.12 (externally governed — closed by reference, not by memory
  or invention).
- **Operational** finalization still depends on either Michael's operational answers or acceptance of
  the **controlled placeholders**; publication and controlled-citation replacement occur in the
  **Compliance Validation milestone / P3 quality review** — **not** in P1D.

---

**Operational documentation continues** (no blocking on regulatory-document intake). Awaiting your
direction on the next workflow's operational validation (or operational answers to close the Schwab
billing placeholders). No publication, no Confluence change, no reconciliation, no controlled-source
records until the Compliance Validation milestone is scheduled and approved.
