# Documentation Crosswalk — Client360 ↔ 360OS Operations Manual (company-wide)

Maps each Client360 capability to its GitHub technical source of truth and the corresponding
360OS Operations Manual (Confluence) staff-facing page. Per **DEC-001 (Use Git as Atlas
Source of Truth)** this crosswalk lives in Git; the Confluence pages are the published,
staff-facing operational rendering.

This is the **company-wide** crosswalk for the whole 360OS Operations Manual — not a
per-domain or Insurance-only index. Every operating area of the firm has a home in the
section map below; software-backed areas link to their Client360 source, and areas without
software yet are still listed as manual sections so the manual tree is complete. Individual
capability→page rows are added per section as pages are drafted.

> **This crosswalk is the framework's Publication Register.** Under the approved
> [360 Wealth Consulting Operations Manual documentation framework](documentation-framework/README.md)
> this file is the register of record — the single index of each page's canonical source, owner,
> status, and review dates. The framework defines the *structure* (information architecture,
> templates, area profiles); this register tracks *state*. They link; neither duplicates the
> other. Roadmap Phase A promotes it to a machine-readable form (`docs/registers/pages.yml`).

**Confluence site:** `360wealthconsulting.atlassian.net` · **Space:** 360 Wealth Consulting
Operations (`3WCO`). Proposed 360OS Ids and final section placement are pending the page
owner's numbering registry.

**Guardrails (apply to every row):**
- **Technical architecture is not duplicated into Confluence** — it stays in GitHub
  (`docs/*_ARCHITECTURE.md` and the `app/**` modules). Confluence holds staff-facing
  operational guidance only.
- **Only current, tested functionality is documented** as available. Planned/unbuilt phases
  are never described as live.
- **Regulated content is gated by AD-5** (see the Insurance section): no suitability,
  replacement/1035, licensing/CE *validation*, or other compliance-determination content is
  published until a qualified, named compliance reviewer approves it.

---

## 1. Company-wide manual section map

The full 360OS Operations Manual tree. **Crosswalk status** describes whether capability→page
rows have been drafted in this document — *not* whether Confluence pages are published (they
are all still draft; see each section).

| # | Manual section | Client360 source (if any) | Crosswalk status | Section owner |
|---|---|---|---|---|
| A | **Executive Management** | — (no software surface yet) | Manual-only section; no rows yet | Michael Shelton |
| B | **Sales and Marketing** | — (relationship/CRM data partial) | Not yet drafted | Michael Shelton |
| C | **Client Experience** | Client portal & secure collaboration (`docs/CLIENT_PORTAL.md`); unified workspace/UI (`docs/UI_DESIGN_SYSTEM.md`) | Candidate — rows not yet drafted | Michael Shelton |
| D | **Tax Operations** | Epic 5 tax platform (`docs/EPIC_5_TAX_PRACTICE_PLATFORM.md`, `docs/TAX_*`) | Candidate — rows not yet drafted | Michael Shelton |
| E | **Wealth Management** | Schwab portfolio intelligence (`docs/SCHWAB_PORTFOLIO_ENGINE.md`); relationship engine | Candidate — rows not yet drafted | Michael Shelton |
| F | **Employee Benefits** | `docs/RELEASE_0.9.11_BENEFITS_ARCHITECTURE.md`; `app/services/benefits_*` | **Drafted** (§2) — 3 pages, draft | Michael Shelton |
| G | **Retirement Plans** | Benefit/retirement compliance & renewal obligations (shared with Benefits) | Partial (folded into §2 obligations) | Michael Shelton |
| H | **Insurance Operations** | `docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md`; `app/services/insurance*` | **Drafted** (§3) — 11 pages, draft | Michael Shelton |
| I | **Finance and Accounting** | Revenue categories; insurance commissions (Phase 5, planned) | Not yet drafted | Michael Shelton |
| J | **HR and People Operations** | — (no software surface yet) | Manual-only section; no rows yet | Michael Shelton |
| K | **Compliance** | Exception engine; audit/identity; **AD-5 gate**; producer licensing/CE records | Cross-cutting — rows appear under source sections (e.g. §3) | Michael Shelton (business); **compliance reviewer UNFILLED (AD-5)** |
| L | **Technology and Cybersecurity** | Security hardening (`docs/SECURITY_HARDENING_0.9.7.md`); identity/RBAC/audit (`docs/IDENTITY_AUTHORIZATION_AUDIT.md`); production architecture | Candidate — rows not yet drafted | Michael Shelton |
| M | **Administration** | — (no software surface yet) | Manual-only section; no rows yet | Michael Shelton |
| N | **Training** | — (per-capability training pages accompany each SOP) | Manual-only section; no rows yet | Michael Shelton |

