# Release 0.11.0 — Change Summary

_Release-facing summary of the **Documentation Foundation & Governance** release (Roadmap Phase A).
Documentation-only: no application code or database migrations changed. Candidate `5e394eb`._

## What Release 0.11.0 delivers

- **Architecture foundation.** Ratified the 360 Wealth Consulting Operations Manual framework and
  recorded the P0 architecture decisions **D1–D10** (register format, taxonomy scope, hybrid union,
  status enum, governance skeleton, advisory-only DoD, AD-5 machine-check, taxonomy reconciliation).

- **Confluence skeleton.** Provisioned the 8 top-level Operations Manual nodes
  (`00/01/10/20/30/40/80/90`) and 3 reusable **Area Shell template pages** (Software / Infrastructure
  / Business Operations) in space 3WCO — structure only, no operational content. The Area Shells are
  **template pages, not native Confluence templates**.

- **Git governance skeleton.** Added `governance/` (README, CONTRIBUTING, and `policies/ runbooks/
  dr/ controls/ inventory/ calendar/` READMEs) as the git-canonical home for future policies,
  runbooks, DR/BCP, controls, inventory, and calendar data — **skeleton and guidance only**.

- **Canonical Publication Register.** Created `docs/registers/pages.yml` (554 rows) as the
  machine-readable source of truth, with a documented schema, controlled enums, deterministic
  generator, and a validator. Covers all **26 framework areas + `SHARED` + `GOV`** with the complete
  per-profile document-type set (Hybrid areas carry the 27-type union).

- **Taxonomy migration (D10).** Adopted the framework area-code taxonomy in the register; the former
  crosswalk section letters (A–N) are recorded and preserved as `legacy_identifier`.

- **Generated crosswalk.** `docs/DOCUMENTATION_CROSSWALK.md` is now a **generated view** of the
  register (do-not-edit header; reproducible — regeneration yields no diff).

- **Legacy-page inventory (non-canonical).** Inventoried the 23 pre-existing 360OS/Atlas pages as
  register rows that are **non-canonical** and `manual_review`, each with a likely framework
  destination. **No legacy page was moved, renamed, merged, archived, relabeled, edited, or
  re-parented.**

- **Advisory documentation DoD checker.** `scripts/docs/check_documentation_dod.py` validates register
  integrity (reusing the P3 validator + generator) and documentation quality (secrets, links, front
  matter, ownership). **Advisory** by default; `--strict` is for local testing only.

- **PR template.** `.github/pull_request_template.md` with a concise DoD checklist.

- **Advisory workflow.** `.github/workflows/documentation-advisory.yml` runs the checker on
  documentation PRs in **advisory, non-blocking** mode (least-privilege, read-only).

## Compliance boundary

- **AD-5 remains OPEN.** The accountable compliance reviewer is **`UNFILLED`**. Regulated insurance
  rule sets — **suitability, replacement/1035, licensing, continuing-education** — remain **blocked**
  and unpublished; the register enforces `compliance_gate: AD-5 ⇒ never published`.
- **Michael Shelton is the business owner** for workflow/operational requirements **only** — not
  regulatory certification. No business approval is represented as regulatory approval.

## Deferred work (NOT delivered in 0.11.0)

- **Substantive governance content** (actual policies, runbooks, DR/BCP, controls, inventories) —
  Phase B/D.
- **Legacy 360OS/Atlas reconciliation execution** (move/merge/archive dispositions) — a separate
  approved decision; **only the inventory is complete, not the reconciliation**.
- **Regulated insurance rule sets** — blocked under AD-5 until a named compliance reviewer signs off.
- **Blocking documentation enforcement** (advisory → blocking, `release.sh` docs precondition) —
  Phase E. **Advisory validation in 0.11.0 is not blocking enforcement.**
- **Confluence rendering of git-canonical pages** (push) — Phase E.
- **Full authored area pages** for the 26 areas — later phases under the Definition of Done.

## Known limitations

- The register's coverage rows are mostly `planned` — they define *what should exist*, not authored
  content.
- 6 draft Insurance proposal pages and the shared/register rows carry `TBD` Confluence IDs until
  created.
- PyYAML is not a declared repository dependency; the advisory workflow installs it directly.
- The legacy reconciliation dispositions are recommendations pending an approval decision.
