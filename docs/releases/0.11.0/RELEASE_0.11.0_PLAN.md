# Release 0.11.0 — Plan

_Status: **PLANNING** (new development cycle). Created 2026-07-17. Predecessor: **0.10.0 —
Insurance Operations**, released and closed 2026-07-17 (`main` at `v0.10.0`, merge `5ba60a2`)._

> **Planning only.** No functionality is implemented, no released code is modified, no branch is
> created by this document. Scope is a proposal for the architecture checkpoint; it becomes
> binding only when approved there. Release 0.11.0 is a **new development cycle** with its own
> architecture checkpoints, validation gates, RC process, release approval, and release
> documentation — independent of 0.10.0.

---

## 0. Theme & source of truth

**Theme: Documentation Foundation & Governance — Roadmap Phase A.**

`docs/documentation-framework/05-IMPLEMENTATION-ROADMAP.md` is the source of truth for sequencing.
It states plainly: *"Structure and enforcement first (cheap, high-leverage), then close the
operational risk floor, then backfill by priority, then automate,"* and *"Phase A is a standalone
docs release."* Phase A **unblocks everything** downstream, is low-risk, requires **no released-code
change**, and is the milestone already recorded as *next* in `PROJECT_STATUS.md`. It is therefore
the correct spine for the first post-0.10.0 cycle.

Phase A also absorbs the single largest item **explicitly deferred out of 0.10.0** — promoting
`DOCUMENTATION_CROSSWALK.md` to the full 26-area **Publication Register** — placing it where the
roadmap always intended it, rather than back-porting it into the closed release.

This cycle is deliberately **not** a regulated-insurance cycle: all regulated insurance work
remains blocked by **AD-5** (§8) and cannot enter an RC gate until a named compliance reviewer and
approved sign-off exist. Non-regulated product work may proceed **alongside** Phase A under the
roadmap's cadence-coupling rule (each ordinary development phase closes its own area's doc gaps as
part of Definition of Done), but the committed 0.11.0 spine is Phase A.

---

## 1. Objectives

1. **Approve and instantiate the framework** — ratify the 360 Wealth Consulting Operations Manual
   framework (`docs/documentation-framework/`) as the operative standard and stand up its structure.
2. **Provision the Confluence space skeleton** — top-level nodes `00, 01, 10, 20, 30, 40, 80, 90`
   and an **Area Shell template per profile** (Software / Infrastructure / Business Operations).
3. **Load the Template Library** (framework deliverable 2) as reusable Confluence templates.
4. **Add a Git `governance/` tree** for git-canonical operational artifacts (policies, runbooks,
   DR/BCP, controls register, operating-calendar data) — structure and READMEs, not full content.
5. **Promote the Publication Register** — expand `DOCUMENTATION_CROSSWALK.md` to all **26 areas ×
   each area profile's document types**, status seeded from the Capability Map. *(The item deferred
   from 0.10.0.)*
6. **Wire the Definition-of-Done docs gate (advisory)** — add the DoD checklist to the PR template
   and an **advisory** (non-blocking) docs gate; assign an owner/reviewer per area.
7. **Exit criterion (Phase A):** every area has a page skeleton, a register row, and an owner; the
   DoD checklist is visible on PRs.

---

## 2. Scope (in)

- Confluence space provisioning: node pages + three profile Area Shell templates + Template Library
  import (idempotent, re-runnable; documented steps).
- `governance/` directory scaffold in the repo: subtree layout, README per node, ownership front
  matter, and a short CONTRIBUTING pointer — **structure only**, no authored policy content.
- Full Publication Register in `DOCUMENTATION_CROSSWALK.md`: 26 area rows × profile document types,
  each with owner, canonical home (Git vs Confluence), status seeded from the Capability Map, and
  AD-5 flags carried onto regulated rows.
