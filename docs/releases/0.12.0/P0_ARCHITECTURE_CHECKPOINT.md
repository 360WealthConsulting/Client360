# Release 0.12.0 — P0 Architecture Checkpoint (PROPOSED)

_Architecture validation for the proposed Release 0.12 scope (`RELEASE_0.12.0_PLAN.md` /
`RELEASE_SCOPE.md`). Planning/analysis only — no implementation. `v0.11.0` immutable; **decisions
D1–D10 unchanged**; no app/migration/Confluence changes; AD-5 unresolved._

## 1. Scope of this checkpoint

Validate that the proposed 0.12 theme — **operationalize the foundation** (legacy reconciliation,
Confluence migration, `docs_sync` publishing pipeline, advisory-only) — is consistent with the
framework, the roadmap, and the 0.11.0 architecture, **without changing any approved decision**, and
identify the architectural risks and conditions before implementation.

## 2. Alignment with the framework & roadmap

- **Roadmap mapping.** 0.11.0 = Phase A. 0.12 as proposed = **Phase E groundwork** (automation /
  self-sustaining sync — `06-SYNC-AND-DEFINITION-OF-DONE.md` §3 `docs_sync verify|push|report`) **plus
  the deferred 0.11.0 close-out** (legacy reconciliation, Confluence migration). This is a coherent,
  bounded slice; it does **not** front-run Phase B authoring (kept out of scope for 0.13).
- **Register-driven publishing** realizes decision **D1** (pages.yml canonical → Confluence as a
  generated/rendered view) — the publishing half that 0.11.0 deferred. No change to D1.
- **No new framework areas**; the taxonomy stays **26 + `SHARED` + `GOV`** (D2/D10 unchanged). Legacy
  pages reconcile *into* existing areas; they do not create areas.

## 3. Decisions D1–D10 — unchanged (confirmed)

| Decision | 0.12 effect | Change? |
|---|---|---|
| D1 register canonical + generated crosswalk | `docs_sync` renders it to Confluence | **No** (operationalized) |
| D2 areas = 26 + SHARED + GOV | reconciliation maps legacy into these | No |
| D3 hybrid union | unchanged | No |
| D4 status enum | `docs_sync` keys off it (publishes only `published`-eligible git rows) | No |
| D5 governance skeleton | authoring deferred to 0.13 | No |
| D6 advisory only | **stays advisory** in 0.12 (blocking still later) | No |
| D7 semantic id + TBD | migration backfills real `confluence_page_id` | No |
| D8 parallelism | phase practice | No |
| D9 AD-5 invariant | `docs_sync` refuses to publish gated rows | No (enforced) |
| D10 taxonomy migration | labels applied during Confluence migration | No |

**No architecture decision is modified.** 0.12 extends their enforcement into a publishing pipeline.

## 4. New 0.12 architecture decisions (proposed — E-series, additive)

| ID | Decision | Rationale |
|---|---|---|
| **E1** | `docs_sync push` publishes **only git-canonical, non-gated** rows whose status is publish-eligible; it **never** publishes `draft` / `needs_review` / `compliance_gate: AD-5` rows | Enforces D6/D9 at the publishing boundary; prevents accidental draft/regulated publication |
| **E2** | Confluence migration **preserves page IDs** — re-parent and label only, never recreate; record old/new parents | Protects the published 0.10.0 Insurance pages and their URLs |
| **E3** | Legacy reconciliation is **archive-not-delete** (to the existing `🗄️ Atlas Archive`), per-page approved, reversible | No destructive action on real pages |
| **E4** | Publishing is **idempotent + diff-driven + dry-run-first**; matches by `confluence_page_id` | No duplicates / clobbering |
| **E5** | `docs_sync` **reuses** `validate_register.py` + `gen_crosswalk.py` (no re-implementation); ships as repo tooling; installs PyYAML in the advisory workflow (no app-dependency change) | Consistent with 0.11.0 P3/P4 tooling model |

These are **new** decisions for 0.12, not amendments to D1–D10.

## 5. Confluence write-authorization boundary (key scope shift)

0.11.0 was strictly **read-only** to Confluence. **0.12 begins authorized Confluence writes** (legacy
reconciliation execution, Insurance re-parenting, `docs_sync push`). This is the principal
architectural change and is gated by:
- an **approved legacy reconciliation decision** (per-page disposition) before any move/merge/archive;
- **ID-preserving** re-parent/label operations (E2);
- **dry-run-first, idempotent** publishing (E4);
- the **publish-eligibility filter** (E1) so no draft/regulated page is ever written as published.

## 6. Security / compliance boundary

- **AD-5 unchanged and unresolved.** No regulated content authored or published; `docs_sync` enforces
  the D9 invariant. Compliance reviewer stays `UNFILLED`; Michael Shelton = business/operational owner
  only. 0.12 does **not** resolve AD-5 or invent approvals.
- **No secrets / client data** enter the register or `docs_sync`; publishing renders documentation
  metadata/summaries + links only.

## 7. Risks (architecture-level)

Carried from `RELEASE_0.12.0_PLAN.md` §5 — highest: **R1** (mis-parent a published page), **R2**
(destroy a legacy page), **R3** (`push` duplicates/clobbers), **R5** (publish a draft/regulated page).
All are mitigated by E1–E4 (ID preservation, archive-not-delete, idempotent dry-run, publish-eligibility
filter). None requires changing D1–D10.

## 8. Dependencies & conditions

- **Prerequisite:** an **approved legacy reconciliation decision** before P1 execution (the one hard
  gate).
- Confluence MCP write access (available); register + tooling (delivered in 0.11.0); CI green on
  `v0.11.0`.
- **AD-5 (external, UNFILLED)** blocks only regulated content — out of scope, not a 0.12 blocker.

## 9. Recommendation

**PROCEED TO PLAN-APPROVAL for 0.12 as scoped — with conditions.** The proposed scope is
architecturally sound, maps cleanly to roadmap Phase E groundwork + 0.11.0 close-out, changes no
approved decision (D1–D10 intact), and stays documentation/governance-focused and non-regulated. It
adds five additive publishing-safety decisions (E1–E5).

**Conditions before P1 implementation:**
1. Approve the 0.12 scope (`RELEASE_0.12.0_PLAN.md`).
2. Approve decisions **E1–E5** (publishing-safety model).
3. Approve the **legacy reconciliation decision** (per-page dispositions) — the gate for all
   Confluence writes.
4. Reaffirm: DoD stays **advisory** (D6); AD-5 stays gated; `v0.11.0` immutable.

**Recommended first milestone:** **0.12-P1 — Legacy reconciliation (decision + execution)**, since it
gates the Confluence migration and publishing; `docs_sync` (P3) may be built in parallel in dry-run.

---

_Checkpoint for review. No implementation. Awaiting approval before Phase P1._
