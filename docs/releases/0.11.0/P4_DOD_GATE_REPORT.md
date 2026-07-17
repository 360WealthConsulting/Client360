# Release 0.11.0 — 0.11-P4 DoD Gate & PR Template Report

_Phase **0.11-P4 — DoD Gate and PR Template**. Branch `release/0.11.0`. Executed 2026-07-17.
**Advisory only (decision D6)** — no blocking CI, no Confluence change, no app/migration/tag change._

> **Guardrails honored:** advisory mode only (never `--strict` in CI); no blocking branch protection
> or required check; no Confluence change; no draft published; no legacy disposition changed; no
> regulated rule set authored; AD-5 not resolved; no invented reviewer; Michael Shelton = business
> owner (not regulatory approval); no app code / migration / release-tag / 0.10.0 change.

## 1. Files created / updated

| File | Action |
|---|---|
| `scripts/docs/check_documentation_dod.py` | **Created** — advisory DoD checker |
| `scripts/docs/README.md` | **Created** — checker docs (usage, rules, exit behavior, reuse) |
| `.github/pull_request_template.md` | **Created** — no PR template existed before |
| `.github/workflows/documentation-advisory.yml` | **Created** — advisory, non-blocking |
| `docs/releases/0.11.0/P4_DOD_GATE_REPORT.md` | **Created** — this report |

Also carried in this phase: a one-line register tweak (draft Insurance proposal rows now use
`confluence_page_id: TBD` instead of null) + regenerated crosswalk, so the clean repo reports **0
warnings**.

## 2. Existing repository automation discovered

- **`.github/workflows/ci.yml`** — the existing **`build`** job (Python 3.12, `requirements-dev.txt`):
  Ruff gate, CHANGELOG lint, compileall, migration-head/reversibility, Postgres test suite. It is the
  blocking check on `main`. **Left unchanged.** The new advisory workflow is **separate** and **not**
  a required check.
- **No PR template** existed → created (not a duplicate).
- **Python convention:** 3.12, pinned deps via `requirements*.txt`. PyYAML is **not** declared there;
  the advisory workflow installs `pyyaml` directly (only the one dependency it needs) rather than
  touching `requirements.txt` (which feeds the app/blocking build).

## 3. Checker architecture

`check_documentation_dod.py` collects findings `(severity, rule, file, message)`, runs
repository-wide integrity checks (always) plus file-level checks (all docs, or `--changed` subset),
prints a grouped advisory report, and applies mode-specific exit behavior. Register validation is
**reused, not reimplemented** — invoked by subprocess. The documentation surface scanned is
`docs/**` and `governance/**` (plus the PR template); `scripts/` is not scanned, so the checker's own
patterns are never self-matched.

## 4. Rules implemented

**Publication Register integrity (reused P3 validator):** schema, required fields, valid area codes
(26 + `SHARED` + `GOV`), valid profiles/doc-types/statuses/canonical-source, unique `page_id`, unique
canonical identity, AD-5 invariant, complete profile/doc-type coverage, no duplicate Hybrid-union
rows, all framework areas + `SHARED`/`GOV`, legacy non-canonical/`manual_review`, governance
artifacts represented, known Confluence IDs preserved, crosswalk current.

**Markdown/artifact:** file exists, non-empty, no merge-conflict markers, conservative
secret/private-key/token/SSN/account-number detection (value never printed), relative-link
resolution, front-matter parseability + required fields + valid status + AD-5-not-published,
generated-file warning retained, generated files current.

**Ownership/review (register-derived):** `owner`/`reviewer`/`status`/`review_cycle`/`next_review`
present; AD-5 rows keep reviewer `UNFILLED` (business owner ≠ regulatory certifier); compliance-gated
content never `published`.

**Canonical-source:** git/generated rows name a `repository_path`; confluence rows name a page id
(or `TBD`); legacy rows never canonical; unauthorized areas rejected; crosswalk consistent with
`pages.yml` (via `--check`).

## 5. Error vs warning classification

- **ERRORS** (fail `--strict`): register-integrity failure, stale crosswalk, removed generated
  warning, merge markers, secrets/keys/tokens/SSN, unparseable/invalid front matter, AD-5+published,
  AD-5 reviewer not `UNFILLED`.
