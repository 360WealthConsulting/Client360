---
title: "Governance — Business Continuity & DR (skeleton)"
area: "DR"
profile: infrastructure
doc_type: "BCDR"
canonical_source: git
owner: "Michael Shelton (business owner)"
reviewer: "UNFILLED"
status: planned
last_reviewed: "TBD"
review_cycle: semiannual
next_review: "TBD"
compliance_gate: "none"
---

# `dr/` — Business Continuity & Disaster-Recovery plans

> **Phase-A skeleton (Release 0.11.0 · P2).** Structure and guidance only. **No DR/continuity plan,
> RTO/RPO value, or recovery procedure is authored here yet.** DR/BCP authoring is roadmap **Phase
> B** — a separate, approved initiative.

## Purpose

Holds Git-canonical `BCDR` documents: critical services & dependencies, RTO/RPO per service, recovery
procedures (linking `runbooks/`), roles & communications tree, backup/restore strategy, and test
schedule/results — one per infrastructure area plus a firm-wide master.

## Permitted artifact types

`BCDR` only. Operational steps belong in `runbooks/`.

## Required metadata

Per `../CONTRIBUTING.md`. Placeholders: **owner** = Michael Shelton (business), **reviewer** =
`UNFILLED`, **status** = `planned`, **review_cycle** = `semiannual`, dates = `TBD`.

## Naming examples

`dr-master-plan.md`, `srv-dr.md`, `m365-dr.md`.

## Canonical-source guidance

Git-canonical (PR-reviewed, audit trail). Confluence renders a summary + link. Builds on the existing
restore-rehearsal (`scripts/restore_rehearsal.sh`) referenced by link, not copied.

## Framework standards

`02-DOCUMENT-TYPE-TEMPLATES.md` (BCDR type), `04-GAP-ANALYSIS.md` (DR/BCP is a high-risk gap),
`05-IMPLEMENTATION-ROADMAP.md` (Phase B).
