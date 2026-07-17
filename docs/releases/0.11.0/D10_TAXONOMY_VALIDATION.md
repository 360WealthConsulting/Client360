# D10 — Taxonomy Migration Validation Artifact

_Validation artifact only. Prepared after approval of `D10_TAXONOMY_IMPACT_ASSESSMENT.md` (Adopt the
framework area-code taxonomy). **This document changes nothing** — no taxonomy, no Publication
Register, no crosswalk, no Confluence, no labels. It pre-verifies the letter→code migration so it can
be executed safely during **P3**. The migration itself is **not performed here** and waits for
explicit approval. Date 2026-07-17._

> Sources verified against: `docs/DOCUMENTATION_CROSSWALK.md` (§1 section map A–N; §2 Benefits rows;
> §3 Insurance rows), `documentation-framework/01-INFORMATION-ARCHITECTURE.md` §3 (26 area codes),
> and the 0.10.0 published Confluence page IDs. All identifiers below are transcribed, not invented.

---

## 1. Complete before/after taxonomy mapping

**Before** = crosswalk business-section letters (A–N, 14). **After** = framework area codes (26) +
pseudo-areas `SHARED` (node 40) / `GOV` (node 90).

| Before (letter · section) | After (primary code) | Node | Related/linked codes | Relationship |
|---|---|---|---|---|
| A · Executive Management | `GOV` (+ node 00 Company Home) | 90 / 00 | — | letter→**structural** (not a capability area) |
| B · Sales and Marketing | `MKT` | 30 | `CRM` (linked) | 1→1 primary |
| C · Client Experience | `DOC` | 10 | `CLM360` (portal, linked) | 1→1 primary |
| D · Tax Operations | `TAXOPS` | 10 | — | 1:1 |
| E · Wealth Management | `WLTH` | 10 | — | 1:1 |
| F · Employee Benefits | `BEN` | 10 | `RET` (linked) | 1:1 |
| G · Retirement Plans | `RET` | 10 | `BEN` (shared) | 1:1 |
| H · Insurance Operations | `INS` | 10 | `ACCT`,`CMP`,`DOC`,`SEC` (cross-serve) | 1:1 |
| I · Finance and Accounting | `ACCT` | 30 | `INS` (commissions) | 1:1 |
| J · HR and People Operations | `HR` | 30 | — | 1:1 |
| K · Compliance | `CMP` | 30 | `INS` (AD-5), `SEC` | 1:1 |
| L · Technology and Cybersecurity | `SEC` | 20 | spawns siblings `M365`,`AD`,`NET`,`SRV`,`DR` | 1→**many** (infra split) |
| M · Administration | `OFFICE` | 30 | — | 1:1 (Office Operations) |
| N · Training | `TRAIN` | 80 | — | 1:1 |

## 2. Every existing identifier

**2a. Crosswalk letter sections (14):** A, B, C, D, E, F, G, H, I, J, K, L, M, N.

**2b. Existing Confluence pages (9 nodes = 1 parent + 8 pages):**

| Confluence ID | Title | Status | → Area code |
|---|---|---|---|
| `28770305` | Insurance Operations — Release 0.10.0 (parent) | published | `INS` |
| `28803073` | Insurance Commissions — Operating Procedure | published | `INS` (rel `ACCT`) |
| `28835841` | Insurance Exceptions & Work Queues — Operating Procedure | published | `INS` (rel `CMP`) |
| `28868609` | Insurance Policyholder Portal — Operating Procedure | published | `INS` (rel `DOC`) |
| `28901377` | Insurance Reporting & Operations Dashboard — Operating Procedure | published | `INS` |
| `28901397` | Insurance Integrations — Extension Points (Reference) | published | `INS` (rel `SEC`) |
| `27951106` | Employee Benefits — Compliance & Renewal Obligations | draft | `BEN` (rel `RET`) |
| `27983873` | Employee Benefits — Deadline Monitoring, Exceptions & Work Queues | draft | `BEN` |
| `27918338` | Employee Benefits — Obligation Management Checklist | draft | `BEN` |

**2c. Existing register rows:** Benefits §2 — 3 rows (`EB-REF-01`, `EB-SOP-01`, `EB-CHK-01`).
Insurance §3 — 12 title rows (Overview, Policy Management, New Business, In-Force Servicing, Reviews &
Obligations, Producer Licensing/CE, Commissions, Exceptions & Work Queues, Policyholder Portal,
Reporting, Roles & Responsibilities, Integrations). *(Note the §3 header says "11 pages" while 12
rows are enumerated — see §5 duplicate/count check.)*

