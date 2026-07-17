# Release 0.12.0 — Plan (PROPOSED)

_Status: **PLANNING** (proposed roadmap; not approved). Created 2026-07-17. Predecessor: **0.11.0 —
Documentation Foundation**, released and tagged `v0.11.0` (`main` @ `8cb7868`). Planning branch
`release/0.12.0` (cut from `main`)._

> **Planning only.** No features implemented; no 0.11.0 artifact modified; `v0.11.0` treated as
> immutable; architecture decisions **D1–D10 unchanged**; no application code, migrations, or
> Confluence changes; no drafts published; **AD-5 not resolved**; no compliance approvals invented.

---

## 1. Executive summary

Release 0.11.0 delivered the documentation **foundation** (framework, Confluence skeleton, Git
governance skeleton, canonical Publication Register of 554 rows, generated crosswalk, D10 taxonomy
migration, advisory DoD). But the foundation is **inert**: **489 of 554 register rows are `planned`**
(unauthored), the **23 legacy 360OS/Atlas pages remain unreconciled** (`manual_review`), the register
does **not yet publish to Confluence**, and the 0.10.0 Insurance pages are not yet re-parented under
the manual hierarchy.

**Proposed theme for 0.12: "Operationalize the Foundation."** Make the register actually *drive*
Confluence: resolve the legacy reconciliation, execute the Confluence migration (re-parent + label
existing pages), and build the **documentation publishing/automation pipeline** (`scripts/docs_sync.py`
— roadmap Phase E groundwork), while keeping the DoD **advisory** (D6). This is the natural,
bounded next step that closes 0.11.0's deferred items and turns the foundation into a working
publishing system — **documentation/governance-focused and non-regulated** (AD-5 stays gated).

Heavy **content authoring** (roadmap Phase B risk floor: DR/BCP, Vendor, Controls, Asset, policies)
is proposed to follow in **0.13**, once the publishing pipeline exists so authored content
auto-renders. (See §10 for the doc-focused-vs-operational recommendation.)

## 2. Scope (proposed in-scope)

1. **Legacy Atlas reconciliation — decision + execution.** Approve a disposition per the 23 pages
   (`retain / link / move / merge / archive`), then execute in Confluence (using the existing
   `🗄️ Atlas Archive`), and update register rows from `manual_review` to a resolved state. *(First
   authorized Confluence writes since 0.10.0.)*
2. **Confluence migration.** Re-parent the Insurance landing + 5 published child pages under an
   Insurance area page in `10 · Client-Facing Operations` (**preserving page IDs**); apply
   `area:`/`type:`/`profile:` labels to the skeleton nodes, templates, and published pages; record
   old/new parents. No content rewrite.
3. **Documentation publishing pipeline (Phase E groundwork).** Implement `scripts/docs_sync.py`
   (`verify` / `push` / `report`) that renders **git-canonical** register rows + the generated
   crosswalk into Confluence (idempotent, diff-driven), reusing the P3 validator/generator. **Push is
   opt-in and dry-run-first.**
4. **DoD gate remains advisory (D6).** `docs_sync verify` integrates with the existing advisory
   checker; **no blocking enforcement in 0.12** (flip-to-blocking stays a later authorized phase).
5. **Register maintenance.** Fill `confluence_page_id` for pages created/migrated; keep coverage rows
   accurate; maintain determinism (crosswalk regen no-diff).

## 3. Out-of-scope

- ❌ **Substantive risk-floor authoring** (DR/BCP plans, Vendor register content, Controls register,
  Asset inventory data, firm policies) — roadmap **Phase B**, proposed for **0.13**.
- ❌ **Phase C/D** technical/operational content authoring across the 26 areas.
- ❌ **Blocking documentation enforcement** — stays advisory (D6); Phase E flip is later.
- ❌ **Any regulated insurance content** (suitability, replacement/1035, licensing/CE validation) —
  AD-5-blocked.
- ❌ **Resolving AD-5 / naming a compliance reviewer** — external, not a code action.
- ❌ **Application features / migrations** — 0.12 is documentation/governance/tooling only.
- ❌ **Modifying 0.11.0 artifacts or `v0.11.0`** — immutable (defect-fix only).

## 4. Proposed phases

| Phase | Title | Work | Confluence writes? |
|---|---|---|---|
| **P0** | Architecture checkpoint | Validate 0.12 scope vs framework/roadmap; confirm no D1–D10 change; risk/dependency review | No |
| **P1** | Legacy reconciliation (decision + execution) | Approve dispositions for 23 pages; execute archive/merge/link/move; update register `reconciliation_status` | **Yes** (first authorized) |
| **P2** | Confluence migration | Re-parent Insurance landing + 5 children under node 10 Insurance (IDs preserved); apply `area/type/profile` labels; record old/new parents | **Yes** |
| **P3** | Publishing pipeline (`docs_sync.py`) | `verify`/`push`/`report`; render git-canonical rows + crosswalk to Confluence, idempotent, dry-run-first; reuse P3 tooling | **Yes** (push, opt-in) |
| **P4** | Advisory integration + register cleanup | Wire `docs_sync verify` into the advisory workflow (still non-blocking); backfill `confluence_page_id`; determinism checks | No |
| **P5** | RC validation & release | Full sweep (register/crosswalk/DoD/CI); acceptance matrix; sign-off; merge → tag `v0.12.0` | No |

## 5. Risks

