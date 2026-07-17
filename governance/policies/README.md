---
title: "Governance — Policies (skeleton)"
area: "GOV"
profile: operations
doc_type: "POLICY"
canonical_source: git
owner: "Michael Shelton (business owner)"
reviewer: "UNFILLED"
status: planned
last_reviewed: "TBD"
review_cycle: annual
next_review: "TBD"
compliance_gate: "none"
---

# `policies/` — Firm policies & standards

> **Phase-A skeleton (Release 0.11.0 · P2).** Structure and guidance only. **No policy is authored
> here yet.** Actual policies are later, separately approved content under the Definition of Done.

## Purpose

Holds the firm's authoritative **policies/standards** (Git-canonical `POLICY` documents): security,
data-retention, acceptable-use, HR, and compliance mandates. Distinct from software Business Rules
(code-enforced logic) — a Policy is a firm mandate.

## Permitted artifact types

`POLICY` only. Staff how-to (SOPs) is Confluence-canonical; system steps are `runbooks/`.

## Required metadata

Per `../CONTRIBUTING.md` front-matter. Placeholders until authored: **owner** = Michael Shelton
(business), **reviewer** = `UNFILLED`, **status** = `planned`, **review_cycle** = `annual`,
**last_reviewed/next_review** = `TBD`.

## Naming examples

`sec-acceptable-use.md`, `data-retention.md`, `hr-code-of-conduct.md` (`<AREA>-POLICY-nn` intent).

## Canonical-source guidance

Git-canonical here (PR-reviewed, versioned); Confluence renders a summary + link only.

## AD-5 note

Any policy touching regulated insurance activity (**suitability, replacement/1035, licensing, CE**)
carries `compliance_gate: AD-5` and stays `draft`/`planned` — never `published` — until the
accountable compliance reviewer (currently **UNFILLED**) signs off.

## Framework standards

`docs/documentation-framework/02-DOCUMENT-TYPE-TEMPLATES.md` (Policy type),
`06-SYNC-AND-DEFINITION-OF-DONE.md` (canonical home, DoD).
