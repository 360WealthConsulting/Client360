# Release 0.11.0 — 0.11-P3 Publication Register Report

_Phase **0.11-P3 — Publication Register Promotion**. Branch `release/0.11.0`. Executed 2026-07-17.
Repository-only: **no Confluence change**, no legacy-page mutation, no app code/migrations, no
0.10.0 artifact/tag change, no P4 work, no blocking CI._

> Under all conditions of `P3_READINESS_CHECK.md`: legacy pages seeded as non-canonical
> manual-review rows with no Confluence movement (C1); the D10 checklist was extended to the 23
> legacy pages (C2); the D9 AD-5 invariant is enforced programmatically; the compliance reviewer
> remains `UNFILLED`.

## 1. Schema adopted

Canonical register `docs/registers/pages.yml` (decision D1). Each row carries the full field set:
`page_id, title, area, node, profile, doc_type, canonical_source, repository_path,
confluence_page_id, confluence_parent_id, owner, reviewer, status, last_reviewed, review_cycle,
next_review, compliance_gate, legacy_identifier, legacy_source, reconciliation_status, notes`. The
file embeds `meta`, `enums`, `schema`, and a `taxonomy_migration_d10` block. `null` is used only for
genuinely-not-applicable fields (per the schema's `null_allowed`); unresolved-but-expected values use
visible `TBD` / `UNFILLED`.

**Status enum (enforced):** `planned | draft | published | needs_review` — any other value is
rejected. **Canonical-source enum (enforced):** `git` (Git-canonical), `confluence`
(Confluence-canonical), `generated` (generated views), `legacy_unresolved` (unresolved legacy — never
canonical).

**Scope decision (documented):** "required document types per area profile" = each profile's
**minimum-viable ("documented") set** (framework `02-DOCUMENT-TYPE-TEMPLATES.md` §D). This yields a
meaningful coverage register rather than ~450 empty matrix cells; the full doc-type matrix expands
later under the Definition of Done. Hybrid areas seed the **union** of the Software min-set and the
Business-Ops process types Policy/SOP/RACI/Checklist/Calendar (D3), de-duplicated.

## 2. Counts

- **Total register rows: 314.**
- **By status:** `planned` 249 · `draft` 23 · `needs_review` 23 (all legacy) · `published` 19.
- **By canonical source:** `git` 147 · `confluence` 137 · `generated` 7 · `legacy_unresolved` 23.
- **By compliance gate:** `AD-5` 10 · `none` 304.
- **By node:** 00→4 · 01→5 · 10→169 · 20→45 · 30→51 · 40→9 · 80→11 · 90→20.
- **By profile:** hybrid 162 · operations 101 · infrastructure 42 · library 9.
- **By area (29 total = 26 framework + SHARED + GOV + MANUAL):** e.g. INS 29, GOV 21, WLTH 15, CRM 14,
  DOC 14, RPT 14, TAXOPS 14, MANUAL 13, CLM360 13, RET 13, AIA 13, WORK 13, CMP 10, BEN 16, node-20
  areas 7–8, node-30 areas 8–10, SHARED 8, SOPLIB 4, TRAIN 3, RELMGMT 3.
- **By document type (29 types):** EXEC 27, SOP 25, LEGACY 23, ARCH 20, PURPOSE 20, CALENDAR 19,
  CHECKLIST 18, POLICY 18, RACI 18, SEC 18, CHANGELOG 17, USERGUIDE 13, DATA 11, RELNOTES 11, NODE 8,
  REGISTER 8, ASSET 7, BCDR 7, RUNBOOK 7, RULES 4, PROCESS 3, TEMPLATE 3, CONTROLS 2, META 2, and
  EXC/GLOSSARY/INTEG/REPORT/WF 1 each.

## 3. Framework-area coverage

All **26 framework areas** plus `SHARED` and `GOV` are represented (validator-enforced). Node 10 (11
hybrid areas), node 20 (6 infra), node 30 (6 ops), node 80 (3 library), node 40 (`SHARED`
singletons), node 90 (`GOV`). `MANUAL` is the structural pseudo-area for the manual's own
nodes/templates.

## 4. Hybrid union treatment

The 11 node-10 areas use `profile: hybrid` = Software min-set (EXEC, PURPOSE, ARCH, DATA, USERGUIDE,
SEC, RELNOTES, CHANGELOG) **∪** Business-Ops process types (POLICY, RACI, SOP, CHECKLIST, CALENDAR) —
13 de-duplicated types (SOP appears once). The validator confirms **no duplicate profile-union
coverage rows** per area.

## 5. Governance-row treatment

All **8 P2 governance artifacts** are represented as `git`-canonical rows (`status: draft`,
skeleton): `governance/README.md`, `CONTRIBUTING.md`, and the six directory READMEs.
`governance/controls/README.md` carries `compliance_gate: AD-5` and `reviewer: UNFILLED (compliance
reviewer — AD-5)`. They are explicitly flagged as **skeleton, not substantive governance content**.

## 6. Confluence-page treatment (no Confluence change)

Real, verified P1 IDs are recorded; **no Confluence page was created, moved, or modified**:
- **8 nodes + 3 templates** → `MANUAL`, `status: published` (they are `current`).
- **6 published Insurance pages** (parent `28770305` + 5 children) → `status: published`,
  `compliance_gate: none` (see §11).
- **3 Benefits pages** (`27951106`, `27983873`, `27918338`) → `status: draft` (preserved).
- Deferred Insurance pages remain unpublished (draft/planned rows, no `published` status).

The validator asserts each of the 20 known Confluence IDs appears exactly once with its intended
status.

## 7. Legacy-page treatment

All **23 legacy 360OS/Atlas pages** are seeded with: real title + `confluence_page_id`,
`confluence_parent_id` (homepage `21266602`), a **likely** framework destination (informational),
`canonical_source: legacy_unresolved`, `reconciliation_status: manual_review`, `status:
needs_review`, `legacy_source: 360os_atlas`, and a `legacy_identifier` (CAP-xxx / HOME-001 / etc.).
The validator enforces that **no legacy row is canonical or published**. None was moved, renamed,
merged, archived, relabeled, edited, or re-parented.

## 8. D10 migration results

The framework area-code taxonomy is now canonical in `pages.yml`. All **14 crosswalk section letters
(A–N)** are recorded in `taxonomy_migration_d10` and **preserved as `legacy_identifier`
(`legacy_source: crosswalk_section_letter`)** on each target area's row — including A→`GOV`, the
1→many L→`SEC` (+ node-20 siblings), and letter→structural mappings. The validator confirms every
letter is both mapped and preserved. The generated crosswalk renders the letter→area legend.

## 9. Insurance "11 vs 12" discrepancy — resolution

- **Cause:** a **counting error (undercount)** in the crosswalk **§1 section-map summary**, which
  labeled Insurance "11 pages" while the **§3 table enumerates 12 distinct proposed pages**. It was
  **not** a duplicate, not an omitted page in §3, and not a scope-definition issue — the §1 summary
  simply miscounted the §3 table.
- **Correct count: 12** proposed Insurance pages.
- **Records involved (the 12):** *Published (6, real Confluence pages):* Insurance Operations Overview
  (`28770305`), Commissions (`28803073`), Exceptions & Work Queues (`28835841`), Policyholder Portal
  (`28868609`), Reporting & Dashboard (`28901377`), Integrations (`28901397`). *Draft (6, no page
  yet):* Policy Management, New Business Case Management, In-Force Policy Servicing, Reviews &
  Obligations, Producer Licensing & CE, Roles & Responsibilities.
- **Validation proving the result:** the register contains exactly **6 INS rows `status: published`
  and 6 INS rows `status: draft`** = 12 proposed pages; page_ids are unique; the 6 published carry
  unique real Confluence IDs. (INS also has 13 planned coverage rows and 4 AD-5 regulated rule-set
  rows — distinct from the 12 proposed pages; INS total = 29.)

## 10. AD-5 validation results

- **10 rows carry `compliance_gate: AD-5`**, all with `status != published` (invariant holds):
  the 4 regulated Insurance rule-set rows (`INS-RULES-SUITABILITY/REPLACEMENT/LICENSING/CE`, planned),
  `INS-SOP-07` (Producer Licensing/CE, draft) and `INS-RACI-01` (Roles, draft), `CMP-POLICY` and
  `CMP-CONTROLS` (regulated compliance, planned), and `GOV-CONTROLS-README` + `GOV-CONTROLS-REGISTER`.
- **Programmatic invariant** `compliance_gate set ⇒ status ≠ published` is enforced by the validator
  (0 violations).
- **Published 0.10.0 Insurance pages classified individually:** all 6 (Overview, Commissions,
  Exceptions, Portal, Reporting, Integrations) are **non-regulated operational / boundary /
  descriptive** pages — **not** prohibited regulated rule sets — so they are correctly `compliance_gate:
  none, status: published` and were **not** overwritten with AD-5. Reviewer remains `UNFILLED`;
  Michael Shelton is business owner only; no regulatory certification is inferred.

## 11. Generator details

`scripts/registers/gen_crosswalk.py` deterministically renders `docs/DOCUMENTATION_CROSSWALK.md` from
`pages.yml`: stable ordering (node → area → doc_type → page_id), a generated-file warning header, all
areas incl. `SHARED`/`GOV`, the D10 letter legend, and per-row owner/reviewer/canonical-home/status/
compliance-gate/legacy-identifier/reconciliation-status. It emits **no manual-only data** (every value
comes from the register) and supports `--check` for CI drift detection. The one-time bootstrap seeder
(`build_pages_yml.py`) is retained for transparency but is **not** a maintained generator — `pages.yml`
is canonical henceforth.

## 12. Validation results

`scripts/registers/validate_register.py` passes with **0 errors**, checking: schema compliance,
required fields, unique `page_id`, unique canonical identity (non-null `confluence_page_id` /
real `repository_path`), valid areas/nodes/profiles/doc-types/status/canonical-source, the AD-5
invariant, no duplicate profile-union rows, every A–N letter mapped **and** preserved, every framework
area + `SHARED` + `GOV` represented, all 20 known Confluence IDs present with intended status, all 8
governance artifacts represented, exactly 23 non-canonical legacy rows, and **crosswalk currency**
(regeneration produces no diff — verified via `gen_crosswalk.py --check`).

## 13. Deviations

1. **Source filename.** Instruction cited `06-SYNC-AND-DEFINITION-OF-DONE.md` (correct here) — no
   deviation this phase; the earlier obsolete `06-DOCUMENTATION-SYNC-AND-DOD.md` reference is not used.
2. **Coverage = minimum-viable set** (not the full doc-type matrix) — documented §1; a deliberate,
   reversible scope choice that keeps the register meaningful. Expanding to the full matrix is a
   later-phase option.
3. **Crosswalk content replaced.** The former hand-authored `DOCUMENTATION_CROSSWALK.md` prose
   (§1 section map, §2 Benefits, §3 Insurance) is **superseded** by the generated view; its data is
   preserved as register rows (Benefits 3, Insurance 12, D10 letter map) and row `notes`. This is the
   intended D1 outcome.
4. **`MANUAL` pseudo-area** introduced for the manual's own nodes/templates (structural pages need a
   home); added to the valid-area set alongside `SHARED`/`GOV`.

