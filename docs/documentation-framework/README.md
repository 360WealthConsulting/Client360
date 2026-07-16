# 360 Wealth Consulting — Operations Manual Documentation Framework

> **Status: APPROVED IN PRINCIPLE — expanded to company-wide scope.** This directory is the
> permanent documentation standard for the **entire 360 Wealth Consulting Operations Manual** —
> every business area, not just the Client360 application. Client360 is **one capability** in
> the business. Documentation architecture only — no application code changes.

This framework makes documentation a **required deliverable (Definition of Done)** for every
software release, every completed phase, **and every material business-process change**, and
gives **every area of the company the same, predictable page structure** — so one manual can
document **software and business operations to the same standard** without duplication.

## The six deliverables (this directory)

| # | Deliverable | File |
|---|---|---|
| 1 | Master information architecture (whole company) | [`01-INFORMATION-ARCHITECTURE.md`](01-INFORMATION-ARCHITECTURE.md) |
| 2 | Document-type templates + area profiles | [`02-DOCUMENT-TYPE-TEMPLATES.md`](02-DOCUMENT-TYPE-TEMPLATES.md) |
| 3 | Company-wide capability map (26 areas) | [`03-CAPABILITY-MAP.md`](03-CAPABILITY-MAP.md) |
| 4 | Documentation gap analysis | [`04-GAP-ANALYSIS.md`](04-GAP-ANALYSIS.md) |
| 5 | Implementation roadmap | [`05-IMPLEMENTATION-ROADMAP.md`](05-IMPLEMENTATION-ROADMAP.md) |
| 6 | GitHub ↔ Confluence ↔ release sync + Definition of Done | [`06-SYNC-AND-DEFINITION-OF-DONE.md`](06-SYNC-AND-DEFINITION-OF-DONE.md) |

## 1. Core principles (unchanged; now company-wide)

1. **One documentation architecture for the entire business.** Software products, IT
   infrastructure, and business operations all live in one Operations Manual with one IA, one
   template library, and one register.
2. **One source of truth per page — never duplicate.** Each page has exactly one canonical home
   (Git *or* Confluence); the other system **links**, never copies.
3. **Git remains the technical source of truth; Confluence remains the operational publishing
   platform.** Version-controlled truth (software architecture/data model/rules, **and**
   policies, DR plans, runbooks, controls, infrastructure config) is canonical in **Git**;
   staff-facing operational guidance is canonical in **Confluence** (per **DEC-001**).
4. **Uniform structure, area-appropriate content.** Every area uses the same template library;
   an **area profile** (Software / Infrastructure / Business Operations) selects the relevant
   subset of document types so the standard fits both a software module and an HR function.
5. **Every page is owned and dated.** Owner, reviewer, canonical source, status, last-reviewed,
   and review-cycle are required metadata on every page.
6. **Documentation is Definition of Done** — for software releases *and* business-process
   changes. Enforced by the register + a docs gate (deliverable 6).
7. **Derive, don't restate.** Generated content (data models from migrations, security matrices
   from role seeds, release notes from the changelog, asset inventories from config) is
   rendered/linked, not hand-copied.

## 2. Client360 vs the business

Client360 is the **software platform** that supports several client-facing business
capabilities. In the manual:
- **Client360** (and its software modules) is documented once under *Client-Facing
  Capabilities* — the technical/software facet (Architecture, Data Model, Business Rules …).
- A **business capability** it supports (e.g. **Tax Operations**) documents the **business
  process** (Policy, SOPs, RACI, Operating Calendar …) and **links** to the Client360 software
  module via *Related Capabilities / Supporting Systems* — the software is documented once and
  referenced, never re-described.

## 3. Document types & profiles

The type catalogue is a **superset**; each area applies a **profile**:

- **Software Capability profile** — the original 21 types (Executive Overview, Business Purpose,
  Architecture, Data Model, User Guide, Admin Guide, SOPs, Business Rules, Security &
  Permissions, Workflows, Exception Handling, Integrations, Reporting, Troubleshooting, FAQ,
  Training, Release Notes, Change Log, Related Capabilities, Ownership, Review Cycle).
- **Infrastructure profile** — adds/uses: Architecture (topology), **Asset & Configuration
  Inventory**, **Runbook**, **Business Continuity & DR Plan**, **Incident Response &
  Postmortem**, Admin Guide, Security & Permissions, Integrations, **Vendor & Contract
  Register**, Change Record (Change Log), Service Levels & KPIs, + core.
- **Business Operations profile** — uses: **Policy**, **Roles & Responsibilities (RACI)**, SOPs,
  **Checklists**, Process Guide (User Guide analog), **Controls & Compliance Register**,
  **Operating Calendar & Key Dates**, **Vendor & Contract Register**, Training, + core.

**Additional document types introduced for business/IT operations** (deliverable 2 §New):
Policy · Roles & Responsibilities (RACI) · Checklist · Runbook · Business Continuity & DR Plan ·
Asset & Configuration Inventory · Vendor & Contract Register · Incident Response & Postmortem ·
Controls & Compliance Register · Operating Calendar & Key Dates · Glossary & Definitions.

Full catalogue, canonical homes, and profiles: [`02-DOCUMENT-TYPE-TEMPLATES.md`](02-DOCUMENT-TYPE-TEMPLATES.md).

## 4. The 26 business areas

**Client-Facing Capabilities (software + business):** Client360 · Tax Operations · Wealth
Management · Insurance · Employee Benefits · Retirement Plans · CRM · Work Management · Document
Management · Reporting · AI & Automation.
**Technology & Infrastructure:** Microsoft 365 · Active Directory · Networking · Servers ·
Security · Disaster Recovery.
**Business Operations:** Compliance · Vendor Management · Office Operations · HR · Accounting ·
Marketing.
**Libraries & Programs (cross-area):** SOP Library · Training · Release Management.

Sources, profiles, and coverage: [`03-CAPABILITY-MAP.md`](03-CAPABILITY-MAP.md).

## 5. Definition of Done (summary)

A change is "done" only when its area's documentation is current:
- **Software release/phase:** Change Log + Release Notes + every Git-canonical doc type touched
  (Architecture, Data Model, Business Rules, Workflows, Exception Handling, Integrations,
  Security, Reporting) + register row + flagged Confluence follow-ups.
- **Infrastructure change:** Asset/Config Inventory + Runbook + (if applicable) DR Plan / Change
  Record + register row.
- **Business-process change:** the affected Policy / SOP / RACI / Checklist / Controls register +
  register row.

A CI/register **docs gate** verifies the Git-side obligations. Full mechanism:
[`06-SYNC-AND-DEFINITION-OF-DONE.md`](06-SYNC-AND-DEFINITION-OF-DONE.md).