| ID | Risk | Sev | Mitigation |
|---|---|---|---|
| R1 | Confluence migration mis-parents / breaks a published Insurance page | High | Preserve page IDs (re-parent only, never recreate); dry-run; record old/new parents; verify read-only after each move |
| R2 | Legacy reconciliation destroys a real page | High | Archive (never delete) to the existing Atlas Archive; require explicit per-page disposition approval; reversible (re-parent back) |
| R3 | `docs_sync push` creates duplicates / clobbers content | High | Idempotent + diff-driven; dry-run default; only push **git-canonical** rows; match by `confluence_page_id` |
| R4 | Scope creep into Phase B authoring | Med | Authoring explicitly out-of-scope (§3); P4 is integration, not content |
| R5 | Accidental publication of a draft/regulated page | High | `docs_sync` refuses to publish `draft`/`needs_review`/`compliance_gate` rows; AD-5 invariant enforced |
| R6 | Legacy canonical-home conflicts (DR/Tech/Compliance) surface during migration | Med | Those become links, not canonical copies (one-home rule); flag for Phase B |
| R7 | PyYAML/dep not in CI for `docs_sync` | Low | Install in the (existing advisory) workflow; do not touch app deps |
| R8 | 0.11.0 regression / `v0.11.0` disturbed | Low | `v0.11.0` immutable; 0.12 on its own branch; defect-fix only |

## 6. Dependencies

| Dependency | Type | Status |
|---|---|---|
| **Legacy reconciliation decision** | Approval | **Pending** — prerequisite for P1/P2 |
| Confluence MCP write access to space 3WCO | External | Available (used in 0.10.0/P1) |
| Canonical register `pages.yml` + validator/generator | Internal (0.11.0) | Delivered |
| Existing advisory workflow + PR template | Internal (0.11.0) | Delivered |
| Confirmed page IDs (nodes, templates, Insurance, Benefits, legacy) | Internal | Verified in 0.11.0 P5 |
| **Compliance reviewer (AD-5)** | External | **UNFILLED** — blocks only regulated content (out of scope) |
| CI `build` gate (Python 3.12 + Postgres) | CI | Green on `v0.11.0` |

## 7. Acceptance criteria

- All 23 legacy pages have an **approved, executed disposition**; register `reconciliation_status`
  updated; **no page deleted** (archive-only).
- Insurance landing + 5 children **re-parented under node 10 Insurance with IDs preserved**; old/new
  parents recorded; pages still `published`, content unchanged, AD-5 banners intact.
- `area/type/profile` labels applied to skeleton + published pages.
- `docs_sync.py` `verify`/`push`/`report` implemented, idempotent, dry-run-first; a push run creates
  **no duplicates** and publishes **only git-canonical, non-gated** rows.
- DoD remains **advisory** (no blocking CI); register validator + crosswalk determinism still pass.
- No application/migration change; **no draft/regulated page published**; AD-5 unresolved and gated;
  `v0.11.0` and 0.10.0 intact.
- RC-validated; acceptance matrix complete; signed off; `v0.12.0` tagged with a dated CHANGELOG entry.

## 8. Recommended implementation order

**P0 → P1 → P2 → P3 → P4 → P5**, with the **legacy reconciliation decision (P1) as the gating
prerequisite** for all Confluence work. P2 (migration) depends on P1; P3 (`docs_sync`) can be built in
parallel with P1/P2 (dry-run) but publishes only after P2. Each phase ends with its own validation and
a stop-for-review, mirroring the 0.11.0 cadence.

## 9. Estimated effort per phase

Relative sequence (not calendar), per the roadmap convention. XS < S < M < L.

| Phase | Effort | Critical path? |
|---|---|---|
| P0 — checkpoint | XS | gate |
| P1 — reconciliation | **M** | yes (unblocks Confluence work) |
| P2 — Confluence migration | M | yes |
| P3 — `docs_sync.py` | **L** | **yes (critical path)** |
| P4 — advisory integration + cleanup | S | — |
| P5 — RC + release | S | gate |

Total ≈ one **M–L** documentation/tooling release (comparable to 0.11.0), centered on the `docs_sync`
build (P3).

## 10. Recommendation — documentation-focused vs operational capability

**Recommendation: Release 0.12 should REMAIN documentation/governance-focused** (operationalizing the
foundation) and **should NOT begin operational application-capability implementation.** Rationale:

1. **The roadmap's next phases (B–F) are all documentation/governance/automation**, not application
   features — 0.12 continues that program.
2. **AD-5 blocks the primary regulated operational capability** (insurance suitability/replacement/
   licensing/CE); starting regulated app work is impossible without a named compliance reviewer.
3. **The foundation is inert until it publishes** — building `docs_sync` (the publishing pipeline)
   and migrating Confluence is the highest-leverage next step and directly enables all later authoring
   to auto-render.
4. **0.11.0's deferred items (legacy reconciliation, Confluence migration) are natural continuity** —
   closing them in 0.12 keeps the release history coherent.
5. **Risk-floor authoring (Phase B) is better sequenced after the pipeline exists** (0.13), so authored
   DR/BCP/Vendor/Controls content publishes automatically under the DoD.

If the business instead needs a **non-regulated application capability** delivered sooner, that should
be scoped as a **separate product release** (not 0.12), with its own architecture checkpoint — and it
would still inherit the 0.12 documentation pipeline for its docs.

---

_Proposed roadmap for review. No implementation. Awaiting approval before Phase P1._
