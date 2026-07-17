# Release 0.11.0 — 0.11-P5 Release Candidate Validation

_Phase **0.11-P5 — Release Candidate Validation & Documentation Consistency Review**. Branch
`release/0.11.0`. Executed 2026-07-17. Validation + release-preparation only — no substantive
authoring, no Confluence writes, no application/migration change (except the P5 Ruff tooling fix)._

## 1. Candidate, branch & remote state

- **Validated candidate:** `5e394eb` (CI `build` **green** — §5). Final head adds only this report +
  the acceptance matrix + change summary (documentation-only; no effect on the build gate).
- **Branch:** `release/0.11.0`; **base (main):** `6f7292c` (unchanged baseline).
- **Local == remote** at push time; working tree clean.

## 2. Release delta summary

- **Base → candidate:** `main` `6f7292c` → branch head.
- **Delta (main..candidate before P5 reports):** 27 files, +16124 / −136; **all documentation /
  tooling / CI** — **no `app/**` or `migrations/**` changes.** P5 adds the Ruff fix + 5 doc files.
- **18+ phase commits** (P0 → P4 + P5), **no merge commits, no duplicate phase commits**.
- **Base commit:** `6f7292c` · **candidate:** `5e394eb` (+ P5 doc commits).

## 3. Files added / modified / deleted (P5)

- **Modified:** `scripts/docs/check_documentation_dod.py` (Ruff F841 fix),
  `docs/releases/0.11.0/P3_PUBLICATION_REGISTER_REPORT.md` (§6 MANUAL→GOV consistency fix).
- **Added:** `docs/releases/0.11.0/RELEASE_0.11.0_ACCEPTANCE_MATRIX.md`,
  `RELEASE_0.11.0_CHANGE_SUMMARY.md`, `P5_RELEASE_CANDIDATE_VALIDATION.md`.
- **Deleted:** none.

## 4. Repository tests (existing project validation)

Authoritative run is the existing CI **`build`** job (Python 3.12 + Postgres). On candidate
`5e394eb` **all steps pass**: Ruff gate ✓, CHANGELOG lint ✓, Compile ✓, single Alembic head ✓,
migrations reversible ✓, test-database safety guard ✓, **Run tests ✓**.

**Classification of earlier signals:**
- The **candidate before the fix (`78b157e`) failed CI at the Ruff gate** — a genuine **Release
  0.11.0 tooling defect** (two `F841` unused-locals in the new DoD checker). **Fixed in P5**
  (`fa538ce`); `ruff check` clean, `ruff_gate.py` reports no new violations, CI re-run green.
- **Local** `check_changelog.py` / `ruff` / `compileall` failures are an **environment limitation**
  (local interpreter is Python 3.9; the project runtime is 3.12 — e.g. `zip(strict=)` is 3.10+; `ruff`
  not installed locally). **Not** a regression — CI (3.12) passes all. No unrelated application
  behavior was changed to make the docs release pass.

## 5. CI evidence

CI run on `5e394eb`: **success** (Ruff gate, CHANGELOG lint, compile, migrations, full test suite all
green). The pre-fix failure (`78b157e`, Ruff gate) is resolved.

## 6. Register validation

`python3 scripts/registers/validate_register.py` → **OK (554 rows)**. Confirmed: **26 framework areas
+ `SHARED` + `GOV`** (28 total), **no `MANUAL`**; complete profile/doc-type coverage; **27-type Hybrid
union** for all 11 hybrid areas; no duplicate Hybrid rows; unique `page_id`; unique canonical
identity; valid statuses & canonical-source; **23 legacy rows non-canonical / `manual_review`**; all
known Confluence IDs preserved; governance records present; **AD-5 invariant holds**.

## 7. Crosswalk determinism

`gen_crosswalk.py --check` → **current**; deterministic rerun produces **no diff**. The generated
crosswalk retains its do-not-edit warning.

## 8. DoD checker validation

`check_documentation_dod.py` default → **exit 0**; `--changed` → **exit 0**; `--strict` → **exit 0**
(clean candidate, 0 errors / 0 warnings). Reuses `validate_register.py` + `gen_crosswalk.py --check`
by subprocess (no duplication). 12 tests (mutation-and-restore) pass; all mutations restored.

## 9. YAML & Python validation

- YAML parses: `ci.yml`, `documentation-advisory.yml`, `pages.yml` — OK.
- Python compiles: all `scripts/registers/*.py`, `scripts/docs/*.py` — OK; `ruff check` clean.
- `git diff --check` — clean. No case-collision: exactly one `.github/pull_request_template.md`,
  one `documentation-advisory.yml`. No unexpected executable bits / line-ending issues in new scripts.

## 10. Documentation consistency findings & corrections

Reviewed the full 0.11.0 documentation set. **One current-state defect corrected:** P3 report §6
described structural nodes/templates as area `MANUAL` — reclassified to **`GOV`** to match the
remediated register (the §16 remediation record is preserved; historical decision history not
altered). All other flagged patterns were **already correct**:
- `MANUAL` elsewhere appears only in removal/remediation context; **314-row / 29-area / obsolete
  minimum-viable coverage** language — none remain as current claims (minimum-viable appears only in
  historical/remediation notes and the legitimate template concept);
- "**six published Insurance pages**" appears only where it is *explained* as 1 landing + 5 children;
- **AD-5 resolved** / **Michael as regulatory approver** / **advisory blocking** — every occurrence
  states the correct negative;
