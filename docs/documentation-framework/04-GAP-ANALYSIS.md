# Deliverable 4 — Documentation Gap Analysis (company-wide)

Gaps grouped by type (fastest/highest-leverage first), then by area risk.

## 1. Structural gaps (whole company)

| Gap | Impact | Effort | Fix |
|---|---|---|---|
| No single company-wide IA / templates / profiles | Every area bespoke; no coverage view | Low | Approve & provision the space shell + Area Shells per profile |
| Register covers only Benefits/Insurance | Can't track state across 26 areas | Low | Promote `DOCUMENTATION_CROSSWALK.md` → full register |
| Docs not in Definition of Done for infra/business changes | Ops docs drift; only software had any rigor | Med | Extend DoD + docs gate to infra & process changes (deliverable 6) |
| No GitHub↔Confluence sync | Manual copying → duplication/drift | Med | Implement `docs_sync` |
| No governance home for policies/DR/controls in Git | Audit-critical docs unversioned | Low | Add `governance/` tree (git-canonical policies, runbooks, DR, controls) |

## 2. Document-type gaps (across areas)

| Doc type | State | Priority |
|---|---|---|
| Data Model / Asset & Config Inventory | Derivable/absent; no page | **High** |
| Executive Overview / Business Purpose | Absent almost everywhere | High |
| User/Process Guide, Admin Guide, SOPs, Checklists | Near-greenfield (1 SOP exemplar) | **High** |
| **Policy / RACI / Controls Register** | Absent (business ops) | **High** (compliance risk) |
| **Runbook / BC-DR Plan / Incident Response** | Absent (infrastructure) | **High** (operational risk) |
| **Vendor & Contract Register** | Absent | High |
| Release Notes (per area) / Change Records | Global CHANGELOG only | Med |
| Troubleshooting / FAQ / Training | Absent | Med |
| Operating Calendar / Glossary | Absent (singletons) | Med |

## 3. Area risk ranking (worst first)

| Tier | Areas | Why |
|---|---|---|
| **Critical (greenfield + high risk)** | Active Directory, Networking, Servers, Disaster Recovery, HR, Vendor Management | No docs; operational/continuity/compliance exposure; bus-factor |
| **High** | Compliance (controls/policy/audit-calendar), Security (infra/IR/policy), AI & Automation, Reporting, Retirement Plans, Marketing, Office Operations | Missing whole facets |
| **Medium** | Tax/Wealth/Insurance/Benefits/CRM/Work/Doc-Mgmt (business-process + exec/user), Accounting, Microsoft 365 (admin/runbook) | Software arch exists; process/ops missing |
| **Low (finish & publish)** | Insurance & Employee Benefits (Confluence drafts), Release Management (strong Git process) | Furthest along |

## 4. Consequences if unaddressed

- **Continuity:** no DR/BCP, runbooks, or asset inventory → recovery depends on individuals'
  memory; RTO/RPO undefined.
- **Compliance:** no controls register, policies, or audit calendar → weak evidence for
  regulated flows (insurance AD-5, tax, data protection).
- **Third-party risk:** no vendor/contract register → missed renewals, unmanaged SLAs/DPAs.
- **Bus-factor & onboarding:** greenfield HR/IT/ops docs → knowledge is tribal.
- **Drift:** without DoD + sync across *all* change types, the gap reopens after every change.

## 5. Recommended attack order (feeds the roadmap)

1. **Framework + register + governance tree + DoD gate** (structural — unblocks everything).
2. **Continuity & risk floor:** Asset & Config Inventory, DR/BCP master + per-system runbooks,
   Vendor & Contract Register, Controls Register — close the critical operational gaps.
3. **Generate/surface** Data Model + Architecture for software areas (cheap).
4. **Executive Overview + Business Purpose** for all 26 (short, high navigation value).
5. **Operational content by traffic/risk:** HR & Compliance policies/RACI; Tax/Client-facing
   process SOPs; infra admin guides.
6. **Net-new architecture:** AD, Networking, AI, Reporting, Retirement Plans.
7. **Troubleshooting/FAQ/Training/Calendar/Glossary** as steady-state, demand-driven.
