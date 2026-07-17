---
title: "Governance — Operating Calendar & Key Dates (skeleton)"
area: "GOV"
profile: operations
doc_type: "CALENDAR"
canonical_source: git
owner: "Michael Shelton (business owner)"
reviewer: "UNFILLED"
status: planned
last_reviewed: "TBD"
review_cycle: quarterly
next_review: "TBD"
compliance_gate: "none"
---

# `calendar/` — Operating Calendar & Key Dates (data)

> **Phase-A skeleton (Release 0.11.0 · P2).** Structure and guidance only. **No deadline, filing
> date, renewal, or schedule is authored here yet.** Calendar data authoring is a later phase.

## Purpose

Holds the Git-canonical `CALENDAR` data: recurring firm deadlines (tax dates, compliance filings,
renewals, reviews, close cycles) with owner, lead time, and source obligation. **One firm-wide
calendar**; areas filter their slice — never duplicated.

## Permitted artifact types

`CALENDAR` (data) only. Obligation logic that is code-driven stays in the application; this is the
firm-wide reference calendar.

## Required metadata

Per `../CONTRIBUTING.md`. Placeholders: **owner** = Michael Shelton (business), **reviewer** =
`UNFILLED`, **status** = `planned`, **review_cycle** = `quarterly`, dates = `TBD`.

## Naming examples

`operating-calendar.md`, `key-dates.yml`.

## Canonical-source guidance

Git-canonical data → rendered into Confluence (node 40 · Shared). Areas link their slice; no
duplication.

## AD-5 note

Regulated filing/deadline **rule sets** (e.g. licensing/CE renewal logic that constitutes a
compliance determination) remain **AD-5-gated**; only non-regulated operational dates are in scope
for later authoring. This skeleton authors none.

## Framework standards

`02-DOCUMENT-TYPE-TEMPLATES.md` (Operating Calendar type),
`01-INFORMATION-ARCHITECTURE.md` (Shared node 40 · singleton calendar).
