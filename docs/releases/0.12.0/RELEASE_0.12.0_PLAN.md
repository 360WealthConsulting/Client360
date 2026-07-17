# Release 0.12.0 — Plan (PROPOSED · Revision 2)

_Status: **PLANNING** (revised roadmap; not approved). Created 2026-07-17; **Revision 2 —
authoring-first sequence** per the approved planning revision. Predecessor: **0.11.0 — Documentation
Foundation**, released and tagged `v0.11.0` (`main` @ `8cb7868`). Planning branch `release/0.12.0`._

> **Planning only.** No features implemented; no 0.11.0 artifact modified; `v0.11.0` immutable;
> architecture decisions **D1–D10 unchanged**; no application code, migrations, or Confluence changes;
> no drafts published; **AD-5 not resolved**; no compliance approvals invented.

> **Revision 2 — architectural decision.** The Documentation Foundation (0.11.0) is complete. Release
> 0.12 prioritizes **authoring operational knowledge** over **publishing automation**. The lifecycle
> is **Author → Review → Validate → Reconcile → Automate → Publish** — *not* automate-then-author-later.

---

## 1. Executive summary

Release 0.11.0 established **where** documentation belongs (framework, register, taxonomy, advisory
DoD). Its register is 554 rows but **489 are `planned`** — the framework is an empty shelf. Release
0.12 fills it: the primary objective is **authoring high-quality operational knowledge** into the
existing framework, then **reviewing and validating** it, then **reconciling** the legacy 360OS/Atlas
pages against the new content, and only then **automating publication**. Publishing automation
(`docs_sync`) and Confluence publication move to the **end** of the release, after quality is
established — so automation ships against real, reviewed content rather than empty scaffolding.

This is a **documentation/governance-focused, non-regulated** release. It **expands the content**
inside the framework, not the framework itself; it authors **no regulated (AD-5) content**.

## 2. Scope (proposed in-scope)

1. **Operations Manual authoring (primary)** — author operational documentation for the priority
   areas (§4 P1) into the existing framework using the existing templates/taxonomy.
2. **Documentation Authoring Standard** — a reusable editorial standard every Operations Manual page
   must follow (structure, style, terminology, cautions, troubleshooting format, review/ownership/
   revision-history, cross-links, publication-readiness checklist). *Editorial standard only — does
   not replace the architecture or templates.*
3. **Documentation quality review** — validate authored pages with the **existing** DoD tooling
   (completeness, consistency, cross-links, ownership, review dates, taxonomy, publication readiness).
4. **Legacy Atlas reconciliation** — per-page **retain / merge / split / replace / archive**, executed
   **only after equivalent replacement documentation exists**; preserve historical identifiers + audit
   history; **archive-not-delete**.
5. **Publishing automation** — implement `docs_sync.py` (idempotent, dry-run, deterministic, publication
   validation, Publication Register sync) — **after** quality is established; **reuse 0.11 tooling**.
6. **Controlled Confluence publication** — synchronize git-canonical documentation after successful
   dry-run; preserve page IDs, hierarchy, metadata, legacy references; **never publish AD-5-gated docs**.

## 3. Out-of-scope (deferred)

- ❌ **AD-5 compliance approval / naming a reviewer** — external, not a code action.
- ❌ **Regulated Insurance rule-set authoring** — suitability, replacement/1035, licensing, CE rule
  sets — **AD-5-blocked**.
- ❌ **Blocking documentation enforcement** — DoD stays **advisory** (D6).
- ❌ **Application capability development unrelated to documentation** / app features / migrations.
- ❌ **Framework or template redesign** — 0.12 populates the framework; it does not change D1–D10 or
  the templates.
- ❌ **Modifying 0.11.0 artifacts or `v0.11.0`** — immutable (defect-fix only).

## 4. Proposed phases (P0–P7)

| Phase | Title | Work | Confluence writes? |
|---|---|---|---|
| **P0** | Architecture checkpoint | Validate the authoring-first sequence vs framework/roadmap; confirm no D1–D10 change (unchanged from Rev 1) | No |
| **P1** | **Operations Manual authoring (primary)** | Author operational docs for the priority areas (below) into the framework; verified facts only; no AD-5 content | No |
| **P2** | Documentation Authoring Standard | Editorial standard every page must follow (structure/style/terminology/cautions/troubleshooting/review/ownership/revision-history/cross-links/readiness checklist) | No |
| **P3** | Documentation quality review | Validate authored pages with existing DoD tooling; correct **before** migration | No |
| **P4** | Legacy Atlas reconciliation | Per-page retain/merge/split/replace/archive **only after replacement exists**; preserve identifiers + audit history; archive-not-delete | **Yes** (archive/merge) |
| **P5** | Publishing automation | `docs_sync.py` — idempotent, dry-run, deterministic, publication validation, register sync; reuse 0.11 tooling | Dry-run only |
| **P6** | Controlled Confluence publication | Sync git-canonical docs after dry-run; preserve IDs/hierarchy/metadata/legacy refs; never publish AD-5-gated | **Yes** (publish) |
| **P7** | Release candidate | RC validation, sign-off, tag `v0.12.0` | No |

**P1 priority areas** (author operational knowledge; group by tier for delivery):
- **Infrastructure / risk-floor:** IT Operations · Microsoft 365 · Active Directory · Windows Server ·
  SonicWall · Networking · Backup & Disaster Recovery · Security Operations.
- **Client-platform operations:** Schwab Operations · AssetMark Operations · TaxDome · Wealthbox ·
  Drake Tax.
- **Client & internal workflows:** Client onboarding workflows · Client servicing workflows ·
  Internal SOPs.

