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

**Scope decision (AMENDED — see §16 Remediation).** Coverage is the **complete** per-profile
document-type set from framework `01-INFORMATION-ARCHITECTURE.md` §2 (Software 19, Infrastructure 14,
Business Operations 14). Hybrid (node 10) areas seed the **complete de-duplicated union of the
Software and Business-Operations requirements** (27 types). Nodes 40/80/90 carry no profile in 01 §2
(shared singletons / aggregators / registers) and use justified specific sets.
_(The initial P3 pass seeded a minimum-viable set; that was corrected in remediation — figures in
§2–§10 below reflect the corrected full-matrix register; §16 documents the change.)_

## 2. Counts

- **Total register rows: 554.**
- **By status:** `planned` 489 · `draft` 23 · `needs_review` 23 (all legacy) · `published` 19.
- **By canonical source:** `git` 230 · `confluence` 294 · `generated` 7 · `legacy_unresolved` 23.
- **By compliance gate:** `AD-5` 11 · `none` 543.
- **By node:** 00→4 · 01→5 · 10→323 · 20→87 · 30→92 · 40→9 · 80→14 · 90→20.
- **By profile:** hybrid 316 · operations 142 · infrastructure 84 · library 12.
- **By area (28 total = 26 framework + `SHARED` + `GOV`; no `MANUAL`):** INS 43, GOV 34, WLTH 29,
  CRM 28, DOC 28, RPT 28, TAXOPS 28, CLM360 27, RET 27, AIA 27, WORK 27, BEN 30, CMP 16, node-20
  areas 14–15, node-30 areas 15–16, SHARED 8, SOPLIB 5, TRAIN 4, RELMGMT 4.
- **By document type:** every profile-bearing area now carries its full 01 §2 doc-type set; the most
  frequent types are EXEC, SOP, RELATED, CHANGELOG, ARCH, PURPOSE, SEC, CALENDAR, POLICY, RACI, plus
  the structural/register/legacy types (NODE 8, TEMPLATE 3, REGISTER 8, LEGACY 23).

## 3. Framework-area coverage

All **26 framework areas** plus `SHARED` and `GOV` are represented (validator-enforced) — **28 valid
areas total; `MANUAL` was removed** (Issue 1). Node 10 (11 hybrid areas), node 20 (6 infra), node 30
(6 ops), node 80 (3 library aggregators), node 40 (`SHARED` singletons), node 90 (`GOV`). The
Operations Manual's own structural pages (8 nodes + 3 templates) are classified under the approved
**`GOV`** area while retaining their true tree node.

## 4. Hybrid union treatment

The 11 node-10 areas use `profile: hybrid` = the **complete de-duplicated union of the full Software
profile (19 types) and the full Business-Operations profile (14 types)** = **27 types** per area
(11 × 27 = 297 coverage rows). SOP/TRAIN and the core types appear once (de-duplicated). This union
is a **superset** of both the 01 §2 hybrid definition (Software + SOP/Policy/RACI/Calendar) and the
approved D3 list (+ Checklist), so no requirement is dropped. The validator confirms **no duplicate
profile-union coverage rows** and **complete coverage** (each hybrid area has exactly the 27-type
set).

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
- **Composition (AMENDED — Issue 3), the 12 proposed pages by class:**
  - **Insurance area parent / landing page — 1:** `INS-EXEC-01` "Insurance Operations — Release
    0.10.0" (`28770305`). Classified as **both** a landing/section-navigation page **and** a
    descriptive operational overview; `doc_type: EXEC` (Executive Overview). It is **not** one of the
    operational child SOPs.
  - **Published operational child pages — 5 (preserved, verified IDs):** Commissions `28803073`
    (`INS-SOP-01`), Exceptions & Work Queues `28835841` (`INS-SOP-02`), Policyholder Portal
    `28868609` (`INS-SOP-03`), Reporting & Dashboard `28901377` (`INS-REPORT-01`), Integrations
    `28901397` (`INS-INTEG-01`). These are the five Release 0.10.0 operational deliverables — retained
    unchanged.
  - **Unpublished / draft proposed pages — 6:** Policy Management (`INS-USERGUIDE-01`), New Business
    Case Management (`INS-SOP-04`), In-Force Servicing (`INS-SOP-05`), Reviews & Obligations
    (`INS-SOP-06`), Producer Licensing & CE (`INS-SOP-07`), Roles & Responsibilities (`INS-RACI-01`).
  - So the earlier "six published Insurance pages" = **1 landing/parent + 5 operational children**
    (not six equivalent operational pages).
- **Total Insurance register records: 43** = 27 hybrid coverage rows + 4 explicit AD-5 regulated
  rule-set rows (`INS-RULES-SUITABILITY/REPLACEMENT/LICENSING/CE`) + the 12 proposed pages above.
- **Validation proving the result:** the register holds exactly **6 INS `published`** (1 landing +
  5 children) and **6 INS `draft`** = 12 proposed pages; all page_ids unique; the 6 published carry
  unique verified Confluence IDs.

## 10. AD-5 validation results

- **11 rows carry `compliance_gate: AD-5`**, all with `status != published` (invariant holds):
  the INS Business-Rules coverage row (`INS-RULES`) + the 4 explicit regulated rule-set rows
  (`INS-RULES-SUITABILITY/REPLACEMENT/LICENSING/CE`, planned), `INS-SOP-07` (Producer Licensing/CE,
  draft) and `INS-RACI-01` (Roles, draft), `CMP-POLICY` and `CMP-CONTROLS` (regulated compliance,
  planned), and `GOV-CONTROLS-README` + `GOV-CONTROLS-REGISTER`.
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

