# Deliverable 2 — Document-Type Templates & Area Profiles

One reusable skeleton per document type, spanning **software and business operations**. In Git
these are front-matter + section stubs; in Confluence they become Space Templates. Each area
applies a **profile** that selects the relevant subset. Ownership and Review Cycle are shared
front-matter on **every** page — not separate documents.

## Shared front-matter (mandatory on every page)

```yaml
---
title: "<Area> — <Document Type>"
page_id: "<AREA>-<TYPE>[-nn]"       # e.g. TAX-SOP-03, AD-RUNBOOK, HR-POLICY-01
area: "<area code>"                 # CLM360|TAXOPS|…|HR|ACCT|MKT|…
profile: software|infrastructure|operations
doc_type: "<type code>"
canonical_source: git|confluence
git_source: "docs/… | app/… | governance/… | n/a"
confluence_page_id: "<id|TBD>"
owner: "<role / name>"             # accountable                         (Ownership)
reviewer: "<role / name>"          # independent where possible          (Ownership)
status: draft|published
effective_or_release: "vX.Y.Z | YYYY-MM-DD"
last_reviewed: "YYYY-MM-DD"        # (Review Cycle)
review_cycle: per_release|quarterly|semiannual|annual
next_review: "YYYY-MM-DD"          # (Review Cycle)
related: ["<page_id>", "…"]
---
```

> **Canonical-source rule (unchanged):** exactly one system owns the words. Git-canonical pages
> render a summary + link and are refreshed from source; Confluence-canonical pages hold the
> content and are tracked by a register row. Version-controlled operational artifacts (policies,
> DR plans, runbooks, controls, infra config) SHOULD be **git-canonical** under `governance/` or
> `docs/` so they get PR review and change history.

---

## A. Software Capability types (18 content + 2 metadata) — unchanged

Executive Overview `EXEC`·C · Business Purpose `PURPOSE`·C · Architecture `ARCH`·G · Data Model
`DATA`·G · User Guide `USERGUIDE`·C · Administrator Guide `ADMINGUIDE`·C · SOPs `SOP`·C ·
Business Rules `RULES`·G · Security & Permissions `SEC`·G · Workflows `WF`·G · Exception Handling
`EXC`·G · Integrations `INTEG`·G · Reporting `REPORT`·G/C · Troubleshooting `TROUBLE`·C · FAQ
`FAQ`·C · Training `TRAIN`·C · Release Notes `RELNOTES`·G · Change Log `CHANGELOG`·G · Related
Capabilities `RELATED`·C · Ownership `OWNERSHIP`·meta · Review Cycle `REVIEW`·meta.
*(Full skeletons as previously specified; `C`=Confluence-canonical, `G`=Git-canonical.)*

---

## B. New document types for business & IT operations

### 22. Policy — `POLICY` · *Git (governance/) → Confluence*
`Statement` · `Scope & applicability` · `Requirements/standards` · `Roles & enforcement` ·
`Exceptions & waivers` · `Related regulations` · `Effective date & version`. Authoritative firm
rules (HR, security, acceptable-use, data-retention, compliance). Distinct from software Business
Rules; version-controlled for audit.

### 23. Roles & Responsibilities (RACI) — `RACI` · *Confluence (Git for org-as-code optional)*
`Process/activity list` · `RACI matrix (Responsible / Accountable / Consulted / Informed)` ·
`Backup/escalation` · `Segregation-of-duty notes`. Process-level accountability (complements
page-level Ownership).

### 24. Checklist — `CHECKLIST` · *Confluence*
`Trigger / when to run` · `Point-of-use steps (checkbox)` · `Sign-off` · `Links to the governing
SOP`. Onboarding/offboarding, month-end close, incident triage, release checklist.

### 25. Runbook — `RUNBOOK` · *Git (governance/) → Confluence*
`System & scope` · `Routine procedures (start/stop, patch, backup, restore, rotate)` ·
`Emergency procedures (failover, recovery)` · `Verification` · `Contacts & escalation`.
Operational + emergency steps for infrastructure and systems.

### 26. Business Continuity & DR Plan — `BCDR` · *Git (governance/) → Confluence*
`Critical services & dependencies` · `RTO / RPO per service` · `Recovery procedures (link
runbooks)` · `Roles & communications tree` · `Backup/restore strategy` · `Test schedule &
results`. One per infrastructure area + a firm-wide master (DR area).