- PR-template DoD checklist + advisory docs-gate script (report-only; exits 0). No blocking.
- Per-area owner/reviewer assignment table.
- Release documentation for 0.11.0 itself (CHANGELOG entry, this plan → architecture checkpoint →
  RC validation → approval, following the established process).

---

## 3. Explicit non-goals

- ❌ **No `scripts/docs_sync.py`** and **no blocking docs gate** — that is **Phase E**, not this
  cycle. The 0.11.0 gate is advisory only.
- ❌ **No operational risk-floor content** (Asset/Config inventory, DR/BCP runbooks, Vendor/Contract
  register, Controls register, IR playbook) — that is **Phase B**, a separate focused initiative.
- ❌ **No Phase C/D authored area content** beyond skeletons and register rows (no new SOPs, user
  guides, or generated Architecture/Data-Model pages in this cycle).
- ❌ **No regulated insurance functionality** of any kind (suitability, replacement/1035, licensing/
  CE **validation**, compliance approvals, sale/issue blocking) — AD-5-blocked (§8).
- ❌ **No changes to released 0.10.0 code** except a critical bug fix, which would ship as its own
  patch, not through this plan.
- ❌ **No back-porting** of deferred roadmap work into 0.10.0.
- ❌ **No publication** of any Confluence page describing AD-5-gated or not-yet-RC-validated
  functionality; the 7 held Insurance draft pages stay draft.
- ❌ **No new application schema/migration** is anticipated (Phase A is documentation/governance).

---

## 4. Dependencies

| Dependency | Type | Status |
|---|---|---|
| Framework approval (`docs/documentation-framework/`) | Internal decision | Approved in principle; formal ratification is Objective 1 |
| Atlassian/Confluence MCP access to space **3WCO** (`21266437`) | External system | Available (used to publish 0.10.0 pages) |
| Template Library content (framework deliverable 2) | Internal artifact | Exists in `docs/documentation-framework/`; needs import as templates |
| Capability Map (framework deliverable 3) | Internal artifact | Exists; seeds Register status |
| `DOCUMENTATION_CROSSWALK.md` current state | Internal artifact | Exists (Benefits §2, Insurance §3 drafted); base for promotion |
| Per-area owners | People | Michael Shelton = business owner across areas; **compliance reviewer UNFILLED (AD-5)** |
| PR template + CI config write access | Repo | Available |
| GitHub branch protection (`build` check on `main`) | CI | In force; unaffected — no code change expected |

---

## 5. Risk register

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Register promotion becomes a content-authoring rabbit hole (drifts into Phase D) | High | Med | Hard scope: **rows + skeletons + owners only**; authored content is out of scope (§3). DoD = a row exists, not a page written. |
| R2 | Confluence space provisioning is partially manual / not idempotent | Med | Med | Script/checklist the node + template creation; make re-runnable; record page IDs in the Register. |
| R3 | AD-5 regulated rows mis-seeded as "publishable" | Med | High | Every regulated row carries an explicit **AD-5-BLOCKED** flag; publication status defaults to draft; boundary reused verbatim from 0.10.0 pages. |
| R4 | Advisory gate mistaken for blocking / blocks delivery | Low | Med | Gate exits 0 (report-only) by design; blocking is deferred to Phase E and called out in the gate's own output. |
| R5 | Ownership gaps — areas without a named owner | Med | Low | Default owner = Michael Shelton (business) with a visible "reviewer TBD" marker; compliance areas flagged UNFILLED (AD-5). |
| R6 | Scope creep pulling Phase B risk-floor content forward | Med | High | Phase B explicitly a non-goal (§3); architecture checkpoint rejects any risk-floor authoring. |
| R7 | Template drift between Git-canonical and Confluence-canonical homes | Low | Med | One canonical home per page rule (Git technical, Confluence operational); Register records the home; no duplication. |
| R8 | 0.10.0 regression surfaced during the cycle | Low | High | Any critical fix ships as a separate 0.10.x patch, never folded into 0.11.0 planning. |

---

## 6. Success criteria

