# Release 0.11.0 — 0.11-P0 Architecture Checkpoint

_Phase **0.11-P0 — Framework Ratification & Architecture Checkpoint**. Branch `release/0.11.0`
(cut from `main` @ `6f7292c`). Baseline: `docs/releases/0.11.0/RELEASE_0.11.0_PLAN.md` (unchanged,
git-object `58f1932…`). Sources of truth reviewed: the plan, `05-IMPLEMENTATION-ROADMAP.md`,
`docs/DOCUMENTATION_CROSSWALK.md`, the framework standards/templates (`documentation-framework/01…06`
+ README), and current repo/Confluence architecture docs. Date 2026-07-17._

> **Analysis only — guardrails honored.** No functionality implemented; no Confluence provisioned
> or modified; no Publication Register created; no template created/modified; no `governance/` tree
> added; no DoD gate modified; no P1+ work; no roadmap work outside 0.11.0 scope; no change to
> 0.10.0 code/docs/tag/release/published pages; **no assumption that AD-5 is resolved**; no
> regulated insurance functionality. This report **recommends**; it does not edit the approved
> baseline (no material defect was found that would require it — see Deliverable 4).

---

## §0. Branch, baseline & validation

**Tasks 1–5**

| # | Task | Result |
|---|---|---|
| 1 | Feature branch from current `main` | ✅ `release/0.11.0`, branch-point `6f7292c` (= `main` HEAD) |
| 2 | Working tree clean | ✅ clean before and after the checkpoint |
| 3 | Plan unchanged after branching | ✅ `git hash-object` = `58f1932…` (identical pre/post) |
| 4 | Plan is the implementation baseline | ✅ adopted; not modified |
| 5 | 0.10.0 artifacts unchanged | ✅ tag `v0.10.0` → `5ba60a2` intact; `main` untouched |

**Validation block**

| Check | Result |
|---|---|
| Branch name / branch point | `release/0.11.0` from `6f7292c` |
| Clean tree before & after | ✅ (`git status --porcelain` empty both times) |
| No released code changed | ✅ diff vs `main` is docs-only (no `app/**`, no `scripts/**`) |
| No Confluence changes | ✅ no MCP write calls issued this phase |
| No migration changes | ✅ no `migrations/**` in the diff |
| No release artifacts modified | ✅ no `CHANGELOG`, `release.sh`, tag, or release touched |
| `git diff --check` | ✅ clean (no whitespace/conflict errors) |
| Documentation consistency | ✅ plan ↔ roadmap ↔ crosswalk ↔ framework cross-checked (findings below) |
| Final commit SHA | recorded in the delivery note after the doc-only commit + push |

---

## Deliverable 1 — Architecture checkpoint report

### 1.1 Current-state assessment
- **Framework**: six deliverables approved-in-principle (`documentation-framework/README.md`),
  company-wide scope (26 areas), documentation-only (no app code).
- **Register-of-record today**: `docs/DOCUMENTATION_CROSSWALK.md` — a company-wide section map
  (business-section lettering A·Executive Management … L·Technology) plus **drafted rows for
  Benefits (§2, 3 pages) and Insurance (§3, 11 pages)**. Its header **already declares** the Phase-A
  target: *"Roadmap Phase A promotes it to a machine-readable form (`docs/registers/pages.yml`)."*
- **Confluence**: space `3WCO` exists; only the **5 non-regulated 0.10.0 Insurance pages are
  published** (under parent `28770305`); all other area pages are draft/absent.
- **Governance in Git**: no `governance/` tree yet; policies/DR/controls are unversioned as manual
  artifacts. Software architecture is broadly present under `docs/*_ARCHITECTURE.md`.
- **DoD/sync**: DoD defined (06 §4) but **not yet wired as a gate**; `scripts/docs_sync.py` is a
  Phase-E proposal, not built.
- **Maturity (from 03/04)**: software architecture strong for client-facing areas; infrastructure &
  business-operations areas largely **greenfield**; DR/BCP, Vendor, Controls, Asset inventories are
  the highest-risk gaps.

### 1.2 Proposed target architecture (end-state of 0.11-P0 → P6, Phase A only)
A single Operations Manual with: one IA (nodes `00–90`), one superset template library selected by
**area profile** (Software / Infrastructure / Business Ops), one **machine-readable Publication
Register** (`docs/registers/pages.yml`) with the human crosswalk generated from it, a git-canonical
`governance/` tree **skeleton**, and an **advisory** DoD checklist/gate on PRs. Every page has one
canonical home and an owner. **Phase A exit**: every area has a page skeleton, a register row, and
an owner; DoD visible on PRs. No risk-floor content (Phase B), no authored area content (C/D), no
automation (E) in this cycle.

