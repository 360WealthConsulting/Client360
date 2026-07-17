# Release 0.12.0 — Scope Definition (PROPOSED · Revision 2)

_Companion to `RELEASE_0.12.0_PLAN.md`. Planning only — nothing implemented. **Revision 2 —
authoring-first** per the approved planning revision. `v0.11.0` immutable; D1–D10 unchanged; no
app/migration/Confluence changes; AD-5 unresolved._

## Theme

**Author Operational Knowledge** into the existing Documentation Foundation. The framework (0.11.0)
defines **where** documentation belongs; Release 0.12 creates **what belongs there** — high-quality
operational documentation — then reviews, validates, reconciles, automates, and finally publishes.
Documentation/governance-focused; non-regulated.

## Lifecycle (this release's ordering principle)

**Author → Review → Validate → Reconcile → Automate → Publish** — not automate-then-author-later.

## Current-state baseline (why this scope)

- Register `docs/registers/pages.yml`: **554 rows — 489 `planned`** (framework is an empty shelf).
- **23 legacy 360OS/Atlas pages** remain `manual_review` (reconcile only after replacements exist).
- Register **does not yet publish to Confluence** (`docs_sync` deferred to late in this release).
- 0.10.0 Insurance pages not yet re-parented; skeleton pages carry no `area:` labels yet.
- DoD is **advisory** (D6); CI `build` green on `v0.11.0`.

## In scope

| # | Item | Phase | Deliverable |
|---|---|---|---|
| S1 | **Operations Manual authoring (primary)** | P1 | Operational docs for the priority areas, authored from **verified facts** into the framework; scaffold-not-fabricate where facts are unavailable |
| S2 | Documentation Authoring Standard | P2 | Editorial standard (structure/style/terminology/cautions/troubleshooting/review/ownership/revision-history/cross-links/readiness) — **does not replace architecture or templates** |
| S3 | Documentation quality review | P3 | Validate authored pages with the **existing** DoD tooling; correct **before** migration |
| S4 | Legacy Atlas reconciliation | P4 | Per-page retain/merge/split/replace/archive **only after replacement exists**; preserve identifiers + audit history; archive-not-delete |
| S5 | Publishing automation | P5 | `docs_sync.py` — idempotent, dry-run, deterministic, publication validation, register sync; **reuse 0.11 tooling** |
| S6 | Controlled Confluence publication | P6 | Sync git-canonical docs after dry-run; preserve IDs/hierarchy/metadata/legacy refs; never publish AD-5-gated |

### P1 priority areas
IT Operations · Microsoft 365 · Active Directory · Windows Server · SonicWall · Networking · Backup &
Disaster Recovery · Schwab Operations · AssetMark Operations · TaxDome · Wealthbox · Drake Tax ·
Security Operations · Client onboarding workflows · Client servicing workflows · Internal SOPs.

_The objective is to **populate** the framework with high-quality operational knowledge — not to
expand the framework itself._

## Out of scope (deferred)

| # | Item | Reason |
|---|---|---|
| O1 | AD-5 compliance approval / naming a compliance reviewer | External; not a code action |
| O2 | Regulated Insurance rule-set authoring (suitability, replacement/1035, licensing, CE) | **AD-5-blocked** |
| O3 | Blocking documentation enforcement (advisory → blocking) | D6 keeps 0.12 advisory |
| O4 | Application capability development unrelated to documentation / app features / migrations | 0.12 is docs/authoring only |
| O5 | Framework or template redesign | 0.12 populates the framework; D1–D10 and templates unchanged |
| O6 | Any modification to 0.11.0 artifacts / `v0.11.0` | Immutable (defect-fix only) |

## Guardrails

- **Author only from verified facts** — never infer or fabricate configs/procedures; scaffold + mark
  `draft`/"SME-completion required" where facts are unavailable.
- **No AD-5 content** authored; regulated topics scaffolded as gated + unpublished only.
- **Reconcile only after replacement exists**; **archive-not-delete**; preserve identifiers + audit
  history.
- Publishing (P5/P6) publishes only **git-canonical, reviewed, non-gated** rows; dry-run-first;
  preserve page IDs/hierarchy/metadata.
- One canonical home per page; DoD stays **advisory**; framework/templates unchanged.
- Michael Shelton = business/operational owner only — **not** regulatory certification.
- `v0.11.0` and 0.10.0 tags/artifacts remain intact.

## Definition of "done" for 0.12 scope

Priority-area (or approved subset) pages authored from verified facts (nothing fabricated); Authoring
Standard published and applied; authored pages pass the existing DoD review; legacy reconciliation
executed only where replacements exist (identifiers/audit preserved, nothing deleted); `docs_sync`
implemented (idempotent, dry-run, deterministic, register-synced); controlled publication preserves
IDs/hierarchy/metadata and publishes no AD-5-gated/draft page; DoD still advisory; RC-validated;
`v0.12.0` tagged with a dated CHANGELOG entry.