- **WARNINGS** (advisory; do not fail `--strict`): empty file, broken relative link, missing
  ownership/front-matter field, account-number-like run, git row missing `repository_path`,
  confluence row missing id.

## 6. Advisory behavior

- **Default:** validates repo-wide, prints errors + warnings, states findings are advisory, **exit 0**.
- **`--changed`:** file-level checks focus on files changed vs a Git base (`GITHUB_BASE_REF` →
  `origin/main` → `main`, via `merge-base`); **register + generated-file integrity still run
  repo-wide**; if the base is unavailable it falls back to all files with a note; **exit 0**.
- **`--strict`:** **non-zero iff ERRORS** (warnings stay 0). For local testing and a future
  authorized enforcement phase — **not** wired as blocking CI in 0.11.0.

## 7. Register-validator reuse & crosswalk currency

The checker calls `scripts/registers/validate_register.py` and `scripts/registers/gen_crosswalk.py
--check` by subprocess and surfaces failures as clear findings (`register_integrity`,
`generated_stale`). **No P3 validation logic was rewritten or weakened.**

## 8. PR-template changes

New `.github/pull_request_template.md` with concise sections — Summary, Scope, Documentation impact,
Canonical-source impact, Publication Register impact, Confluence impact, Taxonomy impact, Security &
client-data review, AD-5/compliance impact, Testing & validation, Rollback, Reviewer checklist — and a
9-item DoD checklist (canonical home; `pages.yml` updated; crosswalk refreshed; DoD checker run; no
secrets/client data; Confluence changes separately authorized; no regulated content published under an
active gate; Michael Shelton's approval ≠ regulatory certification; legacy pages stay non-canonical).
It ends with the D6 advisory notice. Kept practical, not an exhaustive compliance form.

## 9. Workflow triggers & permissions

`documentation-advisory.yml` runs on `pull_request` touching `docs/**`, `governance/**`,
`scripts/registers/**`, `scripts/docs/**`, the PR template, or the workflow itself, plus
`workflow_dispatch` (manual). **Permissions: `contents: read`** (least privilege) — no writes, no
Confluence, no pushes. Python 3.12; installs only `pyyaml`; runs the checker in **advisory
`--changed`** mode; output is always published in the job log inside a log group.

## 10. Why findings do not block PRs

In advisory (default/`--changed`) mode the checker **always exits 0** after reporting, so the workflow
step passes even when documentation ERRORS are present — the findings are visible in the log but do
not fail the PR (decision D6). The step uses **no `continue-on-error`**, so a genuine execution
failure (checker crash, missing dependency) still yields a non-zero exit and a **visible workflow
failure**. Thus documentation findings never block, while real tooling/infra failures remain loud.
`--strict` is never used in CI.

## 11. Known Confluence-ID count used by validation

The register validator asserts **20** known Confluence IDs appear exactly once with their intended
status (status-fidelity check), grouped:

| Category | Count | IDs |
|---|---|---|
| Operations Manual nodes | **8** | 28966913, 28835861, 28999681, 29032449, 29032469, 28868631, 28835881, 28868651 |
| Area Shell template pages | **3** | 28966933, 28999701, 28835901 |
| Insurance landing page | **1** | 28770305 (`INS-EXEC-01`, status `published`) |
| Insurance operational child pages | **5** | 28803073, 28835841, 28868609, 28901377, 28901397 (status `published`) |
| Benefits draft pages | **3** | 27951106, 27983873, 27918338 (status `draft`) |
| **Total (status-fidelity check)** | **20** | — |

**Legacy Atlas pages (23) are NOT part of this 20-ID status-fidelity count.** They are validated by a
**separate** rule that asserts exactly 23 rows are `legacy_unresolved` / `manual_review` and
non-canonical. So: 20 known-good IDs (fidelity) + 23 legacy (non-canonical) are checked by distinct
rules and are not conflated.

## 12. Tests performed (mutation-and-restore; all restored)

| # | Test | Mode | Result |
|---|---|---|---|
| 1 | clean repo | default | 0 errors, 0 warnings, **exit 0** |
| 2 | clean repo | `--changed` | 0/0, **exit 0** |
| 3 | clean repo | `--strict` | 0 errors, **exit 0** |
| 4 | invalid status | `--strict` | `register_integrity` ERROR, **exit 1** |
| 5 | AD-5 + `published` | `--strict` | `register_integrity` ERROR, **exit 1** |
| 6 | stale generated crosswalk | `--strict` | `generated_stale` ERROR, **exit 1** |
| 7 | unresolved merge marker | `--strict` | `merge_conflict_marker` ERROR, **exit 1** |
| 8 | broken relative link | `--strict` | `broken_relative_link` WARNING, **exit 0** |
| 9 | secret pattern (safe fake AWS-style key) | `--strict` | `aws_access_key` ERROR, **exit 1** |
| 10 | legacy record marked canonical | `--strict` | `register_integrity` ERROR, **exit 1** |
| 11 | unauthorized `MANUAL` area | `--strict` | `register_integrity` ERROR, **exit 1** |
| 12 | incomplete Hybrid coverage | `--strict` | `register_integrity` ERROR, **exit 1** |

All mutations used temporary copies / temp files and were **restored**; post-test `git status`
showed only the intended P4 files, and the register re-validated clean. No invalid test data remains
in tracked artifacts.

## 13. Sample advisory output (clean repo, default)

```
Documentation Definition-of-Done — ADVISORY report (Release 0.11.0 · P4)
No findings. Documentation state is clean.
Summary: 0 error(s), 0 warning(s).
ADVISORY mode: findings are advisory only — exit 0 (documentation does not block the PR).
```

## 14. Sample strict-mode result (a mutation)

```
ERRORS (1):
  [register_integrity] docs/registers/pages.yml: P3 register validation failed: ...
Summary: 1 error(s), 0 warning(s).
STRICT mode: exiting non-zero due to ERRORS (local/testing only; not blocking CI).   # exit 1
```

## 15. Security & sensitive-data handling

Conservative regex patterns for private keys, AWS-style keys, assigned secrets/tokens, bearer tokens,
SSNs, and account-number-like runs. **A matched value is never printed** — only filename + rule +
masked note. Scanning is scoped to the documentation surface (not `scripts/`), avoiding self-matches.
This is a lightweight guardrail, **not** a DLP system (per instruction).

## 16. AD-5 treatment

The checker enforces (as ERRORS) that any register row with an active `compliance_gate` is never
`status: published`, and that AD-5 rows keep reviewer `UNFILLED`. The PR template requires confirming
no regulated content is published under an active gate and that Michael Shelton's business approval is
not represented as regulatory certification. **AD-5 remains open; no reviewer invented; no regulated
rule set authored.**

## 17. Deviations

1. **Source filename.** The task cited `docs/documentation-framework/04-OWNERSHIP-AND-REVIEW-MODEL.md`;
   the actual file is **`04-GAP-ANALYSIS.md`** (there is no ownership-and-review-model file). The
   ownership/review model is defined in `02-DOCUMENT-TYPE-TEMPLATES.md` (shared front-matter) and
   `06-SYNC-AND-DEFINITION-OF-DONE.md`; those were used.
2. **PyYAML installed in-workflow** (not added to `requirements.txt`) to avoid touching the app's
   pinned dependency set / the blocking build. Documented in §2.
3. **Checker under `scripts/docs/`** as specified — consistent with the existing `scripts/registers/`
   convention. No deviation.

## 18. Unresolved issues

- **Legacy reconciliation still pending** — 23 pages remain `manual_review`; the checker/PR-template
  enforce they stay non-canonical, but the disposition decision is separate.
- **PyYAML is not a declared repo dependency** — the advisory workflow installs it; a future phase may
  formalize it if the register tooling becomes part of the blocking build.
- **Compliance reviewer `UNFILLED` (AD-5)** — unchanged, not required to be filled in 0.11.0.

## 19. Recommendation for P5

1. P4 is **complete**: advisory checker + PR template + non-blocking workflow, all tested; clean repo
   passes strict; no blocking CI introduced.
2. **Proceed to P5 (RC validation)**: run the full 0.11.0 validation sweep (register + crosswalk +
   DoD advisory + existing `build` CI), audit completeness (areas, coverage, governance, AD-5), and a
   documentation-consistency pass, producing `docs/releases/0.11.0/` RC artifacts.
3. Blocking enforcement of the DoD (advisory → blocking, `release.sh` precondition) stays **Phase E** —
   not 0.11.0.

---

**Stopping after the P4 report.** Awaiting explicit approval before beginning the next Release 0.11.0
phase.