### 1.3 Repository structure (target after Phase A)
```
docs/
  documentation-framework/        # standard (present, unchanged this phase)
  releases/0.11.0/                # this cycle's plan + P0 checkpoint (+ later RC/approval)
  registers/pages.yml             # NEW (P3) — canonical machine-readable register
  DOCUMENTATION_CROSSWALK.md       # becomes a GENERATED view of pages.yml (P3)
governance/                       # NEW (P2) — skeleton only, git-canonical
  policies/ runbooks/ dr/ controls/ inventory/ calendar/   (each: README + front-matter)
  CONTRIBUTING.md
.github/ (PR template + advisory docs-gate step)           # NEW (P4), report-only
```
No `app/**` or `migrations/**` changes anticipated (Phase A is documentation/governance).

### 1.4 Confluence hierarchy (validated, unchanged from IA 01)
Nodes `00·Company Home`, `01·How This Manual Works`, `10·Client-Facing`, `20·Technology & Infra`,
`30·Business Operations`, `40·Cross-Platform/Shared`, `80·Libraries & Programs`, `90·Registers &
Governance` — complete, non-overlapping, provisioned via **Area-Shell-per-profile** (IA §5). The 5
published 0.10.0 Insurance pages slot under `10·Insurance` and are **not touched** by this cycle.
Open items: register rows for **node-40 shared** and **node-90 governance** pages (D2), and semantic
`page_id` vs numeric `confluence_page_id` during skeleton creation (D7).

### 1.5 Governance ownership model (validated)
One-canonical-home-per-page enforced by the capability map's single-home resolution table (03), so
no source is canonical for two areas. Owner + reviewer are page front-matter (02), surfaced as
living Ownership Directory / Review Calendar views. Version-controlled governance (policies, DR,
runbooks, controls) is **git-canonical under `governance/`** for PR review + audit trail. Business
owner across areas: **Michael Shelton**; the **Compliance reviewer role remains UNFILLED (AD-5)** —
carried, not assumed resolved. Gap: AD-5 gating is prose, not a register invariant (D9).

### 1.6 Template & inheritance model (validated)
Shared front-matter is mandatory on every page; each area applies a **profile** selecting a doc-type
subset; the Area Shell template clones the profile's types pre-labelled with canonical-source set
(IA §5, 02). Type-disambiguation (02 §C2) fixes every near-overlap (Rules≠Policy,
Exception≠Incident, SOP≠Runbook≠Checklist, Data-Model≠Asset-Inventory, Integrations≠Vendor-Register,
Ownership≠RACI), so no two types document the same thing. **Inheritance nuance**: node-10
client-facing areas are **Hybrid** (Software **+** Business-Ops types) — shell/register seeding must
union both profiles (D3).

### 1.7 Publication Register architecture (core of this checkpoint)
Target = `docs/registers/pages.yml`, one entry per page (schema in 06 §2:
`page_id, area, profile, doc_type, canonical_source, git_source, confluence_page_id, owner,
reviewer, status, last_reviewed, review_cycle, next_review`); the crosswalk table is generated from
it. Decisions to settle before seeding: **format** (D1 — already endorsed by the crosswalk header),
**row scope incl. shared/governance rows** (D2), **hybrid union** (D3), **status enum** (D4),
**AD-5 field + invariant** (D9), and **taxonomy reconciliation** between the crosswalk's A–L section
lettering and the framework's 26 area codes (D10).

### 1.8 Definition-of-Done integration (validated)
DoD covers three change types with a path→doc-type mapping and a PR checklist (06 §4), **advisory in
Phase A, blocking in Phase E**. Phase-A gate is a **minimal, disposable** report-only check —
explicitly **not** a partial build of the Phase-E `docs_sync.py verify` (D6). No modification to any
existing gate occurs in P0.

### 1.9 Release & change-control workflow (validated)
0.11.0 is a **self-contained cycle**: its own architecture checkpoints, validation gates, RC, release
approval, and release documentation (plan §0/§10), mirroring the 0.10.0 cadence
(architecture → implement → validate → document → separate commit → push → review-gate). The DoD's
advisory→blocking escalation and the future `release.sh` docs precondition (06 §5) integrate cleanly
with existing gates (CHANGELOG lint, migration-head/reversibility, `release.sh`). No release
machinery is changed in P0.

