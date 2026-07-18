# Release 0.12.0 — Stabilization Report

**Phase:** 0.12 Stabilization (post first-pass authoring)
**Date:** 2026-07-18
**Author:** Claude (0.12 Stabilization)
**Scope:** Quality/consistency/maintainability review of already-authored content. **No SOPs modified;
no new documentation created** in this pass. Analysis only.
**Branch state:** `release/0.12.0` @ `921c578` (final Tax batch committed). `v0.11.0` immutable.

---

## 1. Documentation inventory

Counts distinguish **authored git-canonical operational content** (substantive, this release) from
**scaffolding/legacy** register rows (planned placeholders + mapped Confluence pages).

| Artifact class | Authored (git-canonical, substantive) | Scaffolded / planned (register rows) | Legacy (non-canonical) |
|---|---|---|---|
| **SOPs** | **18** (8 TAXOPS, 10 WLTH) — all `needs_review` | — | Atlas SOP-* mapped |
| **Checklists** | 0 | CHECKLIST rows planned | CHK-014…020 (Atlas) |
| **Standards** | **1** — `DOCUMENTATION_AUTHORING_STANDARD.md` | — | — |
| **Policies** | 0 | POLICY rows planned | POL-007/009 (Atlas) |
| **Templates** | **3** — Area-Shell template pages (0.11) | — | — |
| **Registers** | **1 canonical** — `pages.yml` (+ generated `DOCUMENTATION_CROSSWALK.md` view) | REGISTER scaffolding | — |
| **Legacy Atlas/360OS** | — | — | **317 rows** (294 `confluence` + 23 `legacy_unresolved`) |

**Register totals (572 rows):** `git` 248 · `confluence` 294 · `legacy_unresolved` 23 · `generated` 7.
**Status:** `planned` 489 · `needs_review` 41 · `draft` 23 · `published` 19.

- Of the **248 git-canonical** rows, **18 are authored operational SOPs**; the remaining ~230 are
  **0.11 governance-tree scaffolding** (`planned` ARCH/CALENDAR/CONTROLS/POLICY/SEC/etc. placeholders),
  not authored content.
- **Substantive operational authoring to date = the 18 SOPs + 1 standard + 3 templates.** Everything
  else is scaffolding or mapped legacy.

---

## 2. Coverage analysis

### Fully documented workflows (operational procedure authored, `needs_review`)
- **Wealth (10):** Schwab account opening, Portfolio Connect billing, MoneyLink, ACAT transfer-in;
  AssetMark account opening, proposal generation, household setup, model selection, funding & transfers,
  billing review.
- **Tax (8):** TaxDome intake, 1040 preparation, business-return preparation, review & delivery, e-file
  authorization & acknowledgements, IRS/state notice handling, extensions, quarterly estimated payments.
- The **Tax return lifecycle (intake → prep → review/delivery → e-file → post-filing: notices,
  extensions, estimates)** is now end-to-end in draft.

### Partially documented workflows
- Every authored SOP is **procedure-complete but SME-unverified** (`needs_review`, `sme_verification:
  partial`, `sme_verified_by: UNCONFIRMED`). No workflow is production-signed.
- Each carries **operational placeholders** (see §3) that are functional gaps until confirmed.

### Operational gaps (workflows not yet authored)
- **Insurance Operations** — blocked by **AD-5**; intentionally not authored (no regulated content).
- **Technology / IT Operations** — ~24 Atlas pages (admin guides, runbooks, BCDR, incident) not adapted.
- **Firm/BizOps** — onboarding, calendar/deadline management, RACI, KPI operational pages not adapted.
- **Tax supporting workflow** — Tax Planning Opportunity (SOP-022) not adapted.

### Missing supporting artifacts (referenced but not authored)
- **Checklists:** CHK-014 (1040), CHK-015 (business), CHK-016 (review), CHK-017 (e-file), CHK-018
  (notice), CHK-019 (extension), CHK-020 (estimates).
