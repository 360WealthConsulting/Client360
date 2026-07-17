# Release 0.12.0 — P0 Architecture Checkpoint (PROPOSED · Revision 2)

_Architecture validation for the **revised** Release 0.12 scope (`RELEASE_0.12.0_PLAN.md` /
`RELEASE_SCOPE.md`). Planning/analysis only — no implementation. `v0.11.0` immutable; **decisions
D1–D10 unchanged**; no app/migration/Confluence changes; AD-5 unresolved._

> **Revision 2.** The P0 **phase** is unchanged (it remains an architecture checkpoint). This
> **document** is updated to validate the approved **authoring-first** sequence: **Author → Review →
> Validate → Reconcile → Automate → Publish.**

## 1. Scope of this checkpoint

Validate that the revised 0.12 theme — **author operational knowledge first**, then review, validate,
reconcile, automate, and publish — is consistent with the framework, the roadmap, and the 0.11.0
architecture, **without changing any approved decision (D1–D10)**, and identify the architectural
risks and conditions before implementation.

## 2. Alignment with the framework & roadmap — strengthened by this revision

- **Roadmap ordering restored.** `05-IMPLEMENTATION-ROADMAP.md` prescribes: *"Structure and
  enforcement first (Phase A), then close the operational risk floor (Phase B), then backfill by
  priority (C–D), then automate (Phase E)."* 0.11.0 = Phase A. The revised 0.12 authors the
  **operational content (Phases B/C/D)** and sequences **automation (Phase E) last** — this is the
  roadmap's **canonical order**. (Revision 1's automate-first sequence inverted it; Revision 2 aligns.)
- **Priority-area mapping.** IT Ops / M365 / AD / Windows Server / SonicWall / Networking / Backup &
  DR / Security ≈ **Phase B** (risk floor). Schwab / AssetMark / TaxDome / Wealthbox / Drake ≈ **Phase
  C/D** (surface + author client-platform ops). Onboarding / servicing / internal SOPs ≈ **Phase D**.
- **Populate, don't expand.** 0.12 fills the existing framework (register rows → authored content); it
  adds **no new areas** (taxonomy stays 26 + `SHARED` + `GOV`) and **no template/architecture change**.

## 3. Decisions D1–D10 — unchanged (confirmed)

The revision changes **sequencing only**; every approved decision holds:

| Decision | 0.12 effect | Change? |
|---|---|---|
| D1 register canonical + generated crosswalk | authored pages become register rows; crosswalk regenerates | No |
| D2 areas = 26 + SHARED + GOV | authoring populates existing areas | No |
| D3 hybrid union | authored pages fill hybrid doc types | No |
| D4 status enum | authored pages progress `planned → draft → published` | No |
| D5 governance skeleton | governance areas authored (non-regulated) under the skeleton | No |
| D6 advisory only | **stays advisory** in 0.12 | No |
| D7 semantic id + TBD | migration/publication backfills real IDs (P6) | No |
| D8 parallelism | phase practice | No |
| D9 AD-5 invariant | no AD-5 content authored/published; invariant enforced | No (enforced) |
| D10 taxonomy migration | labels applied at publication (P6) | No |

**No architecture decision is modified.** The authoring standard (P2) is **editorial**, not
structural — it does not replace the templates or the information architecture.

## 4. 0.12 architecture decisions (proposed — revised for authoring-first)

| ID | Decision | Rationale |
|---|---|---|
| **A1** | **Lifecycle = Author → Review → Validate → Reconcile → Automate → Publish.** Automation/publication (P5/P6) run **after** authored content passes review (P3). | Ships automation against real, reviewed content; matches the roadmap's "then automate" ordering |
| **A2** | **Verified-facts-only authoring.** Operational content is authored **only from verified operator/SME-provided facts**; where facts are unavailable, ship the **standard-structured scaffold** marked `draft`/"SME-completion required" — **never fabricate** configs/procedures. | Infrastructure/vendor areas have **no codebase source**; the framework rule is "verified, never inferred" |
| **A3** | **Reconcile only after replacement exists.** No legacy 360OS/Atlas page is archived until equivalent authored documentation exists; **archive-not-delete**; preserve identifiers + audit history. | No knowledge loss; reversible |
| **A4** | **Authoring standard is editorial.** P2 defines how pages are written; it does **not** change D1–D10, the templates, or the taxonomy. | Populate, don't redesign |
| **E1–E5** (from Rev 1, now applied at **P5/P6**) | Publishing safety: publish only git-canonical **non-gated, reviewed** rows (never draft/AD-5); preserve page IDs (re-parent/label only); idempotent + dry-run-first; reuse P3 validator/generator; PyYAML installed in the advisory workflow. | Enforces D6/D9 at the publishing boundary; protects existing pages |