## 5. Risks

| ID | Risk | Sev | Mitigation |
|---|---|---|---|
| **R1** | **Authoring accuracy** — operational facts (AD/SonicWall/Server/Schwab procedures) have **no codebase source** and must not be invented | **High** | Author **only from verified operator/SME-provided facts** (framework rule: never infer). Where facts are unavailable, ship the **standard-structured scaffold** marked `status: draft` / "SME-completion required" — never fabricated configs/procedures |
| R2 | Scope size — ~16 areas of high-quality docs is large, likely multi-release | **High** | Tier + batch P1 (infra → client-platform → workflows); a release may deliver a **prioritized subset**; the rest continues in 0.13+ under the same standard |
| R3 | Authoring before the standard (P1 before P2) | Med | P1 uses the **existing templates + the Insurance-Commissions exemplar** as a provisional standard; P2 formalizes it; P3 retrofits P1 pages to the ratified standard |
| R4 | Reconciliation archives a page with no replacement | High | **No legacy page archived until equivalent documentation exists** (P4 gate); archive-not-delete; preserve identifiers + audit history |
| R5 | Publishing a draft / regulated / low-quality page | High | Publish only **git-canonical, reviewed, non-gated** rows; dry-run-first; AD-5 invariant enforced; P6 after P3 quality pass |
| R6 | Accidental AD-5 content authoring | High | No suitability/replacement/licensing/CE content; regulated topics scaffolded as gated, unpublished only |
| R7 | Framework/template drift during authoring | Med | Authoring uses existing templates/taxonomy unchanged; P2 is editorial, not structural |
| R8 | `v0.11.0` / 0.10.0 disturbed | Low | Immutable; 0.12 on its own branch; defect-fix only |

## 6. Dependencies

| Dependency | Type | Status |
|---|---|---|
| **Verified operational facts / SME input** (system configs, procedures for AD/M365/SonicWall/Server/Schwab/AssetMark/TaxDome/Wealthbox/Drake) | **External (firm)** | **Required for accurate P1 authoring** — the critical dependency |
| Existing framework, templates, taxonomy, register | Internal (0.11.0) | Delivered |
| Existing advisory DoD tooling | Internal (0.11.0) | Delivered (reused in P3) |
| 0.11 publishing/tooling patterns for `docs_sync` | Internal | Available for reuse (P5) |
| Confluence MCP write access | External | Available (P4/P6) |
| Legacy reconciliation dispositions | Approval | Needed at P4 (after replacements exist) |
| **Compliance reviewer (AD-5)** | External | **UNFILLED** — blocks only regulated content (out of scope) |

## 7. Acceptance criteria

- Priority-area (or approved subset) pages **authored from verified facts** into the framework;
  unavailable facts scaffolded as `draft` "SME-completion required" — **nothing fabricated**.
- **Documentation Authoring Standard** published; existing pages retrofitted to it.
- Authored pages pass the **existing DoD** review (completeness, consistency, cross-links, ownership,
  review dates, taxonomy, publication readiness); corrected **before** migration.
- Legacy reconciliation executed **only where replacement exists**; identifiers + audit history
  preserved; **no page deleted**.
- `docs_sync.py` implemented (idempotent, dry-run, deterministic, publication validation, register
  sync); a dry-run produces **no duplicates**.
- Controlled publication preserves IDs/hierarchy/metadata/legacy refs; **no AD-5-gated / draft page
  published**; DoD remains **advisory**.
- No app/migration change; `v0.11.0` and 0.10.0 intact; RC-validated; signed off; `v0.12.0` tagged
  with a dated CHANGELOG entry.

## 8. Recommended implementation order

**P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7**, embodying **Author → Review → Validate → Reconcile →
Automate → Publish**. P1 (authoring) is the primary, gating effort; P4 reconciliation runs **only
after** P1/P3 replacements exist; P5/P6 automate/publish **only after** quality (P3) is established.
Each phase ends with its own validation and a stop-for-review.

## 9. Estimated effort per phase

Relative sequence (not calendar). XS < S < M < L < XL.

| Phase | Effort | Note |
|---|---|---|
| P0 — checkpoint | XS | gate |
| **P1 — authoring** | **XL** | **critical path**; scope-bounded by SME facts + tiering (may be a prioritized subset) |
| P2 — authoring standard | M | editorial |
| P3 — quality review | M | reuses DoD tooling |
| P4 — reconciliation | M | Confluence writes; gated on replacements |
| P5 — `docs_sync` | L | reuse 0.11 tooling |
| P6 — controlled publication | M | Confluence writes; dry-run-first |
| P7 — RC + release | S | gate |

Total ≈ a **large** release dominated by P1 authoring; realistically P1 delivers a **prioritized
subset** with the remainder continuing in 0.13 under the ratified standard.

## 10. Recommendation — documentation-focused vs operational capability

**Confirmed: Release 0.12 remains documentation/governance-focused**, now **authoring-first** per the
approved decision. It creates the operational knowledge the framework was built to hold, before
investing in publication automation. It does **not** begin application-capability development, and
authors **no regulated (AD-5) content**.

**Primary caveat (R1/R2):** authentic operational documentation for infrastructure and vendor systems
requires **verified operator/SME facts the repository does not contain**. P1 must author from
firm-provided verified information and **scaffold (not fabricate)** where facts are unavailable —
consistent with the framework's "verified, never inferred" rule. Given the scope (~16 areas), 0.12
should target a **prioritized subset** (recommended: the infrastructure/risk-floor tier first), with
the remainder in 0.13.

---

_Revised roadmap for review. No implementation. Awaiting approval before Phase P1._