- **no broken relative links / nonexistent-file links** (DoD link check clean);
- **no stale phase status** (P1–P4 all reported complete).

Historical reports were **not** rewritten to conceal what happened; corrections were limited to
current-state text and generated artifacts.

## 11. Confluence read-only verification (no writes)

| Category | Expected | Verified |
|---|---|---|
| Operations Manual nodes | 8, each once, `current` | ✅ `28966913, 28835861, 28999681, 29032449, 29032469, 28868631, 28835881, 28868651` |
| Area Shell template **pages** (not native templates) | 3, each once, `current` | ✅ `28966933, 28999701, 28835901` |
| Insurance landing page | present, `current` | ✅ `28770305` |
| Insurance operational child pages | 5, `published`/`current` | ✅ `28803073, 28835841, 28868609, 28901377, 28901397` (AD-5 banners intact) |
| Benefits pages | drafts | ✅ `27951106, 27983873, 27918338` = `draft` |
| Legacy page sample (unchanged by 0.11.0) | not moved/edited | ✅ `24117290` (360OS Home) last modified **Jul 10** (before the release branch) |
| Duplicate framework hierarchy | none | ✅ each ID resolves exactly once |
| Draft/regulated inadvertently published | none | ✅ none |

**No Confluence write occurred in any 0.11.0 phase.** Read access was available; verification is
fresh (not inferred).

## 12. Known Confluence-ID accounting

- **Status-fidelity check: 20 IDs** = 8 nodes + 3 templates + 1 Insurance landing + 5 Insurance
  children + 3 Benefits drafts.
- **Legacy: 23 IDs** checked by a **separate** rule (non-canonical / `manual_review`); not part of the
  20-ID count. Total distinct Release-0.11.0-tracked Confluence IDs = **43** (20 + 23), not conflated.

## 13. AD-5 state

Accountable compliance reviewer **`UNFILLED`**; Michael Shelton is **business owner only** (no
regulatory certification). **11 AD-5-gated register rows, all non-published** (invariant holds).
Suitability / replacement-1035 / licensing / CE rule sets remain **blocked**; **no substantive
regulated rule set authored**. P5 does **not** resolve AD-5. AD-5 is a **post-release
content-authoring dependency**, not a blocker to releasing the documentation foundation.

## 14. Security & client-data review

Delta scanned for private keys / AWS-style keys / assigned secrets / bearer tokens / SSNs — **none
found** (advisory checker + delta grep). Discovered values are never printed. No client data added.
This is a lightweight guardrail, not a DLP system.

## 15. Acceptance-matrix summary

26 requirements/decisions **PASS**; 1 (existing CI) **PASS WITH CONDITION** now satisfied (CI green on
`5e394eb`); **0 BLOCKED**; **no unexplained blocker** (`RELEASE_0.11.0_ACCEPTANCE_MATRIX.md`).

## 16. Deviations

1. **Source filename.** The task cited `docs/documentation-framework/04-OWNERSHIP-AND-REVIEW-MODEL.md`;
   the actual file is **`04-GAP-ANALYSIS.md`** (ownership/review model lives in `02` + `06`). Used
   actual filenames.
2. **P5 tooling fix touched a script** (`check_documentation_dod.py`) — permitted, as it fixes a
   defect **directly caused by Release 0.11.0 documentation tooling** (Ruff F841); documented here and
   in commit `fa538ce`. No application code / migration changed.

## 17. Unresolved issues (none block the merge)

- **Legacy reconciliation decision** — inventory complete; dispositions pending (**post-merge
  follow-up**).
- **AD-5 content authoring** — blocked pending a named reviewer (**external dependency**).
- **Blocking DoD enforcement / Confluence push** — **later-phase** (Phase E).
- **PyYAML not a declared repo dependency** — the advisory workflow installs it (later phase may
  formalize).

## 18. Release blockers

**None (pre-merge).** The one pre-merge condition — CI `build` green on the candidate — is **met** on
`5e394eb` (the P5 Ruff fix resolved the only failure). All remaining conditions are post-merge /
later-phase / external.

## 19. Merge recommendation

### READY TO MERGE

Release 0.11.0 delivers the complete non-substantive Documentation Foundation & Governance layer
(architecture decisions D1–D10; Confluence skeleton; Git governance skeleton; canonical Publication
Register with full profile coverage and D10 taxonomy migration; generated crosswalk; non-canonical
legacy inventory; Insurance count resolved; AD-5 invariant enforced; advisory DoD checker + PR
template + non-blocking workflow). Every approved requirement is **PASS**; the existing CI `build`
gate is **green** on the candidate; no Confluence writes; no application/migration/0.10.0/tag changes;
no secrets/client data; no blocking CI introduced.

**Conditions (none pre-merge blocking):**
- *Post-merge follow-up:* legacy 360OS/Atlas reconciliation decision.
- *Later phase (E):* advisory→blocking enforcement, Confluence push, `release.sh` docs precondition.
- *External dependency:* AD-5 — name a compliance reviewer before any regulated content is authored.

_Recommend proceeding to release actions (merge → tag `v0.11.0` → GitHub Release) upon approval._

---

**Stopping after this P5 report and merge recommendation.** Awaiting explicit approval before merging,
tagging, opening/converting a release PR, or beginning any later phase.
