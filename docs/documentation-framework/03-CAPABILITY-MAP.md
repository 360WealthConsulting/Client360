# Deliverable 3 — Company-Wide Capability Map

All 26 areas of the 360 Wealth Consulting Operations Manual placed into the framework, with
profile, current canonical source(s), and maturity. Seeds the Publication Register. Legend:
**G** = exists in Git · **C** = drafted in Confluence · **D** = derivable but no page · **—** = gap.

## Domain 10 — Client-Facing Capabilities (profile: Software; Hybrid where noted)

| Area | Code | Canonical source(s) today | Maturity |
|---|---|---|---|
| Client360 (platform) | CLM360 | `EPIC_4_PRACTICE_MANAGEMENT_PLATFORM.md`, `README.md`, baseline migrations (deployment arch `PRODUCTION_ARCHITECTURE.md` canonical in **40 · Cross-Platform**; CLM360 links it) | Platform arch strong; exec/user gap |
| Tax Operations | TAXOPS | `EPIC_5_TAX_PRACTICE_PLATFORM.md`, `TAX_*` , `app/services/tax_*` | Arch strong; **business process (SOP/policy/calendar) gap** |
| Wealth Management | WLTH | `SCHWAB_PORTFOLIO_ENGINE.md`, `app/services/portfolio*` | Arch strong; ops gap |
| Insurance | INS | `RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md`, `app/services/insurance*`, Commissions SOP draft | Arch strong; SOP started; drafts in register |
| Employee Benefits | BEN | `RELEASE_0.9.11_BENEFITS_ARCHITECTURE.md`, `app/services/benefits_*` | Arch strong; 3 Confluence drafts |
| Retirement Plans | RET | retirement compliance in `benefits_obligations.py` (canonical in **Employee Benefits**; RET links it) | No dedicated pages — gap |
| CRM | CRM | `RELATIONSHIP_ENGINE.md`, `app/services/{relationships,notes,activities}.py` | Arch partial; ops gap |
| Work Management | WORK | `WORK_MANAGEMENT_PLATFORM.md`, `app/services/work_*` | Arch strong; ops gap |
| Document Management | DOC | `CLIENT_PORTAL.md`, `app/services/{documents,microsoft_documents}.py` (Microsoft sync canonical in **Microsoft 365**; DOC links it) | Arch strong; ops gap |
| Reporting | RPT | `app/services/*_reporting.py`, `dashboard.py` | No consolidated doc — gap |
| AI & Automation | AIA | `app/services/{advisor_ai,work_intelligence,client_summary,tax_document_intelligence}.py` | No doc — gap |

## Domain 20 — Technology & Infrastructure (profile: Infrastructure)

| Area | Code | Canonical source(s) today | Maturity |
|---|---|---|---|
| Microsoft 365 | M365 | `MICROSOFT_CALENDAR_SYNC.md`, `MICROSOFT_DOCUMENT_SYNC.md`, `app/services/microsoft_*` (integration only) | Integration documented; **tenant admin/runbook/DR gap** |
| Active Directory | AD | — | **Greenfield** (topology, runbook, access model) |
| Networking | NET | — | **Greenfield** (topology, inventory, runbook) |
| Servers | SRV | partial in `PRODUCTION_ARCHITECTURE.md` | Deployment arch partial; **inventory/runbook/DR gap** |
| Security | SEC | `SECURITY_HARDENING_0.9.7.md`, `IDENTITY_AUTHORIZATION_AUDIT.md`, `app/security/*` | App security strong; **infra security/IR/policy gap** |
| Disaster Recovery | DR | `scripts/restore_rehearsal.sh`, `RELEASE_0.9.9_DEPLOYMENT_*` | Backup/restore rehearsal exists; **BCP/DR plan (RTO/RPO) gap** |

## Domain 30 — Business Operations (profile: Business Operations)

| Area | Code | Canonical source(s) today | Maturity |
|---|---|---|---|
| Compliance | CMP | `ADR_EXCEPTION_ENGINE_SCOPE.md`, AD-5 (insurance arch), `SPRINT_5_5_EXCEPTION_DESIGN.md` | Software-compliance strong; **controls register / policies / audit calendar gap** |
| Vendor Management | VEND | — | **Greenfield** (vendor & contract register, SLAs, renewals) |
| Office Operations | OFFICE | — | **Greenfield** (facilities, procedures, checklists) |
| HR | HR | — | **Greenfield** (policies, onboarding/offboarding, RACI, training) |
| Accounting | ACCT | revenue categories (`service_revenue`), insurance commissions | Software revenue partial; **finance SOP/policy/calendar gap** |
| Marketing | MKT | — | **Greenfield** (brand, campaigns, KPIs, procedures) |