### 1.10 Security, access, audit & scope boundaries
- **AD-5 (regulated insurance)**: OPEN, non-code blocker. No regulated capability is built, enabled,
  or documented as available in 0.11.0. Regulated register rows must be **machine-blocked from
  `published`** (D9). The 5 published 0.10.0 pages carry AD-5 boundary text and are **not** modified.
- **Confluence access/audit**: publishing is via the connected Atlassian MCP under the operator's
  identity; Phase A provisioning (P1) is **skeleton + templates only**, no regulated content. Every
  page carries owner/reviewer + status metadata (auditable via Page Properties → registers).
- **Governance audit trail**: git-canonical `governance/` artifacts get PR review + history — the
  point of putting policies/DR/controls in Git (06 §1).
- **Data-scope**: this cycle handles **documentation metadata only** — no client/PII, no application
  data, no secrets, no endpoints. `governance/` skeleton holds **no** authored sensitive content
  (D5). No new capabilities, roles, or migrations; the platform's capability-based authorization is
  untouched.
- **Release-isolation**: 0.10.0 tag/release/published pages are read-only for this cycle (critical
  bug fix only, which would ship as a separate 0.10.x patch — not through 0.11.0).

---

## Deliverable 2 — Decision log

> Recommended; effective on checkpoint approval. Status: **Proposed** unless noted.

**D1 — Register canonical format = `docs/registers/pages.yml`**
- *Decision*: Author the register directly as machine-readable `pages.yml`; generate the crosswalk table from it.
- *Rationale*: 06 §2 mandates it, and the crosswalk header **already declares** this Phase-A target — so the plan's "expand the crosswalk" is satisfied by generating it *from* the canonical YAML.
- *Alternatives considered*: (a) keep hand-authored markdown crosswalk as canonical [rejected — Phase E would force a markdown→YAML migration + drift]; (b) build the automated generator now [rejected — generator/`docs_sync.py` is Phase E; Phase A needs only a one-shot render].
- *Consequences*: one authoring pass; crosswalk becomes a generated view; a trivial one-shot render is acceptable interim until the Phase-E generator lands.
- *Status*: **Proposed (endorsed by crosswalk header — low controversy).**

**D2 — Register row scope includes shared + governance pages**
- *Decision*: Rows = 26 areas × profile doc types **plus** node-40 shared singletons and node-90 register/governance pages under pseudo-areas `SHARED`/`GOV`.
- *Rationale*: guarantees every page — area or shared — has exactly one register row and one canonical home.
- *Alternatives considered*: (a) area rows only [rejected — shared/singleton pages become homeless, coverage blind spot]; (b) track shared pages in a separate file [rejected — violates single-register principle].
- *Consequences*: modestly more rows; the register is genuinely complete; expands row *count*, not authored *content*.
- *Status*: **Proposed.**

**D3 — Hybrid node-10 areas seed the union of both profiles**
- *Decision*: For the 11 client-facing areas, seed Software-profile **and** Business-Ops process types (Policy/SOP/RACI/Checklist/Calendar).
- *Rationale*: IA §2 / README §2 define client-facing capabilities as Hybrid.
- *Alternatives considered*: single Software profile [rejected — drops the business-process facet the framework requires].
- *Consequences*: more rows per node-10 area; correctly reflects the Hybrid model.
- *Status*: **Proposed.**