## 3. Every new identifier

**3a. Area codes with no dedicated source letter (new rows — 13):**

| Code | Node | Area | Origin |
|---|---|---|---|
| `CLM360` | 10 | Client360 platform | linked from C; platform spine, no letter |
| `CRM` | 10 | CRM | linked from B; own area, no letter |
| `WORK` | 10 | Work Management | new |
| `RPT` | 10 | Reporting | new |
| `AIA` | 10 | AI & Automation | new |
| `M365` | 20 | Microsoft 365 | L-split sibling |
| `AD` | 20 | Active Directory | L-split sibling |
| `NET` | 20 | Networking | L-split sibling |
| `SRV` | 20 | Servers | L-split sibling |
| `DR` | 20 | Disaster Recovery | L-split sibling |
| `VEND` | 30 | Vendor Management | new |
| `SOPLIB` | 80 | SOP Library | new |
| `RELMGMT` | 80 | Release Management | new |

**3b. Pseudo-areas (2):** `SHARED` (node 40 singletons), `GOV` (node 90 registers/governance).

**3c. Reconciliation:** 13 codes remapped 1:1 from letters (TAXOPS, WLTH, BEN, RET, INS, ACCT, HR,
CMP, OFFICE, TRAIN, MKT, DOC, SEC) **+** 13 new codes (3a) **= 26 framework codes** ✔, plus 2
pseudo-areas = **28 register area keys**.

## 4. One-to-one mapping verification

| Class | Count | Members | Verified |
|---|---|---|---|
| 1:1 letter → single code | 12 | B→MKT, C→DOC, D→TAXOPS, E→WLTH, F→BEN, G→RET, H→INS, I→ACCT, J→HR, K→CMP, M→OFFICE, N→TRAIN | ✔ each letter → exactly one primary code |
| 1→many (split) | 1 | L→SEC (+ siblings M365/AD/NET/SRV/DR as own rows) | ✔ intended infra decomposition |
| letter→structural | 1 | A→GOV/00 (no capability area) | ✔ absorbed into GOV pseudo-area |
| **Total letters** | **14** | A–N | ✔ all accounted, none unmapped |

## 5. Duplicate detection

| Check | Result |
|---|---|
| Two letters → same primary code | **None.** All 14 primaries distinct (A→GOV, B→MKT, …, N→TRAIN). |
| Two codes collide in the 26-set | **None.** Area codes are disjoint (IA §3); `page_id = <AREA>-<TYPE>[-nn]` is position-scoped, so `SEC-SEC`/`HR-SEC` never collide. |
| Duplicate Confluence page IDs | **None.** 9 IDs all unique. |
| Duplicate register rows | **None** functionally; **1 count discrepancy flagged**: crosswalk §3 header says "11 pages" but 12 title rows are listed. *Validation flag — resolved by counting rows at migration time; not corrected here (crosswalk not modified).* |

## 6. Orphan detection

| Check | Result |
|---|---|
| Letters with no target code | **None** — every letter maps (A→structural GOV). |
| Framework codes with no source letter | **13** (see §3a) — **expected**, gain fresh `planned` rows; not errors. |
| Published pages with no area code | **None** — all 9 nodes → `INS`/`BEN` (valid). |
| Register rows with no area | **None** — Benefits→`BEN`, Insurance→`INS`. |
| Cross-refs pointing at a non-existent code | **None** (see §7). |

## 7. Cross-reference verification

Crosswalk §3 declares cross-serving links; each must resolve to a valid target code:

| Source (INS row) | "also serves" (letter) | → code | Valid? |
|---|---|---|---|
| Commissions | I · Finance and Accounting | `ACCT` | ✔ |
| Exceptions & Work Queues | K · Compliance | `CMP` | ✔ |
| Producer Licensing & CE | K · Compliance | `CMP` | ✔ |
| Roles & Responsibilities | K · Compliance | `CMP` | ✔ |
| Policyholder Portal | C · Client Experience | `DOC` | ✔ |
| Integrations | L · Technology & Cybersecurity | `SEC` | ✔ |
| Retirement (G) ↔ Benefits (F) | shared obligations | `RET`↔`BEN` | ✔ |