- ✅ Framework formally ratified; recorded in `PROJECT_STATUS.md` and CHANGELOG.
- ✅ Confluence space skeleton live: all node pages + 3 profile Area Shell templates + Template
  Library imported; page IDs recorded in the Register.
- ✅ `governance/` tree scaffolded in-repo with per-node README and ownership front matter.
- ✅ **Publication Register complete:** every one of the 26 areas has ≥1 register row per its
  profile's document types, an owner, a canonical home, a seeded status, and (where regulated) an
  AD-5 flag.
- ✅ DoD checklist present in the PR template; advisory docs gate runs in CI and reports (exit 0).
- ✅ Every area has a named owner/reviewer (or an explicit TBD/UNFILLED marker).
- ✅ **Phase A exit** met: *every area has a page skeleton, a register row, an owner; DoD visible
  on PRs.*
- ✅ Full validation suite green (existing 717 passed / 5 skipped baseline preserved; no code
  regressions); Ruff ratchet, CHANGELOG lint, and migration gates all pass.
- ✅ RC validation + release approval artifacts produced under `docs/releases/0.11.0/`.
- ✅ No AD-5-gated content published; no released 0.10.0 code modified.

---

## 7. Deferred items carried forward from 0.10.0

| Item | Origin | Disposition in 0.11.0 |
|---|---|---|
| Full 26-area × doc-type **Publication Register** | Deferred at 0.10.0 close (Phase A deliverable) | **In scope** — Objective 5. |
| 7 held Insurance Confluence draft pages (Overview, Policy Mgmt, New Business, In-Force Servicing, Reviews & Obligations, Producer Licensing/CE, Roles & Responsibilities) | 0.10.0 register §3 | **Remain draft.** Register rows updated; publication only when their phase is RC-validated and (regulated) AD-5-cleared. |
| Regulated portions of Insurance Phases 2–4 (suitability, replacement/1035, licensing/CE validation, sale/issue blocking) | 0.10.0 AD-5 gate | **Out of scope** — AD-5-blocked (§8). |
| Reserved-but-unused caps `insurance.suitability`, `insurance.sensitive.read` | 0.10.0 | Remain reserved; no activation. |
| Benefits (0.9.11) Confluence pages awaiting page-owner approval | Pre-existing | Register rows carried; approval tracked, not forced by this cycle. |
| Documentation as Definition of Done (advisory → blocking) | Framework | Advisory gate lands here (Phase A); blocking is Phase E. |

---

## 8. AD-5 blockers

🔴 **AD-5 remains OPEN and is not resolvable in code.** All regulated insurance logic — suitability
determinations, replacement/1035 recommendations, licensing/CE **validation**, compliance
approvals, sale/issue blocking — is **blocked** and **out of scope for 0.11.0**. It cannot pass an
RC gate without a **qualified, named compliance reviewer** and an **approved sign-off artifact**.
Michael Shelton is the business/operational owner only — not regulatory certification.

Implications for this cycle:

- No regulated capability is built, enabled, or documented as available.
- Regulated Publication Register rows are seeded **AD-5-BLOCKED / draft**, never publishable.
- The only AD-5 movement possible outside code is **naming the reviewer**; until then the gate
  stays shut. This plan does not assume that happens within 0.11.0.

---

## 9. Documentation roadmap dependencies

Aligned to `05-IMPLEMENTATION-ROADMAP.md`:

- **0.11.0 == Phase A** (Foundation & governance) — the roadmap's designated *standalone docs
  release* and the prerequisite that "unblocks everything."
- **Phase B** (operational risk floor: Asset/Config, DR/BCP, Vendor register, Controls, IR) — a
  **separate focused initiative after 0.11.0**; explicitly not in this cycle.
- **Phases C–D** (surface/generate technical layer; author operational/business layer) — proceed
  **alongside normal delivery** per the roadmap's cadence-coupling rule; each future development
  phase closes its own area's gaps as Definition of Done. Not front-loaded here.