**D4 — Register status enum = `planned | draft | published | needs_review`**
- *Decision*: Standardize the enum; seed not-yet-drafted pages `planned`, existing Insurance/Benefits drafts `draft`, the 5 published 0.10.0 pages `published`.
- *Rationale*: template front-matter's two-value `draft|published` can't express "row exists, page not written" (IA §5 uses `planned`; 06 §3 uses `needs_review`).
- *Alternatives considered*: two-value enum [rejected — can't seed planned rows]; free-text status [rejected — not machine-checkable].
- *Consequences*: unambiguous seeding; the front-matter enum is widened to match.
- *Status*: **Proposed.**

**D5 — `governance/` = structure-only skeleton**
- *Decision*: Scaffold `governance/{policies,runbooks,dr,controls,inventory,calendar}/README.md` + front-matter + `governance/CONTRIBUTING.md`; **zero** authored policy/DR/control content.
- *Rationale*: realizes 06 §17 / gap-analysis §1 without pulling Phase-B risk-floor authoring into Phase A (plan §3 non-goal).
- *Alternatives considered*: author starter policies/DR now [rejected — that is Phase B/D and would breach scope]; defer the tree entirely to Phase B [rejected — roadmap places the tree in Phase A].
- *Consequences*: git-canonical home exists and is ready; content follows in later cycles under DoD.
- *Status*: **Proposed.**

**D6 — Phase-A advisory gate is a disposable, standalone check**
- *Decision*: Ship a minimal report-only check (PR-checklist presence + "register row touched?" heuristic), exit 0, explicitly labeled superseded by Phase-E `docs_sync.py verify`.
- *Rationale*: keeps Phase A advisory-only (06 §4) without pre-building the Phase-E tool.
- *Alternatives considered*: start `docs_sync.py` now [rejected — Phase E; risks premature/partial tool]; no gate at all [rejected — roadmap Phase A adds the advisory gate + PR template].
- *Consequences*: cheap, throwaway-by-design; replaced wholesale in Phase E.
- *Status*: **Proposed.**

**D7 — Semantic `page_id` now, numeric `confluence_page_id: TBD` backfilled**
- *Decision*: seed stable semantic ids; fill numeric Confluence ids as Area Shells instantiate pages.
- *Rationale*: register rows must not block on page creation; semantic ids are the stable key (IA §3).
- *Alternatives considered*: wait for all pages before seeding rows [rejected — serializes P3 behind full provisioning].
- *Consequences*: rows exist early with `TBD`; a short backfill step follows P1.
- *Status*: **Proposed.**

**D8 — Allow P1 ‖ P2 ‖ P3-authoring**
- *Decision*: once D1 fixes the format, author register rows in parallel with space provisioning and the governance skeleton; backfill ids post-P1.
- *Rationale*: row authoring depends on format + capability map, not full provisioning.
- *Alternatives considered*: strict P1→P2→P3 [rejected — needlessly serial; longer critical path].
- *Consequences*: shorter wall-clock; P3 remains the critical-path item.
- *Status*: **Proposed.**

**D9 — AD-5 as a machine-checkable register field**
- *Decision*: add nullable `compliance_gate` (e.g. `AD-5`); enforce **`compliance_gate set ⇒ status ≠ published`** for regulated Insurance (INS) and Compliance (CMP) rows.
- *Rationale*: moves AD-5 enforcement from prose (plan R3/§8) to a checkable invariant.
- *Alternatives considered*: prose-only note [rejected — a regulated row could be seeded `published` by mistake].
- *Consequences*: regulated pages provably cannot be seeded publishable; small schema addition.
- *Status*: **Proposed (compliance-significant).**

**D10 — Reconcile the register taxonomy to the framework's 26 area codes**
- *Decision*: the promoted register adopts the **framework IA area-code taxonomy** (CLM360, TAXOPS, …, SHARED, GOV); map the crosswalk's existing A–L business-section rows into it during promotion.
- *Rationale*: the crosswalk currently uses business-section lettering (A·Executive Management …) that is not 1:1 with the framework's 26 area codes; the register must use one taxonomy — the IA is the architectural standard.
- *Alternatives considered*: keep A–L lettering [rejected — diverges from IA/templates/labels which key off area codes]; maintain both [rejected — dual taxonomy defeats a single register].
- *Consequences*: a one-time mapping during P3; some crosswalk sections (e.g. Executive Management) map to `GOV`/`SHARED` or a node rather than a software area.
- *Status*: **Proposed (required reconciliation before seeding).**

---

## Deliverable 3 — Risk-register review

**Confirmed (from the plan, R1–R8)** — all still valid; severities and P0 gates below.

| ID | Risk | Severity | Mitigation | Implementation gate |
|---|---|---|---|---|
| R1 | Register promotion drifts into content authoring (→ Phase D) | **High** | Rows/skeletons/owners only; DoD for P3 = "a row exists," never "a page written" | P3 exit review rejects any authored area content |
| R2 | Confluence provisioning not idempotent | Med | Scripted/checklisted, re-runnable; record page ids | P1 validation: re-run creates no duplicates |
| R3 | AD-5 regulated rows mis-seeded publishable | **High** | Reuse 0.10.0 boundary text; default draft (now enforced via D9) | P3 gate: no regulated row `status: published` |
| R4 | Advisory gate mistaken for / becomes blocking | Med | Exit 0 by design; blocking deferred to Phase E | P4 validation: gate exits 0 on a failing checklist |
| R5 | Ownership gaps (areas without owner) | Low | Default owner = M. Shelton + visible TBD; compliance flagged UNFILLED | P3 gate: every row has an owner or explicit TBD |
| R6 | Scope creep pulling Phase-B risk-floor forward | **High** | Phase B is a non-goal (plan §3) | Checkpoint/P-phase reviews reject risk-floor authoring |
| R7 | Git↔Confluence canonical drift | Med | One-home rule; register `canonical_source` is the contract | P3 validation: no page canonical in both |
| R8 | 0.10.0 regression surfaced mid-cycle | High | Fix ships as separate 0.10.x patch, never folded into 0.11.0 | Release-isolation check each phase |

**Newly identified (this checkpoint)**

| ID | Risk | Severity | Mitigation | Implementation gate |
|---|---|---|---|---|
| R9 | Register authored as markdown then migrated to `pages.yml` → rework/drift | Med | **D1** — author `pages.yml` canonical from the start; crosswalk generated | P3 gate: canonical register is `pages.yml` |
| R10 | Shared (node 40) / governance (node 90) pages omitted → homeless pages | Med | **D2** — explicit `SHARED`/`GOV` rows | P3 gate: every shared/gov page has a row |
| R11 | AD-5 enforced only in prose → regulated row seeded `published` | **High** | **D9** — `compliance_gate` field + not-publishable invariant | P3 gate: invariant check passes |
| R12 | Advisory gate grows into a premature partial `docs_sync.py` | Low | **D6** — disposable report-only check, labeled superseded | P4 review: gate has no push/sync logic |
| R13 | Crosswalk A–L section taxonomy ≠ framework 26 area codes → inconsistent register | Med | **D10** — adopt IA area codes; map existing rows | P3 gate: register uses one (IA) taxonomy |

---

## Deliverable 4 — Planning refinements

**No material inconsistency or architectural defect requires editing `RELEASE_0.11.0_PLAN.md` now.**
The plan's "expand the crosswalk" wording is *reconcilable* with the framework (the crosswalk header
itself names `pages.yml` as the Phase-A target), so it is a wording clarification, not a defect. Per
the guardrail, the baseline is **left unedited** pending approval. The following are recommended for
the reviewer to fold into the plan **before P3 begins**:

