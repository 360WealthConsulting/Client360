# Contributing to `governance/`

> **Release 0.11.0 · Phase A (P2) skeleton.** This defines *how* governance artifacts will be
> contributed. It authors **no** governance content itself.

## Permitted document types

Only these Git-canonical governance types belong here (framework
`docs/documentation-framework/02-DOCUMENT-TYPE-TEMPLATES.md`):

| Type | Code | Directory |
|---|---|---|
| Policy | `POLICY` | `policies/` |
| Runbook | `RUNBOOK` | `runbooks/` |
| Business Continuity & DR Plan | `BCDR` | `dr/` |
| Controls & Compliance Register | `CONTROLS` | `controls/` |
| Asset & Configuration Inventory | `ASSET` | `inventory/` |
| Operating Calendar & Key Dates | `CALENDAR` | `calendar/` |

Staff-facing narrative (SOPs, User Guides, Training) is **Confluence-canonical** — it does not belong
here; link it from the register instead.

## Required metadata (front-matter on every artifact)

```yaml
---
title: "<Area> — <Document Type>"
page_id: "<AREA>-<TYPE>[-nn]"
area: "<area code>"                 # e.g. SEC, DR, HR, CMP, GOV
profile: infrastructure|operations
doc_type: "POLICY|RUNBOOK|BCDR|CONTROLS|ASSET|CALENDAR"
canonical_source: git              # governance/ is always git-canonical
git_source: "governance/<dir>/<file>.md"
confluence_page_id: "TBD"
owner: "<role / name>"             # accountable
reviewer: "<role / name>"          # independent of the author
status: planned|draft|published|needs_review
last_reviewed: "YYYY-MM-DD"
review_cycle: per_release|quarterly|semiannual|annual
next_review: "YYYY-MM-DD"
compliance_gate: "none|AD-5"       # if set, status MUST NOT be published
---
```

Use visible placeholders (`TBD`, `UNFILLED`) for unknowns. **Do not invent** reviewers, approval
dates, or compliance credentials.

## Filename conventions

- Lowercase, hyphenated: `governance/<dir>/<area>-<topic>.md` (e.g. `policies/sec-acceptable-use.md`,
  `dr/dr-master-plan.md`).
- One artifact per file; the filename matches the `page_id` intent.

## Owner & reviewer requirements

- Every artifact names an **accountable owner** and an **independent reviewer** (reviewer ≠ author
  where possible).
- **Michael Shelton** may be recorded as the **business owner** for workflow/operational
  requirements.

## Review-cycle requirements

- Every artifact sets `review_cycle` and `next_review`. DR/continuity and controls default to at
  least **semiannual**; policies at least **annual**. Overdue items are chased by the Review Calendar
  (later phase).

## Approval expectations

- Business-process approval is by the artifact **owner**.
- Regulated material additionally requires the **accountable compliance reviewer** (see AD-5).

## Pull-request expectations

- All changes land via **pull request** with the reviewer assigned.
- The PR follows the Definition of Done (framework §06): metadata complete, canonical home correct,
  register row updated (in later phases), no secrets/PII.

## Canonical-source rules

- `governance/` artifacts are **git-canonical**; Confluence gets a generated summary + link only.
- Never author the same content in two places; link, don't copy.

## Security & sensitive-data restrictions

- **No** secrets, credentials, tokens, certificates, private keys, endpoints, or **client/PII**.
- Reference sensitive values by name (secret store / authoritative system), never by value.

## AD-5 handling

- Regulated rule sets (**suitability**, **replacement/1035**, **licensing**, **continuing-education**)
  carry `compliance_gate: AD-5` and **must not** be authored, approved, or marked `published` while
  the accountable compliance reviewer is **UNFILLED**.

## Business approval vs regulatory certification

- A **business owner's** approval (e.g. Michael Shelton) authorizes **operational** use only.
- It is **not** regulatory certification. Do not represent business sign-off as compliance
  certification unless the person is **separately confirmed** as the appropriately licensed and
  authorized **compliance principal**.

## Prohibition on marking regulated material approved/publishable

Regulated material must **not** be marked approved or publishable without the accountable compliance
reviewer's sign-off artifact. Any regulated artifact defaults to `status: draft`/`planned` with
`compliance_gate: AD-5` until that condition is met.