**Document-type taxonomy (cross-cutting — every section uses these):**

| Type | Meaning | Convention |
|---|---|---|
| **Policies** | What the firm requires / why | `<AREA>-POL-nn` |
| **SOPs** | Step-by-step how-to for a task | `<AREA>-SOP-nn` |
| **Checklists** | Point-of-use verification lists | `<AREA>-CHK-nn` |
| **Ref** | Reference / background material | `<AREA>-REF-nn` |

---

## 2. Employee Benefits — Release 0.9.11 · Phase 5 (Compliance & Renewal Obligations)

Manual section **F (Employee Benefits)**; retirement obligations also serve section **G**.

| Client360 capability | GitHub technical source | Confluence operational page | SOP / checklist | Page owner | Status | Release | Last reviewed | Next review |
|---|---|---|---|---|---|---|---|---|
| Benefit/retirement compliance & renewal **obligations** (model, statuses, recurrence, evidence, roles) | `app/services/benefits_obligations.py`; migration `u1f9c0i9h8g7`; `docs/RELEASE_0.9.11_BENEFITS_ARCHITECTURE.md` §17A | Employee Benefits — Compliance & Renewal Obligations — page `27951106` (`/wiki/x/AoCqAQ`) | Ref (proposed **EB-REF-01**) | Michael Shelton | Draft (Confluence draft, awaiting approval) | v0.9.11 (Phase 5) | 2026-07-15 | 2026-10-15 |
| Benefits **deadline monitoring, exceptions, SLA escalation, staff notifications, work queues** | `app/services/benefits_detectors.py`; `app/services/exception_sla.py`; `app/services/benefits_notifications.py`; `app/services/work_management.py` | Employee Benefits — Deadline Monitoring, Exceptions & Work Queues — page `27983873` (`/wiki/x/AQCrAQ`) | SOP (proposed **EB-SOP-01**) | Michael Shelton | Draft (Confluence draft, awaiting approval) | v0.9.11 (Phase 5) | 2026-07-15 | 2026-10-15 |
| Benefits **obligation management** — staff procedure & training | `app/services/benefits_obligations.py`; `app/services/benefits_work.py`; `app/jobs/scheduler.py` | Employee Benefits — Obligation Management Checklist — page `27918338` (`/wiki/x/AgCqAQ`) | Checklist (proposed **EB-CHK-01**) | Michael Shelton | Draft (Confluence draft, awaiting approval) | v0.9.11 (Phase 5) | 2026-07-15 | 2026-10-15 |

### Notes
- Confluence pages are **status = draft** (unpublished): they await the page owner's approval,
  final Id assignment, and section placement before going live.
- Scope guardrail honored: pages document **only** current, tested Phase 5 functionality; no
  Phase 6–8 features are described as available.

---

## 3. Insurance Operations — Release 0.10.0 (draft pages)

Manual section **H (Insurance Operations)**; licensing/exceptions/roles rows also serve
section **K (Compliance)**; commissions also serves **I (Finance and Accounting)**; the
policyholder portal also serves **C (Client Experience)**.

> **All pages below are DRAFT and unpublished.** Per the guardrails, no regulated content is
> published, and pages for unbuilt phases (Commissions, Policyholder Portal) are placeholders
> that must not be published until the phase is implemented and RC-validated. **AD-5:** any
> page touching suitability, replacement/1035, or licensing/CE *validation* stays blocked
> until a qualified, named compliance reviewer approves it — business (operational) sign-off
> is not regulatory certification. **Review cycle for all rows: Quarterly (next: 2026-10-16).**

