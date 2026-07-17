---
title: "Governance — Runbooks (skeleton)"
area: "GOV"
profile: infrastructure
doc_type: "RUNBOOK"
canonical_source: git
owner: "Michael Shelton (business owner)"
reviewer: "UNFILLED"
status: planned
last_reviewed: "TBD"
review_cycle: semiannual
next_review: "TBD"
compliance_gate: "none"
---

# `runbooks/` — System operational & emergency procedures

> **Phase-A skeleton (Release 0.11.0 · P2).** Structure and guidance only. **No runbook is authored
> here yet.** Runbook authoring is roadmap **Phase B** (operational risk floor) — a separate,
> approved initiative.

## Purpose

Holds Git-canonical `RUNBOOK` documents: routine (start/stop, patch, backup, restore, rotate) and
emergency (failover, recovery) procedures for infrastructure and systems, plus verification and
escalation contacts.

## Permitted artifact types

`RUNBOOK` only. DR/continuity plans are `dr/`; asset records are `inventory/`.

## Required metadata

Per `../CONTRIBUTING.md`. Placeholders: **owner** = Michael Shelton (business), **reviewer** =
`UNFILLED`, **status** = `planned`, **review_cycle** = `semiannual`, dates = `TBD`.

## Naming examples

`ad-runbook.md`, `m365-tenant-runbook.md`, `srv-backup-restore.md`.

## Canonical-source guidance

Git-canonical (PR-reviewed); Confluence gets a summary + link. **No secrets, endpoints, or
credentials** — reference the secret store by name.

## Framework standards

`02-DOCUMENT-TYPE-TEMPLATES.md` (Runbook type), `01-INFORMATION-ARCHITECTURE.md` (Infrastructure
profile), `05-IMPLEMENTATION-ROADMAP.md` (Phase B).
