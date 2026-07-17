---
title: "Governance — Controls & Compliance Register (skeleton)"
area: "CMP"
profile: operations
doc_type: "CONTROLS"
canonical_source: git
owner: "Michael Shelton (business owner)"
reviewer: "UNFILLED (compliance reviewer — AD-5)"
status: planned
last_reviewed: "TBD"
review_cycle: semiannual
next_review: "TBD"
compliance_gate: "AD-5"
---

# `controls/` — Controls & Compliance Register

> **Phase-A skeleton (Release 0.11.0 · P2).** Structure and guidance only. **No control, evidence
> record, audit-calendar entry, or compliance rule is authored here yet.** Controls authoring is
> roadmap **Phase B/D** and, for regulated scope, **AD-5-gated**.

## Purpose

Holds the Git-canonical `CONTROLS` register: control catalogue (id, objective, owner), regulatory
mapping, evidence & attestations, audit calendar, findings & remediation. Feeds the Compliance area
and the Registers node (90).

## Permitted artifact types

`CONTROLS` only. Firm mandates are `policies/`; DR is `dr/`.

## Required metadata

Per `../CONTRIBUTING.md`. Placeholders: **owner** = Michael Shelton (business), **reviewer** =
`UNFILLED` (the accountable compliance reviewer), **status** = `planned`, **review_cycle** =
`semiannual`, dates = `TBD`, **compliance_gate** = `AD-5`.

## Naming examples

`controls-register.md`, `cmp-audit-calendar.md`.

## Canonical-source guidance

Git-canonical (PR-reviewed, evidence trail). Confluence renders a summary + link.

## AD-5 note

The Controls Register governs regulated obligations. Regulated control sets (**suitability,
replacement/1035, licensing, CE**) carry `compliance_gate: AD-5` and **must not** be authored,
approved, or marked `published` while the accountable compliance reviewer is **UNFILLED**. Business
ownership (Michael Shelton) is operational only — **not** regulatory certification.

## Framework standards

`02-DOCUMENT-TYPE-TEMPLATES.md` (Controls type), `03-CAPABILITY-MAP.md` (Compliance area),
`06-SYNC-AND-DEFINITION-OF-DONE.md`.
