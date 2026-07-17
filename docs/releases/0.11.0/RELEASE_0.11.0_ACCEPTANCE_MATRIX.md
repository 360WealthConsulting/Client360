# Release 0.11.0 — Acceptance Matrix

_Maps every approved release requirement/decision to its implementation artifact, validation
evidence, and status. Candidate commit `5e394eb` (branch `release/0.11.0`). Prepared in P5._

Status legend: **PASS** · **PASS WITH CONDITION** · **BLOCKED** · **N/A**.

| ID | Requirement / decision | Implementation artifact | Validation evidence | Status | Remaining condition | Release blocker |
|---|---|---|---|---|---|---|
| **D1** | Register canonical as `pages.yml`; crosswalk generated | `docs/registers/pages.yml`; `scripts/registers/gen_crosswalk.py` | `validate_register.py` OK; `gen --check` clean | PASS | — | No |
| **D2** | Rows = 26 areas + `SHARED` + `GOV` | `pages.yml` (28 areas) | validator: areas set = 28, no others | PASS | — | No |
| **D3** | Hybrid node-10 = union of both profiles | `pages.yml` 11 hybrid areas × 27 types | validator coverage check: 27/area | PASS | — | No |
| **D4** | Status enum `planned\|draft\|published\|needs_review` | `pages.yml` `enums`; validator | validator rejects other statuses (T4) | PASS | — | No |
| **D5** | `governance/` structure-only skeleton | `governance/` (8 files) | present; DoD strict clean | PASS | — | No |
| **D6** | Advisory documentation validation only | `check_documentation_dod.py`; `documentation-advisory.yml` | default/`--changed` exit 0; workflow non-blocking | PASS | — | No |
| **D7** | Semantic `page_id`; `confluence_page_id: TBD` where uncreated | `pages.yml` | DoD 0 warnings | PASS | — | No |
| **D8** | P1‖P2‖P3-author parallelism | phase execution | phase reports P1–P3 | PASS | — | No |
| **D9** | Machine-checkable `compliance_gate: AD-5`; gate ⇒ not published | `pages.yml` (11 AD-5 rows); validator | AD-5 invariant passes (T5) | PASS | — | No |
| **D10** | Reconcile crosswalk letters → framework area codes | `pages.yml` `taxonomy_migration_d10`; legacy_identifier | validator: 14 letters mapped + preserved | PASS | — | No |
| **P1** | Confluence skeleton (8 nodes + 3 templates) | space 3WCO pages | read-only verify: 8 nodes + 3 templates `current`, one each | PASS | — | No |
| **P2** | Git governance skeleton | `governance/` + `P2` report | present; skeleton only | PASS | — | No |
| **Legacy inventory** | Reconciliation inventory of 23 legacy pages | `LEGACY_ATLAS_CONFLUENCE_RECONCILIATION.md` | 23 pages inventoried | PASS | Reconciliation **decision** pending (post-merge follow-up) | No |
| **P3 register** | Canonical Publication Register | `pages.yml` (554 rows) | `validate_register.py` OK | PASS | — | No |
| **D10 migration** | Taxonomy migrated in register | `pages.yml` | validator letter checks | PASS | — | No |
| **Full coverage** | Complete profile/doc-type coverage | `pages.yml` coverage rows | validator coverage-completeness: 0 missing | PASS | — | No |
| **Generated crosswalk** | Crosswalk generated from register, current | `docs/DOCUMENTATION_CROSSWALK.md` | `gen --check` clean; deterministic rerun no diff | PASS | — | No |
| **Legacy non-canonical** | 23 legacy rows non-canonical / `manual_review` | `pages.yml` | validator: 23 `legacy_unresolved`/`manual_review`; T10 catches violation | PASS | — | No |
| **Insurance count** | 11-vs-12 resolved | `pages.yml` INS rows; `P3` report §9 | 1 landing + 5 children + 6 draft = 12; 43 total | PASS | — | No |
| **AD-5 invariant** | `compliance_gate` set ⇒ not published | validator + DoD | 11 AD-5 rows, 0 published | PASS | — | No |
| **P4 DoD checker** | Advisory checker reusing P3 tooling | `scripts/docs/check_documentation_dod.py` | 12 tests pass; reuses validator/gen | PASS | — | No |
| **PR template** | PR template with DoD checklist | `.github/pull_request_template.md` | present exactly once | PASS | — | No |
| **Advisory workflow** | Non-blocking docs workflow | `.github/workflows/documentation-advisory.yml` | YAML parses; `permissions: contents: read`; advisory | PASS | — | No |
| **No blocking enforcement** | Advisory only (D6) | checker exit 0; workflow no required check | no branch protection/required check added | PASS | Blocking is Phase E | No |
| **No unauthorized Confluence changes** | Read-only only | — | zero Confluence writes; read-only verify shows unchanged pages | PASS | — | No |
| **No regulated content published** | AD-5 content stays unpublished | `pages.yml`; Confluence | 11 AD-5 rows not published; no regulated page live | PASS | AD-5 content authoring is post-release (external dependency) | No |
| **No 0.10.0 modifications** | 0.10.0 code/docs/tag/pages intact | git + Confluence | tag `v0.10.0`→`5ba60a2` intact; 5 Insurance pages unchanged | PASS | — | No |
| **Existing CI (`build`)** | Existing blocking gate green on candidate | `.github/workflows/ci.yml` | Ruff F841 fixed (`fa538ce`); re-run on `5e394eb` | PASS WITH CONDITION | CI `build` green on final candidate (pre-merge) | No* |

\* Not an intrinsic release-content blocker, but merge to `main` requires the `build` check green
(branch protection). The Ruff defect that failed CI was fixed in P5 (`fa538ce`); the condition is the
CI re-run confirming green on the final candidate — a **pre-merge condition**.

## Summary

- **26 requirements/decisions: PASS.** **1 (existing CI): PASS WITH CONDITION** (CI green on final
  candidate — pre-merge). **0 BLOCKED.**
- **No unexplained blocker.** The only open conditions are (a) the CI `build` re-run confirming green
  after the P5 Ruff fix (pre-merge), and (b) legacy reconciliation decision + AD-5 content authoring
  (both explicitly **post-merge / later-phase / external**, not release blockers for the documentation
  foundation).