**Required before P3** (needed for unambiguous, correct seeding):
- **R-1 (D1)** — state the register target as canonical `docs/registers/pages.yml` + generated crosswalk.
- **R-2 (D2)** — row scope = 26 areas + `SHARED` + `GOV`.
- **R-3 (D3)** — hybrid union for node-10 areas.
- **R-4 (D4)** — status enum `planned|draft|published|needs_review`.
- **R-5 (D9)** — add `compliance_gate` (AD-5) field + not-publishable invariant.
- **R-6 (D10)** — reconcile crosswalk A–L sections to the framework's 26 area codes.

**Optional (execution-quality, not correctness):**
- **O-1 (D5)** — record the concrete `governance/` subtree layout in §10 P2.
- **O-2 (D6)** — label the advisory gate as disposable/standalone in §10 P4.
- **O-3 (D7/D8)** — note `page_id`/`TBD` handling and P1‖P2‖P3 parallelism in §11.

None alters the cycle's theme, objectives, non-goals, or effort envelope; all sharpen P2/P3/P4
detail. Applying them is itself a plan change and should be **approved** before edit.

---

## Deliverable 5 — P0 recommendation

**APPROVE P1 WITH CONDITIONS.**

The framework architecture, repository organization, Confluence hierarchy, governance model, DoD
integration, register strategy, template inheritance, roadmap alignment, and future-release workflow
are all **validated**. Proceed to **0.11-P1 (Confluence space skeleton)** — the plan's recommended
first milestone — **subject to** ratifying decisions **D1–D10** and folding the **Required (R-1…R-6)**
refinements into the plan first. Rationale: these settle register format/scope/taxonomy/status/AD-5
*before* any row is seeded or node created, which is exactly what prevents P3 rework; none is a
blocker, all are cheap to settle at P0.

**Conditions to clear before P1/P3:**
1. Approve D1–D10 (or direct alternatives).
2. Fold Required refinements R-1…R-6 into `RELEASE_0.11.0_PLAN.md` (a reviewed baseline change).
3. Reaffirm the AD-5 boundary (D9 invariant) and the "rows not authored content" scope (R1/R6).

**Not blocked**, because no architectural defect was found — only format/scope clarifications the
checkpoint is designed to surface.

---

**Stopping here for review.** No P1 work, no Confluence provisioning, no template/register/governance
creation, no DoD-gate change. Awaiting explicit approval of D1–D10 + Required refinements before P1.