1. **Source filename.** `06-SYNC-AND-DEFINITION-OF-DONE.md` is the correct file — no deviation.
2. **Crosswalk content replaced.** The former hand-authored `DOCUMENTATION_CROSSWALK.md` prose
   (§1 section map, §2 Benefits, §3 Insurance) is **superseded** by the generated view; its data is
   preserved as register rows (Benefits 3, Insurance 12, D10 letter map) and row `notes`. This is the
   intended D1 outcome.
3. **Node-40/80/90 use justified specific sets, not a profile matrix** — 01 §2 assigns profiles only
   to nodes 10/20/30; nodes 40 (shared singletons), 80 (aggregators), 90 (registers/governance) carry
   no framework profile, so they seed specific pages rather than a doc-type matrix. Source-justified,
   not a preference reduction.

_(Resolved in remediation — no longer deviations: the earlier "minimum-viable coverage" and the
unapproved `MANUAL` pseudo-area. See §16.)_

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

## 16. P3 REMEDIATION (amendment)

Applied after the conditional-acceptance review. The figures in §1–§15 above already reflect the
remediated register; this section records what changed and the verification.

### 16.1 Issue 1 — `MANUAL` pseudo-area removed
- `MANUAL` was **not** an approved taxonomy area. It has been **removed**. The approved taxonomy is
  now exactly the **26 framework area codes + `SHARED` + `GOV` = 28 valid areas**.
- The 11 structural manual pages (8 nodes + 3 templates) are re-classified under the approved
  **`GOV`** area (page_ids `GOV-NODE-00…90`, `GOV-TEMPLATE-*`), keeping their true tree node and
  verified Confluence IDs — no Confluence change.
- The 2 legacy pages formerly tagged `MANUAL` (`📐 360 Standards` `23199768`, `🏠 Home` `23166977`)
  now map to the approved area **`GOV`**, remaining `legacy_unresolved` / `manual_review` /
  `needs_review` (non-canonical — a legacy page mapping to an approved area does **not** make it that
  area's canonical page).
- **Schema validation updated:** only the 26 codes + `SHARED` + `GOV` are accepted; any other area
  (incl. `MANUAL`) is rejected.

### 16.2 Issue 2 — complete document-type coverage
- **Controlling determination (Option A — full matrix).** Framework `01-INFORMATION-ARCHITECTURE.md`
  §2 defines the complete per-profile sets: **Software 19, Infrastructure 14, Business Operations 14**
  (core = EXEC/PURPOSE/RELATED/CHANGELOG). The initial pass seeded a minimum-viable subset — corrected
  to the **full** sets for all profile-bearing areas (nodes 10/20/30).
- **Hybrid union.** Per remediation Issue 2A, node-10 areas seed the **complete de-duplicated union**
  of the full Software and full Business-Operations sets = **27 types**. This is a **superset** of the
  narrower 01 §2 hybrid definition (Software + SOP/Policy/RACI/Calendar) and the approved D3 list
  (+ Checklist); adopting the superset drops no requirement, so the sources do not conflict in a way
  that reduces scope. (Had they, this section would report a conflict rather than choose a smaller set.)
- **Nodes 40/80/90** carry no profile in 01 §2 → justified specific sets (SHARED singletons, node-80
  aggregators core+index, GOV registers/governance/structural).
- **Validation:** a new coverage-completeness check asserts every node-10 area has the full 27-type
  set, every node-20 area the 14 infra types, every node-30 area the 14 ops types, and node-80 areas
  the aggregator set — **0 missing**.

### 16.3 Issue 3 — Insurance count terminology (see §9)
- **1** Insurance parent/**landing** page (`28770305`, `INS-EXEC-01`) — classified as **both** landing
  and descriptive overview; `doc_type: EXEC`.
- **5** published operational child pages (verified IDs preserved).
- **6** unpublished/draft proposed pages.
- **43** total Insurance register records.
- The prior "six published Insurance pages" is now explained as **1 landing + 5 operational children**.

### 16.4 Final figures
- **Final valid-area count: 28** (26 framework + `SHARED` + `GOV`).
- **Final row count: 554.**
- **Hybrid coverage: 11 × 27 = 297 rows** (node 10 total 323 incl. INS/BEN instances).
- **AD-5 rows: 11**, all non-published (invariant holds).
- **Legacy: 23**, all `legacy_unresolved` / `manual_review` / `needs_review` (non-canonical).

### 16.5 Validation results (post-remediation)
`validate_register.py` passes **0 errors**, including: only 26+`SHARED`+`GOV` areas valid; complete
profile/document-type coverage; no duplicate Hybrid-union rows; AD-5 invariant; all 20 known
Confluence IDs present with intended status (**unchanged**); 23 non-canonical legacy rows; crosswalk
current. `gen_crosswalk.py --check` exit 0 (regeneration produces no diff). **No Confluence change**
occurred during remediation.

### 16.6 Remaining deviations
Only the non-blocking items in §13 (crosswalk prose superseded by the generated view; nodes 40/80/90
use justified specific sets). No unresolved conflict.

---

**Stopping after the amended P3 report.** Awaiting explicit approval before beginning
**0.11-P4 — DoD Gate and PR Template**.