- **Phase E** (automation: `scripts/docs_sync.py`, blocking gate, review calendar) — future;
  0.11.0 lays only the advisory groundwork.
- **Phase F** (steady state) — the end target; not this cycle.

No edit to the roadmap file is required for 0.11.0 — it already defines Phase A as next. The only
roadmap-adjacent updates are **pointers** (§ "Updated roadmap references" in the cover note):
`PROJECT_STATUS.md` already names Phase A as the next milestone; the Register promotion is executed
where the roadmap places it.

---

## 10. Estimated implementation phases

Effort is **relative sequence**, not calendar dates (consistent with the roadmap's convention).

| Phase | Title | Work | Rel. effort | Gate |
|---|---|---|---|---|
| **0.11-P0** | Framework ratification & checkpoint | Approve framework; confirm scope; architecture checkpoint for this cycle | XS | Architecture checkpoint |
| **0.11-P1** | Confluence space skeleton | Create nodes `00/01/10/20/30/40/80/90`; 3 profile Area Shell templates; import Template Library | M | Validation: pages exist, IDs recorded |
| **0.11-P2** | `governance/` Git tree | Scaffold subtree + per-node README + ownership front matter + CONTRIBUTING pointer | S | Validation: tree builds, links resolve |
| **0.11-P3** | Publication Register promotion | Expand crosswalk to 26 areas × doc types; seed status from Capability Map; assign owners; flag AD-5 rows | **L** | Validation: every area has rows + owner + home + status |
| **0.11-P4** | DoD gate (advisory) + PR template | Add DoD checklist to PR template; advisory docs-gate script (report-only) in CI | S | Validation: gate runs, reports, exits 0 |
| **0.11-P5** | RC validation | Full suite green; register/skeleton/owner completeness audit; docs consistency pass | S | RC gate |
| **0.11-P6** | Release approval & tag | Approval artifact; CHANGELOG date; release `v0.11.0`; publish release notes | XS | Release approval gate |

Legend: XS < S < M < L. Total cycle ≈ one **M–L** documentation/governance release; the register
promotion (P3) is the critical-path item.

---

## 11. Recommended implementation order

1. **0.11-P0 — Framework ratification & architecture checkpoint** *(recommended first milestone).*
2. **0.11-P1 — Confluence space skeleton** (unblocks register page-ID linking).
3. **0.11-P2 — `governance/` tree** (can run in parallel with P1).
4. **0.11-P3 — Publication Register promotion** (critical path; depends on P1 for IDs, Capability
   Map for status).
5. **0.11-P4 — Advisory DoD gate + PR template.**
6. **0.11-P5 — RC validation** → **0.11-P6 — Release approval & tag.**

Each phase ends with its own validation and a stop-for-review, mirroring the 0.10.0 cadence.

---

## 12. Major technical risks (condensed)

- **Register scope discipline (R1)** — the single biggest risk: keep P3 to rows/skeletons/owners;
  authored content is Phase D, not here.
- **Confluence provisioning idempotency (R2)** — script/checklist creation so re-runs don't
  duplicate; capture page IDs immediately.
- **AD-5 mis-flagging (R3)** — regulated rows must default to blocked/draft with the boundary text
  reused from the 0.10.0 pages.
- **Scope creep into Phase B/E (R6, R4)** — risk-floor authoring and any blocking gate are out of
  scope; enforce at the architecture checkpoint.

---

## 13. Recommended first implementation milestone

**0.11-P0 — Framework ratification & architecture checkpoint.** Before any provisioning, hold the
0.11.0 architecture checkpoint to: (a) formally ratify the framework as operative, (b) confirm the
Phase A scope and the explicit non-goals above, (c) confirm owners (and the AD-5 reviewer gap), and
(d) authorize P1. This is XS effort, carries no code risk, and gates the rest of the cycle — the
right first step for a new development cycle that must not disturb the closed 0.10.0 release.