## Domain 80 — Libraries & Programs (cross-area aggregators)

| Area | Code | Source today | Maturity |
|---|---|---|---|
| SOP Library | SOPLIB | Insurance Commissions SOP draft; Benefits SOP drafts | Index not yet built; 1 exemplar + drafts |
| Training | TRAIN | — | **Greenfield** (learning paths, onboarding) |
| Release Management | RELMGMT | `CHANGELOG.md`, `scripts/release.sh`, `docs/RELEASE_*`, RC validation docs | Release process well-established in Git; not yet a manual page |

## Coverage summary — minimum-viable set by domain

| Domain | Architecture/Topology | Data/Config/Inventory | Ops (User/Runbook/SOP) | Policy/Controls | Exec/Purpose | Change/Release |
|---|---|---|---|---|---|---|
| 10 Client-Facing | G (most) / — (AI, RPT, RET) | D | — (near-greenfield) | (rules G) | — | CHANGELOG G* |
| 20 Infrastructure | partial (M365, SRV) / — (AD, NET) | — | — | — | — | RELEASE docs G |
| 30 Business Ops | n/a | — | — | — (CMP partial) | — | — |
| 80 Libraries | — | — | 1 SOP exemplar | — | — | Release process G |

`G*` = one global CHANGELOG; per-area Release Notes/Change Records not yet sliced.

## Single-home resolution for shared sources (no duplicate mappings)

A handful of sources are *used by* several areas. To honour "every capability maps into exactly
one place," each is **owned by one area** (or the Cross-Platform node); every other area **links**
it and never re-documents it. This table is authoritative over the per-area rows above.

| Shared source | Canonical home (owns it) | Linked (not duplicated) by |
|---|---|---|
| `PRODUCTION_ARCHITECTURE.md` (deployment/topology) | 40 · Cross-Platform → Platform Architecture | Client360, Servers, IT |
| `IDENTITY_AUTHORIZATION_AUDIT.md` | 40 · Cross-Platform → Global Security & Identity | Security, Administration, every module's Security & Permissions page |
| `ADR_EXCEPTION_ENGINE_SCOPE.md` (exception engine) | 40 · Cross-Platform → Global Exception Engine | Compliance, every module's Exception Handling page |
| `WORKFLOW_PROCESS_AUTOMATION.md` (workflow engine) | 40 · Cross-Platform → Global Workflow Engine | every module's Workflows page |
| `MICROSOFT_CALENDAR_SYNC.md`, `MICROSOFT_DOCUMENT_SYNC.md` | Microsoft 365 | Document Management, CRM (calendar) |
| retirement compliance in `benefits_obligations.py` | Employee Benefits | Retirement Plans |
| `CLIENT_PORTAL.md` | Document Management | Insurance/portal consumers (Phase 7) |
| `service_revenue` / insurance-commission revenue | Insurance / Reporting (software definition) | Accounting (business process) |
| `SECURITY_HARDENING_0.9.7.md` | Security | IT, every module's Security page |

**Rule:** a page's `canonical_source` names its owning area's artifact; a consuming area's page is
a *Related Capabilities* link. No source is the canonical home of two areas — the capability-map
expression of the framework's one-canonical-home principle.

## What this tells us

- **Software architecture is broadly present** for client-facing capabilities — surface &
  standardize, don't recreate.
- **Infrastructure and business-operations areas are largely greenfield** — AD, Networking,
  Vendor Management, Office Operations, HR, Marketing have essentially no documentation today.
- **DR/BCP, Vendor/Contract, Controls, and Asset inventories** are the highest-risk missing
  operational artifacts.
- **Release Management already exists as a strong Git process** — it becomes the exemplar for
  documenting a *program*, and the home of the Definition of Done.

Gaps and priorities: [`04-GAP-ANALYSIS.md`](04-GAP-ANALYSIS.md).
