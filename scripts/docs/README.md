# `scripts/docs/` — Documentation Definition-of-Done checker (advisory)

`check_documentation_dod.py` validates documentation quality and Publication Register integrity for
the 360 Wealth Consulting Operations Manual. **Release 0.11.0 ships it in advisory mode only**
(decision **D6**): it reports findings and does not block. Blocking enforcement is deferred to a
later authorized phase.

## Usage

```bash
python3 scripts/docs/check_documentation_dod.py            # default: advisory, always exit 0
python3 scripts/docs/check_documentation_dod.py --changed  # focus file checks on Git-changed files
python3 scripts/docs/check_documentation_dod.py --strict   # exit non-zero on ERRORS (local/testing)
```

## Modes & exit behavior

| Mode | File-level scope | Register/generated integrity | Exit code |
|---|---|---|---|
| default | all documentation files | repository-wide | **always 0** (advisory) |
| `--changed` | files changed vs a Git base (falls back to all if base unavailable) | repository-wide | **always 0** (advisory) |
| `--strict` | all documentation files | repository-wide | **non-zero iff ERRORS** (warnings stay 0) |

`--strict` is for local testing and a future authorized enforcement phase. It is **not** wired as
blocking CI in Release 0.11.0.

## Errors vs warnings

**ERRORS** (fail `--strict`): register-integrity failure (any P3 validator error), stale generated
crosswalk, removed generated-file warning, merge-conflict markers, secrets / private keys / tokens /
SSN patterns, unparseable front matter, invalid front-matter status, front-matter `compliance_gate`
set with `status: published`, AD-5 register row whose reviewer is not `UNFILLED`.

**WARNINGS** (advisory; do not fail `--strict`): empty file, broken relative link, missing
front-matter/ownership field, account-number-like digit run, git/generated row missing
`repository_path`, confluence row missing `confluence_page_id`.

## Reuse of P3 tooling (no duplication)

Register validation is **not** reimplemented. The checker invokes, by subprocess:

- `scripts/registers/validate_register.py` — every register invariant (schema, areas, profiles,
  doc-types, statuses, canonical-source, unique `page_id`, unique canonical identity, AD-5 invariant,
  complete profile/doc-type coverage, no duplicate Hybrid rows, all framework areas + `SHARED`/`GOV`,
  legacy non-canonical/`manual_review`, governance artifacts, known Confluence IDs, crosswalk
  currency).
- `scripts/registers/gen_crosswalk.py --check` — the generated crosswalk is current.

Register-validator failures surface as a single `register_integrity` ERROR with the captured detail.

## Secret handling

Detection uses conservative patterns and **never prints a matched value** — only the filename, rule
name, and a masked note. The checker scans `docs/**` and `governance/**` (not `scripts/`), so its own
patterns are not self-matched. This is a lightweight guardrail, **not** a DLP system.

## CI

`.github/workflows/documentation-advisory.yml` runs the checker in **advisory** mode (never
`--strict`) on documentation/register/governance/tooling PRs and via manual dispatch. It publishes
the output in the job log and does **not** fail the PR on documentation findings; only genuine
workflow/tool execution failures fail the job.
