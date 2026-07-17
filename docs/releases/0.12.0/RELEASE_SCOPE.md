# Release 0.12.0 — Scope Definition (PROPOSED)

_Companion to `RELEASE_0.12.0_PLAN.md`. Planning only — nothing implemented. `v0.11.0` immutable;
D1–D10 unchanged; no app/migration/Confluence changes; AD-5 unresolved._

## Theme

**Operationalize the Documentation Foundation** — turn the canonical register into a working
publishing system: reconcile the legacy pages, migrate Confluence, and build the documentation
publishing pipeline (`docs_sync`). Documentation/governance/tooling only; non-regulated.

## Current-state baseline (why this scope)

- Register `docs/registers/pages.yml`: **554 rows** — **489 `planned`** (unauthored), 23 `draft`, 23
  `needs_review` (legacy), 19 `published`.
- **23 legacy 360OS/Atlas pages** remain `manual_review` (reconciliation **decision pending**).
- Register **does not yet publish to Confluence** (no `docs_sync` tool).
- 0.10.0 Insurance pages are **not yet re-parented** under the manual hierarchy; skeleton pages carry
  **no `area:` labels** yet.
- DoD is **advisory** (D6); CI `build` green on `v0.11.0`.

## In scope

| # | Item | Deliverable |
|---|---|---|
| S1 | Legacy Atlas reconciliation decision + execution | Approved per-page disposition; executed archive/merge/link/move; register `reconciliation_status` resolved (archive-only, no deletion) |
| S2 | Confluence migration | Insurance landing + 5 children re-parented under node 10 Insurance (**IDs preserved**); `area/type/profile` labels applied; old/new parents recorded |
| S3 | `scripts/docs/docs_sync.py` (or `scripts/registers/`) | `verify` / `push` / `report`; renders **git-canonical** rows + generated crosswalk to Confluence; idempotent, diff-driven, dry-run-first; reuses P3 validator/generator |
| S4 | Advisory integration | `docs_sync verify` wired into the existing non-blocking workflow; **DoD stays advisory (D6)** |
| S5 | Register maintenance | Backfill `confluence_page_id` for created/migrated pages; determinism preserved (crosswalk regen no-diff) |

## Out of scope (deferred)

| # | Item | Deferred to | Reason |
|---|---|---|---|
| O1 | Risk-floor content authoring (DR/BCP, Vendor, Controls, Asset, policies) | **0.13 (Phase B)** | Heavy authoring; better after the publishing pipeline exists |
| O2 | Phase C/D area content across the 26 areas | Later phases | Sequenced by priority under the DoD |
| O3 | Blocking documentation enforcement (advisory → blocking) | Later authorized phase | D6 keeps 0.12 advisory |
| O4 | Regulated insurance content (suitability/replacement/licensing/CE) | Blocked | **AD-5** |
| O5 | Resolving AD-5 / naming compliance reviewer | External | Not a code action |
| O6 | Application features / database migrations | Separate product release | 0.12 is docs/tooling only |
| O7 | Any modification to 0.11.0 artifacts / `v0.11.0` | N/A | Immutable (defect-fix only) |

## Guardrails (carried from 0.11.0)

- One canonical home per page; `docs_sync` publishes **only git-canonical, non-gated** rows.
- **Never publish** `draft` / `needs_review` / `compliance_gate: AD-5` rows.
- Legacy pages: **archive, never delete**; a legacy page mapping to an approved area does not make it
  canonical until reconciled.
- Confluence migration **preserves page IDs**; re-parent/label only, no content rewrite.
- Michael Shelton = business/operational owner only — **not** regulatory certification.
- `v0.11.0` and 0.10.0 tags/artifacts remain intact.

## Definition of "done" for 0.12 scope

Legacy reconciliation executed (archive-only); Insurance pages migrated with IDs preserved; labels
applied; `docs_sync` implemented (idempotent, dry-run-first, publishes only git-canonical non-gated
rows); DoD still advisory; register validator + crosswalk determinism pass; no draft/regulated page
published; AD-5 gated; RC-validated; `v0.12.0` tagged with a dated CHANGELOG entry.
