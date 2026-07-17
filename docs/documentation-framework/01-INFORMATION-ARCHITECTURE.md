# Deliverable 1 — Master Information Architecture (360 Wealth Consulting Operations Manual)

The permanent shape of the whole-company manual. Every area is a subtree with the **same**
template library; an **area profile** selects the relevant document types. One IA for software,
infrastructure, and business operations.

**Confluence site:** `360wealthconsulting.atlassian.net` · **Space:** 360 Wealth Consulting
Operations (`3WCO`).

## 1. Space tree

```
360 Wealth Consulting — Operations Manual (space 3WCO)
│
├── 00 · Company Home
│     Executive overview of the firm; the four domains; "start here"; org map link.
│
├── 01 · How This Manual Works
│     ├── Documentation Standard        (renders docs/documentation-framework/README.md)
│     ├── Area Profiles                 (Software / Infrastructure / Business Operations)
│     ├── Contribution Guide            (canonical-source rules; one owner per page)
│     ├── Definition of Done            (docs obligations per release / infra / process change)
│     └── Page Template Library         (all document types, as Confluence templates)
│
├── 10 · Client-Facing Capabilities            [profile: Software (+ Business where noted)]
│     ├── Client360 (platform)                 (cross-module architecture; the software spine)
│     ├── Tax Operations
│     ├── Wealth Management
│     ├── Insurance
│     ├── Employee Benefits
│     ├── Retirement Plans
│     ├── CRM
│     ├── Work Management
│     ├── Document Management
│     ├── Reporting
│     └── AI & Automation
│
├── 20 · Technology & Infrastructure           [profile: Infrastructure]
│     ├── Microsoft 365
│     ├── Active Directory
│     ├── Networking
│     ├── Servers
│     ├── Security
│     └── Disaster Recovery
│
├── 30 · Business Operations                    [profile: Business Operations]
│     ├── Compliance
│     ├── Vendor Management
│     ├── Office Operations
│     ├── HR
│     ├── Accounting
│     └── Marketing
│
├── 40 · Cross-Platform / Shared                (documented once; every area links, never copies)
│     ├── Platform Architecture         (PRODUCTION_ARCHITECTURE.md)
│     ├── Global Security & Identity     (IDENTITY_AUTHORIZATION_AUDIT.md)
│     ├── Global Exception Engine        (ADR_EXCEPTION_ENGINE_SCOPE.md)
│     ├── Global Workflow Engine         (WORKFLOW_PROCESS_AUTOMATION.md)
│     ├── Design System / UI             (UI_DESIGN_SYSTEM.md)
│     ├── Glossary & Definitions         (single company glossary)
│     ├── Operating Calendar & Key Dates (firm-wide deadlines)
│     └── Architecture Decisions (ADR / DEC index)
│
├── 80 · Libraries & Programs                   (cross-area aggregators — no duplication)
│     ├── SOP Library                    (index of every SOP across all areas)
│     ├── Training                       (index of every learning path / onboarding)
│     └── Release Management             (release process, change management, the DoD itself)
│
└── 90 · Registers & Governance
      ├── Publication Register            (promoted DOCUMENTATION_CROSSWALK.md — all areas)
      ├── Ownership Directory             (owner/reviewer per area & page)
      ├── Review Calendar                 (next-review dates; overdue queue)
      ├── Vendor & Contract Register       (firm-wide vendors, SLAs, renewals)
      ├── Asset & Configuration Inventory  (systems/servers/network/AD/M365/licenses)
      ├── Controls & Compliance Register   (controls, evidence, audit calendar)
      └── Documentation Backlog & Gaps     (from 04-GAP-ANALYSIS.md)
```

## 2. Area profiles (which document types each area carries)

| Profile | Applied to | Document types (beyond core) |
|---|---|---|
| **Software Capability** | 10 · Client-Facing Capabilities | Architecture, Data Model, User Guide, Admin Guide, SOPs, Business Rules, Security & Permissions, Workflows, Exception Handling, Integrations, Reporting, Troubleshooting, FAQ, Training, Release Notes, Change Log, Related Capabilities |
| **Infrastructure** | 20 · Technology & Infrastructure | Architecture (topology), Asset & Config Inventory, Runbook, Business Continuity & DR Plan, Incident Response & Postmortem, Admin Guide, Security & Permissions, Integrations, Vendor & Contract Register, Change Record, Service Levels & KPIs |
| **Business Operations** | 30 · Business Operations | Policy, Roles & Responsibilities (RACI), SOPs, Checklists, Process Guide, Controls & Compliance Register, Operating Calendar & Key Dates, Vendor & Contract Register, Training, Reporting/KPIs |
| **Core (all profiles)** | every area | Executive Overview, Business Purpose, Related Capabilities, Change Record, Ownership (metadata), Review Cycle (metadata), Glossary link |

Client-facing capabilities are **Hybrid** where the business process is distinct from the
software: they carry the Software profile **and** the Business-Operations SOP/Policy/RACI/Calendar
types (e.g. Tax Operations documents both the Client360 Tax module *and* the firm's tax process).

## 3. Naming & identifiers

- **Page id:** `<AREA>-<TYPE>[-nn]` — e.g. `TAX-SOP-03`, `AD-RUNBOOK`, `HR-POLICY-01`, `DR-BCP`.
- **Area codes:** CLM360, TAXOPS, WLTH, INS, BEN, RET, CRM, WORK, DOC, RPT, AIA, M365, AD, NET,
  SRV, SEC, DR, CMP, VEND, OFFICE, HR, ACCT, MKT, SOPLIB, TRAIN, RELMGMT.
- **Type codes:** existing (EXEC, PURPOSE, ARCH, DATA, USERGUIDE, ADMINGUIDE, SOP, RULES, SEC,
  WF, EXC, INTEG, REPORT, TROUBLE, FAQ, TRAIN, RELNOTES, CHANGELOG, RELATED) + new (POLICY,
  RACI, CHECKLIST, RUNBOOK, BCDR, ASSET, VENDOR, INCIDENT, CONTROLS, CALENDAR, GLOSSARY).
- **Labels:** `domain:capabilities|infrastructure|operations|shared`, `area:<code>`,
  `type:<code>`, `profile:<software|infra|ops>`, `source:git|confluence`,
  `status:draft|published`, `review:<cadence>`.
- **Codes are position-scoped** in the page id (`<AREA>-<TYPE>`): a token reused as both an area
  and a type (e.g. `SEC`, `TRAIN`) never collides — `SEC-SEC` is the Security area's Security &
  Permissions page; `HR-SEC` is HR's. Area codes and type codes are disjoint namespaces by position.

## 4. Page properties (required on every page)

Same as before — Module/Area, Document type, Canonical source, Owner, Reviewer, Status,
Applicable release/effective date, Last reviewed, Review cycle, Next review — surfaced by a Page
Properties Report into the Ownership Directory and Review Calendar (so **Ownership** and **Review
Cycle** are living views, not hand-maintained lists).

## 5. Provisioning a new area

1. Create the area page under its domain (`10/20/30`) from the **Area Shell** template for its
   **profile** (clones the profile's document types, pre-labelled, canonical-source set).
2. Add a Publication Register row per page (status = `planned`).
3. Assign owner/reviewer.
4. Fill Git-canonical pages by linking sources; author Confluence-canonical pages per the roadmap.

One-click provisioning applies equally to a software module and an HR function — that is the
point of a single architecture.