- **Policies:** POL-007 (Tax Documentation), POL-009 (E-file Authorization); `WLTH-POL-01`,
  `TAXOPS-POL-01` (referenced in front matter — see §4).
- **Standards:** shared Tax Operations governance standard (deferred).

---

## 3. Consistency review

### Duplicated operational controls (candidates for consolidation)
| Control | Appears in | Consolidation target |
|---|---|---|
| Externally-governed tax-requirements block | all 8 Tax SOPs | shared **Tax Operations governance standard** (deferred) |
| "No live integration implied" caution | 6 SOPs (Tax prep/e-file/notice/extension/estimates) | shared caution snippet |
| Externally-governed advisory-fee/policy block | Wealth billing SOPs (WLTH-02/10) | shared **Wealth billing governance** note |
| Federal + per-state acknowledgement, transmitted ≠ accepted | TAXOPS-05/07 | shared status-ladder pattern |
| Evidence/proof retention **by reference** | TAXOPS-04/05/06/07/08 | shared retention control |
| Follow-up-task-until-resolved | TAXOPS-06/07/08 | shared control |
| Reviewer/advisor approval before client communication | TAXOPS-04/08, WLTH-10 | shared control |

### Inconsistent terminology
- **"Externally governed"** capitalization/heading varies: `## Externally governed — tax requirements`
  (Tax, 4×) vs `## Externally governed — fees & investment suitability` vs `## Billing policy —
  externally governed` vs inline numbered `## 2. Externally governed advisory-fee policy` (Wealth).
  Present as a heading in 11/18; lowercase inline in 14/18.
- **Placeholder section name** varies: `## Operational unknowns (controlled placeholders)` (10×) vs a
  numbered `Unresolved operational & platform details` concern (Tax lifecycle SOPs). Same intent, two
  names.

### Inconsistent document structure
- **Strong shared skeleton** — 7 sections in **all 18**: Purpose & scope, Audience, Troubleshooting,
  Escalation, Related, Source assessment, Revision history.
- **Variances:** `Expected results` in 17/18 (1 missing); `Procedure` vs `The N concerns` vs
  `Operational stages` used for the body depending on SOP type (defensible by type, but not labeled as a
  convention anywhere).

### Repeated placeholder categories
- Recurring `SME CONFIRMATION REQUIRED` themes across SOPs: **system-of-record** (where status/deadlines/
  confirmations are tracked); **approved delivery/submission method**; **platform version/deployment**
  (Drake); **acknowledgement timing**; **retention period/rules**; **approval scope**. These cluster into
  ~6 questions that could be resolved in **one SME session**.

### Opportunities to standardize wording
- One canonical "Externally governed" block per domain (Tax / Wealth-billing), transcluded/referenced.
- One canonical placeholder-section name and one canonical "no live integration" caution.
- A short **structure convention** in the Authoring Standard naming the allowed body patterns.

---

## 4. Dependency analysis

### Cross-reference integrity
- **`related[]` front matter:** 6 references point to **not-yet-authored policies** — `WLTH-POL-01`
  (WLTH-01/02/03/04) and `TAXOPS-POL-01` (TAXOPS-01/02). These are **forward references to planned
  policies**, not broken links, but they will dangle until those policies are authored.
- **In-body cross-references between authored SOPs resolve correctly** (Tax lifecycle chain
  01→02/03→04→05→06/07/08 links are consistent).

### Missing forward/backward references
- **Orphans (no inbound reference from any authored doc):** **TAXOPS-SOP-06** (IRS Notice) and
  **WLTH-SOP-09** (AssetMark funding & transfers). Both link outward but nothing links back — add a
  backward reference from the adjacent lifecycle SOP.

### Duplicate responsibilities
- **Reviewer/lead approval** and **evidence retention** responsibilities are described independently in
  multiple SOPs (see §3 table) — same responsibility, restated; a shared control would remove drift risk.

### Registers
- `pages.yml` canonical; `DOCUMENTATION_CROSSWALK.md` generated view **current**; validator passes
  (unique IDs, valid areas, AD-5 invariant holds, coverage complete). No register integrity issues.

