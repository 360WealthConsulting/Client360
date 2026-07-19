# ADR-014 — Engineering Backlog & Roadmap Governance

- **Status:** Accepted
- **Date:** 2026-07-18 (accepted during E1.6; persisted 2026-07-19)
- **Relates to:** [ADR-013](ADR-013-repository-reconciliation.md) (in-place reconciliation);
  governs the current-track roadmap at `docs/architecture/REIMPLEMENTATION_ROADMAP.md`.

## Context
The current work is a strict, phase-gated **re-implementation / reconciliation** track
(ADR-013) layered on the existing Client360 application. Its epic sequence and feature
numbering were, until now, defined only in conversation (the "Engineering Backlog") and
**not persisted** in the repository. Meanwhile the repository already contains a **legacy
product roadmap** (`docs/ROADMAP.md`, `EPIC_4_PRACTICE_MANAGEMENT_PLATFORM.md`,
`EPIC_5_TAX_PRACTICE_PLATFORM.md`, `EPIC_5_REVISED_PLAN.md`) whose epic numbering is
**different** (legacy Epic 4 = Practice Management; legacy Epic 5 = Tax Practice Platform,
~55% shipped in v0.9.x). This produced an "Epic N" collision and an unpersisted sequence.

## Problem statement
There must be one authoritative, persisted source of truth for the current-track epic
sequence, a clear rule separating it from the legacy roadmap, and governance for how that
roadmap is maintained — **without** duplicating architectural decisions into planning
documents or rewriting legacy history.

## Decision
Establish the **canonical current-track roadmap** at
`docs/architecture/REIMPLEMENTATION_ROADMAP.md` as the single source of truth for
implementation **sequencing** (epic numbering, epic status, feature sequencing, milestone
history, release progression). **ADRs remain architecture decision records only** and do
**not** duplicate the roadmap; each epic is governed by its own ADR (e.g. ADR-015 → Epic 3,
ADR-016 → Epic 4, ADR-017 → Epic 5) which the roadmap references.

This ADR defines the **governance rules** for maintaining that roadmap:

### Roadmap ownership
- The roadmap is a controlled document. Changes to epic scope, numbering, status, or
  sequencing require explicit product-owner approval (the same approval gate used for
  feature acceptance). No epic is "adopted" until recorded in the roadmap.

### Numbering rules
- **Current re-implementation track:** epics are `Epic N — {Theme}`; features are `F{N}.x`;
  epic-milestone tags are `v0.{N}-{slug}` (e.g. `v0.4-workflow-orchestration-foundation`).
  Unqualified **"Epic N" always means the current track**.
- **Legacy product track:** always qualified as **"Legacy Epic N — {Theme}"**. Legacy
  numbering is frozen and never reused or renumbered.
- Epic numbers are assigned in the roadmap **before** the epic's governing ADR is drafted.

### Track separation
- The two roadmaps are distinct and permanently distinguished: the legacy product roadmap
  (historical) and the current ADR-driven re-implementation roadmap (active). Legacy
  planning documents are retained unchanged, marked historical, and cross-referenced; they
  are neither renamed, renumbered, nor invalidated.

### Acceptance workflow
- Per-feature: **implement → validate → present → STOP for approval → commit after
  approval** (commits are not pushed and no PR/release/tag is created without separate
  authorization). Each epic begins with an accepted governing ADR and closes with a release
  checkpoint. The roadmap's status is updated only on acceptance.

### Change management
- Every epic/feature status transition (planned → in-progress → complete → released) is
  recorded in the roadmap. A new epic is added to the roadmap (number + theme + status)
  before its ADR; the ADR then references the roadmap for scope rather than re-deriving it.
  Milestone tags and their commits are recorded in the roadmap's milestone history.

## Alternatives considered
- **Keep the backlog conversational (status quo).** Rejected: it caused the unpersisted
  sequence and the Epic-5 numbering collision.
- **Put the roadmap inside ADRs.** Rejected: conflates architectural decisions with project
  planning; ADRs would churn on every status change.
- **Renumber or rewrite the legacy roadmap.** Rejected: violates ADR-013 (preserve history;
  additive only) and would erase the record of shipped v0.9.x work.

## Consequences
- **Positive:** one persisted source of truth for sequencing; the Epic-N collision cannot
  recur; ADRs stay focused on architecture; legacy history is preserved and clearly
  separated.
- **Neutral/limits:** two roadmaps continue to coexist by design (one historical, one
  active); the roadmap must be kept current as part of each acceptance.

## References
ADR-013; `docs/architecture/REIMPLEMENTATION_ROADMAP.md`; the legacy roadmap docs
(`docs/ROADMAP.md`, `docs/EPIC_4_PRACTICE_MANAGEMENT_PLATFORM.md`,
`docs/EPIC_5_TAX_PRACTICE_PLATFORM.md`, `docs/EPIC_5_REVISED_PLAN.md`); Engineering
Constitution §3.