## 14. Unresolved issues

- **Legacy reconciliation decision still pending** — the 23 legacy rows are `manual_review`; their
  final disposition (retain/link/move/merge/archive) and canonical homes await the separately-gated
  reconciliation decision. Until then their `confluence_page_id` is recorded but they are non-canonical.
- **Confluence rendering of git-canonical rows** (push to Confluence) is Phase E; today those rows
  carry `confluence_page_id: TBD`.
- **Compliance reviewer UNFILLED (AD-5)** — regulated rows stay gated.

## 15. Recommendation for P4

1. P3 is **complete**: `pages.yml` is canonical, the crosswalk is reproducibly generated, all schema
   and invariant checks pass, D10 is migrated, the Insurance discrepancy is resolved (12), and the 23
   legacy pages are non-canonical manual-review rows — with **no Confluence change**.
2. **Proceed to P4 (DoD Gate + PR Template)**: wire the **advisory** (non-blocking) docs gate that
   runs `validate_register.py` and `gen_crosswalk.py --check` in report-only mode, and add the DoD
   checklist to the PR template. Keep it advisory (exit 0) — blocking is Phase E (D6).
3. Before any **Confluence** mutation (re-parenting Insurance pages, applying `area:` labels, legacy
   moves/merges), obtain the separate reconciliation + Confluence-change approval.

---

**Stopping after the P3 report.** Awaiting explicit approval before beginning
**0.11-P4 — DoD Gate and PR Template**.