All 7 cross-references resolve to existing codes. **No dangling reference.**

## 8. Register-row verification

| Register area | Rows before | Area key after | Row action |
|---|---|---|---|
| Benefits (§2) | 3 (`EB-*`) | `BEN` | re-key `area: BEN`; keep IDs, owners, dates |
| Insurance (§3) | 12 titles | `INS` (+ `related:` per §7) | re-key `area: INS`; 5 keep published status + `confluence_page_id`; 7 stay `draft`; regulated rows carry `compliance_gate: AD-5` (D9) |
| Section map (§1) | 14 letters | 28 area keys | become area rows (mostly `planned`, no pages) |

**Invariant checks to run at migration:** every row has a valid `area` code; every published row keeps
its `confluence_page_id`; every regulated INS row has `compliance_gate: AD-5` and `status != published`
where unpublished (D9); no row loses owner/review dates.

## 9. Confluence page verification

| Property | Before | After | Change? |
|---|---|---|---|
| Numeric page ID | e.g. `28803073` | `28803073` | **No** (immutable) |
| Canonical URL `/pages/<id>/…` | stable | stable | **No** |
| Title / body | 0.10.0 content | unchanged | **No** (release-isolation) |
| Breadcrumb / parentage | under parent `28770305` | under node `10 · Insurance (INS)` | Yes (breadcrumb only) |
| AD-5 boundary text | present | unchanged | **No** |

The 5 published Insurance pages and 3 Benefits drafts are **not re-authored**; only parentage/labels
change. No published-page URL breaks.

## 10. Label verification

| Check | Result |
|---|---|
| Label scheme | `area:<code>`, `type:<code>`, `profile:…` (IA §3) |
| Existing `area:` labels today | **None** (0.10.0 pages published with prose banners, not formal labels) → migration is **additive**, not relabel-and-remove |
| Labels to apply | `area:INS` ×6 (incl. parent), `area:BEN` ×3; `profile:software`/`operations` per Hybrid |
| Label collisions | **None** — codes are disjoint; letters were never label values |

## 11. Rollback verification

| Step | Mechanism | Reversible? |
|---|---|---|
| Register taxonomy | `git revert` the `pages.yml` + generated-crosswalk change | ✔ restores letters verbatim from history |
| Confluence labels | MCP bulk remove `area:*` labels (≤9 pages) | ✔ additive change, cleanly removable |
| Page parentage | re-parent ≤9 pages back under `28770305` | ✔ breadcrumb only; IDs/URLs never moved |
| Page content / IDs | never changed | ✔ nothing to roll back |

**Conclusion:** rollback is a **metadata + regeneration** operation, fully git-reversible; no content
migration, so no data-loss path. Rollback tested-by-construction (each step has a defined inverse).

## 12. Final migration checklist (for P3 — do NOT execute yet)

- [ ] D10 migration explicitly approved (this artifact reviewed).
- [ ] `docs/registers/pages.yml` authored with `area` = framework codes (§3c set of 28).
- [ ] 14 letter sections re-keyed per §1; 13 new codes seeded as `planned` (§3a); `SHARED`/`GOV` seeded.
- [ ] Benefits 3 rows → `BEN`; Insurance 12 rows → `INS` with `related:` cross-refs (§7).
- [ ] Regulated INS rows carry `compliance_gate: AD-5`; invariant `gate ⇒ status != published` passes (D9).
- [ ] §5 count discrepancy (11 vs 12) resolved by actual row count during authoring.
- [ ] Crosswalk regenerated **from** `pages.yml`; retain letter→code legend for one release (traceability).
- [ ] 9 existing pages: apply `area:` labels; re-parent under node `10` (INS/BEN); verify IDs/URLs unchanged.
- [ ] Orphan check re-run: 0 unmapped letters, 0 published-page orphans, 0 dangling cross-refs.
- [ ] Duplicate check re-run: 0 colliding area keys / page_ids.
- [ ] Rollback rehearsal noted (git revert + label removal + re-parent) before go-live.
- [ ] `git diff --check` clean; no `app/**`/`migrations/**`/release changes; 0.10.0 artifacts untouched.

---

_This is a validation artifact. **No taxonomy, register, or crosswalk change is made here.** Awaiting
explicit approval to perform the D10 migration during P3._