### 27. Asset & Configuration Inventory — `ASSET` · *Git (config) / register*
`Systems / servers / network devices / AD & M365 tenants / endpoints` · `Owner & lifecycle` ·
`Licenses & renewals` · `Dependencies` · `Configuration source/link`. CMDB-lite; rolled into the
Registers node. Prefer generation from config where possible.

### 28. Vendor & Contract Register — `VENDOR` · *Confluence / register*
`Vendor & contacts` · `Service provided` · `Contract & term` · `SLA` · `Renewal/notice dates` ·
`Data-processing/DPA & risk tier` · `Owner`. Firm-wide register in `90`; per-area views link it.

### 29. Incident Response & Postmortem — `INCIDENT` · *Confluence (+ Git for IR policy)*
`Detection` · `Severity classification` · `Containment / eradication / recovery` ·
`Communications` · `Postmortem (timeline, root cause, actions)`. Security/IT/operational
incidents; org-level analogue of software Exception Handling.

### 30. Controls & Compliance Register — `CONTROLS` · *Git (governance/) / register*
`Control catalogue (id, objective, owner)` · `Regulatory/obligation mapping` · `Evidence &
attestations` · `Audit calendar` · `Findings & remediation`. Feeds the Compliance area and the
Registers node.

### 31. Operating Calendar & Key Dates — `CALENDAR` · *Git (data) → Confluence*
`Recurring deadlines (tax dates, compliance filings, renewals, reviews, close cycles)` ·
`Owner & lead time` · `Source obligation`. One firm-wide calendar in `40 · Shared`; areas filter
their slice — not duplicated.

### 32. Glossary & Definitions — `GLOSSARY` · *Git → Confluence (singleton)*
`Term → definition → canonical page`. One company glossary in `40 · Shared`; areas link terms,
never redefine them.

*(Optional 33. Service Levels & KPIs — `KPI` · Confluence — service catalogue, SLAs, and
operational metrics for IT and business areas; adopt if/when metrics are formalized.)*

---

## C. Profile → document-type selection

| Document type | Software | Infrastructure | Business Ops |
|---|:--:|:--:|:--:|
| Executive Overview / Business Purpose / Related Capabilities / Change Record / Ownership / Review / Glossary link (core) | ✅ | ✅ | ✅ |
| Architecture · Data Model | ✅ | ✅ (topology/config) | — |
| User Guide / Process Guide · Admin Guide | ✅ | ✅ (admin) | ✅ (process) |
| Business Rules | ✅ | — | (→ Policy) |
| Workflows · Integrations · Reporting | ✅ | ✅ (integrations) | ✅ (process/KPIs) |
| Exception Handling | ✅ | (→ Incident) | (→ Incident) |
| Security & Permissions | ✅ | ✅ | ✅ (access) |
| Troubleshooting / FAQ / Training | ✅ | ✅ | ✅ |
| Release Notes | ✅ | (→ Change Record) | — |
| **Policy** | (rules) | ✅ | ✅ |
| **RACI** | ✅ | ✅ | ✅ |
| **Checklist** | ✅ | ✅ | ✅ |
| **Runbook** | — | ✅ | (ops procedures) |
| **Business Continuity & DR** | — | ✅ | (dept continuity) |
| **Asset & Config Inventory** | — | ✅ | (assets) |
| **Vendor & Contract Register** | (integrations) | ✅ | ✅ |
| **Incident Response & Postmortem** | (exceptions) | ✅ | ✅ |
| **Controls & Compliance Register** | (rules) | ✅ | ✅ |
| **Operating Calendar & Key Dates** | ✅ (deadlines) | ✅ (maintenance) | ✅ |

## D. Minimum-viable page set per profile ("documented")

- **Software:** Executive Overview, Business Purpose, Architecture, Data Model, User Guide,
  Security & Permissions, Release Notes, Change Log + Ownership/Review.
- **Infrastructure:** Executive Overview, Architecture/Topology, Asset & Config Inventory,
  Runbook, Security & Permissions, DR Plan (or link to DR master), Change Record + Ownership/Review.
- **Business Ops:** Executive Overview, Business Purpose, Policy, RACI, core SOPs + Checklists,
  Operating Calendar + Ownership/Review.

Remaining types are demanded by the Definition of Done when the relevant surface changes.