A1–A4 are **new** 0.12 decisions (sequencing + authoring integrity); E1–E5 carry over unchanged and now
sit at the end of the release.

## 5. Confluence write-authorization boundary (now later in the release)

0.11.0 was read-only to Confluence. Under the revised sequence, **authorized Confluence writes occur
only at P4 (reconciliation: archive/merge) and P6 (controlled publication)** — **after** authoring
(P1), the standard (P2), and quality review (P3). Every write is gated by A3 (reconcile-after-
replacement), E2 (ID-preserving), E4 (idempotent, dry-run-first), and E1/D9 (never publish
draft/regulated).

## 6. Security / compliance boundary

- **AD-5 unchanged and unresolved.** No regulated content authored or published; regulated topics are
  scaffolded as gated + unpublished only; the D9 invariant is enforced at publication. Compliance
  reviewer stays `UNFILLED`; Michael Shelton = business/operational owner only. 0.12 does **not**
  resolve AD-5 or invent approvals.
- **No secrets / client data.** Authoring is operational procedure/knowledge; system credentials,
  keys, and client PII are referenced by name (secret store / system of record), **never** by value —
  the advisory DoD checker's secret scan is a backstop.

## 7. Risks (architecture-level)

Highest, from `RELEASE_0.12.0_PLAN.md` §5:
- **R1 authoring accuracy** (no codebase source for infra) → **A2** verified-facts-only, scaffold-not-
  fabricate.
- **R2 scope size** (~16 areas) → tier + deliver a prioritized subset; remainder in 0.13.
- **R3 author-before-standard (P1 before P2)** → P1 uses the existing templates + the Insurance-
  Commissions exemplar as a **provisional** standard; P2 ratifies; P3 retrofits.
- **R4 archive-without-replacement** → **A3** gate.
- **R5 publish draft/regulated** → **E1/D9** publish-eligibility filter at P6.
None requires changing D1–D10.

## 8. Dependencies & conditions

- **Critical dependency:** **verified operational facts / SME input** for the infrastructure and
  vendor areas — without it, P1 can only scaffold, not author. This is the gating input to P1.
- Existing framework/templates/register/DoD tooling (delivered in 0.11.0); Confluence MCP write access
  (P4/P6); legacy dispositions (approved at P4, after replacements).
- **AD-5 (external, UNFILLED)** blocks only regulated content — out of scope, not a 0.12 blocker.

## 9. Recommendation

**PROCEED TO PLAN-APPROVAL for the revised 0.12 as scoped — with conditions.** The authoring-first
sequence is architecturally sound and **more roadmap-aligned than Revision 1** (it restores
Author-before-Automate), changes no approved decision (D1–D10 intact), keeps the DoD advisory (D6),
and authors no regulated content (AD-5 gated). It adds four sequencing/integrity decisions (A1–A4);
the publishing-safety decisions (E1–E5) move to P5/P6.

**Conditions before P1 implementation:**
1. Approve the revised scope (`RELEASE_0.12.0_PLAN.md` Rev 2).
2. Approve decisions **A1–A4** (lifecycle, verified-facts-only, reconcile-after-replacement, editorial
   standard) and confirm E1–E5 apply at P5/P6.
3. Provide (or commit to providing) the **verified operational facts / SME input** for P1 authoring —
   or explicitly approve **scaffold-first** authoring where facts are pending.
4. Confirm a **prioritized P1 subset** (recommended: the infrastructure/risk-floor tier first) given
   the ~16-area scope; remainder to 0.13.
5. Reaffirm: DoD stays **advisory** (D6); AD-5 stays gated; `v0.11.0` immutable.

**Recommended first milestone:** **0.12-P1 — Operations Manual authoring** (infrastructure/risk-floor
tier first), authored from verified facts using the existing templates + exemplar as the provisional
standard, with scaffold-not-fabricate where facts are pending.

---

_Revised checkpoint for review. No implementation. Awaiting approval before Phase P1._
