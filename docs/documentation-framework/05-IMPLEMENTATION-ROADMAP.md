# Deliverable 5 — Implementation Roadmap (company-wide Operations Manual)

Structure and enforcement first (cheap, high-leverage), then close the operational risk floor,
then backfill by priority, then automate. Relative effort and sequence — not dates.

## Phase A — Foundation & governance (unblocks everything)
- Approve the expanded framework.
- Provision the Confluence space: nodes `00`, `01`, `10`, `20`, `30`, `40`, `80`, `90`, and an
  **Area Shell template per profile** (Software / Infrastructure / Business Operations).
- Load the **Template Library** (deliverable 2) as Confluence templates.
- Add a Git **`governance/` tree** for git-canonical operational artifacts (policies, runbooks,
  DR/BCP plans, controls register, operating calendar data).
- Promote `DOCUMENTATION_CROSSWALK.md` → the full **Publication Register** (all 26 areas × their
  profile's types; status seeded from the Capability Map).
- Add the **Definition-of-Done checklist** to the PR template and an **advisory docs gate**;
  assign **owners/reviewers** per area.
- *Exit:* every area has a page skeleton, a register row, an owner; DoD visible on PRs.

## Phase B — Close the operational risk floor (highest-risk, greenfield)
Prioritize continuity, security, and compliance before convenience:
- **Asset & Configuration Inventory** (Servers, Networking, AD, M365, endpoints, licenses).
- **DR/BCP master + per-system Runbooks** (RTO/RPO; backup/restore builds on the existing
  restore-rehearsal).
- **Vendor & Contract Register** (SLAs, renewals, DPAs, risk tiers).
- **Controls & Compliance Register** + core **Policies** (security, data-retention, HR, AUP).
- **Incident Response** playbook.
- *Exit:* the firm can recover, evidence its controls, and manage vendors from documentation.

## Phase C — Surface & generate the technical layer (mostly derivable)
- Link/refresh **Architecture** for each software area; **generate Data Model** pages from
  migrations.
- Populate **Security & Permissions** views from role seeds (link Global Security — no dup).
- Generate per-area **Release Notes / Change Records** from `CHANGELOG.md`; document the
  **Release Management** program (the release process + DoD) in `80`.
- *Exit:* all Git-canonical doc types exist and are linked for software + infra areas.

## Phase D — Author the operational/business layer (by priority)
Order by traffic/risk:
1. HR, Compliance (policies/RACI/onboarding), Tax Operations, Client360 core.
2. Insurance, Employee Benefits (finish drafts), Wealth, Work Mgmt, Document Mgmt, Accounting.
3. CRM, Administration/Office Ops, Microsoft 365 admin, Marketing, Integrations.
4. AI & Automation, Reporting, Retirement Plans (net-new architecture first).

Per area: **Executive Overview → Business Purpose → (Policy/RACI or User Guide) → SOPs/Checklists
→ Troubleshooting/FAQ/Training**, using the Insurance Commissions SOP as the exemplar.
- *Exit:* every area meets its profile's minimum-viable page set; priority areas complete.

## Phase E — Automation & self-sustaining sync
- Implement `scripts/docs_sync.py` (`verify`|`push`|`report`|`audit`) — deliverable 6.
- Flip the docs gate **advisory → blocking**; add the **release.sh docs precondition**.
- Wire the **Review Calendar** (overdue → owner tasks) and the coverage dashboard.
- *Exit:* no release/infra/process change lands without its documentation; reviews auto-chased.

## Phase F — Steady state
- Documentation is Definition of Done for software releases, infrastructure changes, and
  business-process changes alike.
- Quarterly reviews run off the Review Calendar; coverage tracked in `90`.
- New areas provisioned one-click from the profile Area Shells.

## Backfill vs new-work split
- **Surface/generate (cheap):** software Architecture, Data Model, Security, Release Notes.
- **Author (net content):** operational/business docs across all areas; policies; runbooks.
- **Create from near-zero:** AD, Networking, Vendor Mgmt, HR, Marketing, Office Ops, AI,
  Reporting, Retirement Plans.

## Cadence coupling
Phase A is a standalone docs release. Phase B (risk floor) runs as its own focused initiative
because it is greenfield and high-risk. Phases C–D proceed **alongside normal delivery** — each
development phase (from Phase 6 onward) and each infra/process change closes its own area's gaps
as part of the Definition of Done, so the backlog shrinks with ordinary work.