| Proposed page title | Manual section | Owner | Status | Source GitHub document / feature | Release phase | Publication gate |
|---|---|---|---|---|---|---|
| **Insurance Operations Overview** | H · Insurance Operations | Michael Shelton | Draft | `docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md` | Phase 0–1 | Publish once Phases 0–4 skeletons are RC-validated; describe operational scope only, exclude all regulated features. |
| **Insurance Policy Management** | H · Insurance Operations | Michael Shelton | Draft | `app/services/insurance.py`; `app/services/insurance_catalog.py`; `app/routes/insurance.py`; migrations `v2b3d4f5a6c7`,`w3c4e5g6b7d8`,`x4d5f6h7c8e9` | Phase 1 | Publish when Phase 1 is RC-validated. Operational CRUD/lifecycle only. |
| **New Business Case Management** | H · Insurance Operations | Michael Shelton | Draft | `app/services/insurance.py` (case/pipeline/requirements); `app/services/insurance_reporting.py`; migration `y5e6g7i8d9f0` | Phase 2 (non-regulated skeleton) | Operational pipeline/requirements only. **AD-5-BLOCKED** for suitability determination — excluded until reviewer named. |
| **In-Force Policy Servicing** | H · Insurance Operations | Michael Shelton | Draft | `app/services/insurance.py` (servicing); migration `z6f7h8j9e0g1` | Phase 3 (non-regulated skeleton) | Operational servicing only. Replacement/1035 **recommendation** excluded (AD-5). |
| **Insurance Reviews and Obligations** | H · Insurance Operations | Michael Shelton | Draft | `app/services/insurance.py` (reviews state machine); `app/services/insurance_detectors.py` | Phase 3 | Publish when Phase 3 is RC-validated. Review lifecycle/obligation calendar only; **no suitability determination** content (AD-5). |
| **Producer Licensing and Continuing Education** | H · Insurance Operations / K · Compliance | Michael Shelton | Draft | `app/services/insurance_licensing.py`; migration `a7g8i9k0f1h2` | Phase 4 (non-regulated skeleton) | Records + expiry reminders only. Licensing/CE **validation** and sale/issue blocking excluded (AD-5). |
| **Insurance Commissions** | H · Insurance Operations / I · Finance and Accounting | Michael Shelton | Draft | `app/services/insurance_commissions.py`; `app/services/insurance_detectors.py` (variance/outstanding); `app/services/insurance_reporting.py` (`commission_report`); migration `b8i9k1l2g3j4`; SOP draft `docs/confluence/INSURANCE_COMMISSIONS_SOP_DRAFT.md` | Phase 5 (non-regulated, built) | Operational ledger/reconciliation/revenue only. Publish when Phase 5 is RC-validated; excludes any regulated determination (AD-5). Live scan cron is Phase 6. |
| **Insurance Exceptions and Work Queues** | H · Insurance Operations / K · Compliance | Michael Shelton | Draft | `app/services/insurance_detectors.py` (`run_insurance_scan`); `app/services/insurance_work.py`; shared exception engine (`app/services/exception_*`) + Work Management (`app/services/work_management.py`); scheduler `app/jobs/scheduler.py`; migration `c9k0m1n2h3j4`; SOP draft `docs/confluence/INSURANCE_EXCEPTIONS_WORK_QUEUES_SOP_DRAFT.md` | Phase 6 (non-regulated, built — scheduled scan live) | Operational exceptions/queues/scheduling only; firm-internal, organization-scoped, **not client-facing**. Publish when Phase 6 is RC-validated; excludes any regulated determination (AD-5). Reuses shared subsystems — document the shared engine/queue/scheduler by link, not duplication. |
| **Policyholder Portal Operations** | H · Insurance Operations / C · Client Experience | Michael Shelton | Draft | `app/services/insurance_portal.py`; portal routes (`app/routes/portal.py`); reuses the portal framework (`app/portal/service.py`, `docs/CLIENT_PORTAL.md`); SOP draft `docs/confluence/INSURANCE_POLICYHOLDER_PORTAL_SOP_DRAFT.md` | Phase 7 (non-regulated, built) | Read-only, opt-in (`insurance` grant permission), person/household/org-scoped; out-of-scope 404. **No producers/commissions/licensing/exceptions exposed** — client-facing exception visibility out of scope. Publish when Phase 7 is RC-validated; excludes any regulated determination (AD-5). |
| **Insurance Reporting** | H · Insurance Operations | Michael Shelton | Draft | `app/services/insurance_reporting.py` | Phase 2 (counts-only) | Operational counts only; **no compliance metrics**. Publish when Phase 2 is RC-validated. |
| **Insurance Roles and Responsibilities** | H · Insurance Operations / K · Compliance | Michael Shelton | Draft | `insurance.*` capabilities/roles (migration `v2b3d4f5a6c7`); `docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md` §AD-5 | Phase 0 | Publish role/capability map. **Flag prominently that the accountable compliance-reviewer role is UNFILLED (AD-5)** and that regulated duties are blocked until filled. |

### Notes
- Every Insurance page is **draft/unpublished**; none may go live until its phase is
  RC-validated and, for regulated content, AD-5-cleared.
- The **Insurance Commissions** (Phase 5), **Exceptions & Work Queues** (Phase 6), and
  **Policyholder Portal** (Phase 7) pages are now backed by **built** functionality (draft SOPs
  exist) but stay draft/unpublished until their phase is RC-validated. All remaining Insurance
  pages track unbuilt phases and must not describe functionality as available.
- The **compliance reviewer role remains unfilled (AD-5)**; this is an open, non-code blocker
  and is surfaced on the Roles & Responsibilities page rather than hidden.