---

## 5. Operational readiness

| Readiness tier | Count / items |
|---|---|
| **Production-ready** | **0** — every SOP is `needs_review`; none SME-signed |
| **SME validation required** | **All 18 SOPs** — confirm placeholders, platform assumptions, methods, systems-of-record |
| **Compliance validation required** | Wealth billing SOPs (WLTH-02/10) + all externally-governed references (Tax + Wealth) — pending the **Controlled Source Authority / Compliance Validation milestone** |
| **Implementation blockers** | Insurance Operations (**AD-5**, hard block); missing supporting checklists/policies; unresolved platform facts (Drake version/deployment; e-file transmission path; systems-of-record) |

- No workflow should be treated as production/authoritative until (a) SME sign-off replaces
  `UNCONFIRMED`, and (b) externally-governed references are backed by controlled citations.

---

## 6. Technical debt register

| ID | Item | Nature |
|---|---|---|
| TD-1 | **Controlled Source Authority milestone** — onboard Form ADV 2A / advisory agreement; SRC-ADV-2A / SRC-IAA; replace external references with controlled citations; compliance sign-off | Deferred architecture |
| TD-2 | **Shared Tax Operations governance standard** — consolidate the externally-governed tax block repeated across 8 Tax SOPs | Future governance standard |
| TD-3 | **Shared Wealth billing governance note** — consolidate the advisory-fee/billing externally-governed blocks | Future governance standard |
| TD-4 | **Terminology/section standardization** — one "externally governed" heading, one placeholder-section name, one integration caution; encode in Authoring Standard | Consistency debt |
| TD-5 | **Supporting artifacts** — CHK-014…020, POL-007/009, WLTH-POL-01, TAXOPS-POL-01 | Coverage debt |
| TD-6 | **Legacy Atlas reconciliation** — 317 non-canonical rows; formal supersede/retire dispositions | Reconciliation debt |
| TD-7 | **Publishing automation** — promotion `needs_review → published`, Confluence sync, DoD as gate (currently advisory) | Future automation |
| TD-8 | **SME validation session** — resolve ~6 clustered placeholder questions across all SOPs | Validation debt |
| TD-9 | **Orphan/forward-reference cleanup** — backward refs for TAXOPS-06/WLTH-09; policy forward refs | Maintainability debt |

---

## 7. Release recommendations

### Critical before production
1. **SME validation of all 18 SOPs** (TD-8) — replace `UNCONFIRMED`; resolve platform assumptions and
   systems-of-record. *No SOP is production-usable without this.*
2. **Compliance validation of externally-governed references** (TD-1) — at minimum for Wealth billing
   (WLTH-02/10) before any billing SOP is treated as authoritative.
3. **AD-5 boundary maintained** — Insurance Operations remains unbuilt/undocumented.

### Recommended before Release 1.0
4. **Terminology & structure standardization** (TD-4) — low-risk, high-consistency; encode conventions.
5. **Shared governance standards** (TD-2, TD-3) — consolidate repeated externally-governed blocks.
6. **Supporting checklists & policies** (TD-5) — complete the referenced artifact set.
7. **Orphan/forward-reference cleanup** (TD-9) — small structural fixes.
8. **Legacy Atlas reconciliation** (TD-6) — formal dispositions for the 317 non-canonical rows.

### Future enhancement
9. **Publishing automation** (TD-7) — promotion workflow, Confluence sync, DoD-as-gate.
10. **Additional operational domains** — Technology/IT Ops, BizOps, Tax Planning (SOP-022).

---

## Appendix — verification basis
- Counts parsed from `docs/registers/pages.yml` (572 rows) and front matter of the 18 authored SOPs.
- Structure/terminology from heading-frequency and body scans across all 18 SOPs.
- Cross-reference/orphan analysis from `related[]` + in-body `page_id` reference graph.
- Register validator + crosswalk `--check`: **current/OK**; DoD `--strict`: **0/0** at `921c578`.
