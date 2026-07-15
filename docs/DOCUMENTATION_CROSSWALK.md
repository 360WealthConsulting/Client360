# Documentation Crosswalk — Client360 ↔ 360OS Operations Manual

Maps each Client360 capability to its GitHub technical source of truth and the corresponding
360OS Operations Manual (Confluence) staff-facing page. Per **DEC-001 (Use Git as Atlas
Source of Truth)** this crosswalk lives in Git; the Confluence pages are the published,
staff-facing operational rendering.

**Confluence site:** `360wealthconsulting.atlassian.net` · **Space:** 360 Wealth Consulting
Operations (`3WCO`). Proposed 360OS Ids are pending the page owner's numbering registry and
final section placement (recommended: a new **🧾 Employee Benefits Operations** capability
section, or the **⚖️ Compliance** section).

## Release 0.9.11 · Phase 5 — Employee Benefits: Compliance & Renewal Obligations

| Client360 capability | GitHub technical source | Confluence operational page | SOP / checklist | Page owner | Status | Release | Last reviewed | Next review |
|---|---|---|---|---|---|---|---|---|
| Benefit/retirement compliance & renewal **obligations** (model, statuses, recurrence, evidence, roles) | `app/services/benefits_obligations.py`; migration `u1f9c0i9h8g7`; `docs/RELEASE_0.9.11_BENEFITS_ARCHITECTURE.md` §17A | Employee Benefits — Compliance & Renewal Obligations — page `27951106` (`/wiki/x/AoCqAQ`) | Ref (proposed **EB-REF-01**) | Michael Shelton | Draft (Confluence draft, awaiting approval) | v0.9.11 (Phase 5) | 2026-07-15 | 2026-10-15 |
| Benefits **deadline monitoring, exceptions, SLA escalation, staff notifications, work queues** | `app/services/benefits_detectors.py`; `app/services/exception_sla.py`; `app/services/benefits_notifications.py`; `app/services/work_management.py` | Employee Benefits — Deadline Monitoring, Exceptions & Work Queues — page `27983873` (`/wiki/x/AQCrAQ`) | SOP (proposed **EB-SOP-01**) | Michael Shelton | Draft (Confluence draft, awaiting approval) | v0.9.11 (Phase 5) | 2026-07-15 | 2026-10-15 |
| Benefits **obligation management** — staff procedure & training | `app/services/benefits_obligations.py`; `app/services/benefits_work.py`; `app/jobs/scheduler.py` | Employee Benefits — Obligation Management Checklist — page `27918338` (`/wiki/x/AgCqAQ`) | Checklist (proposed **EB-CHK-01**) | Michael Shelton | Draft (Confluence draft, awaiting approval) | v0.9.11 (Phase 5) | 2026-07-15 | 2026-10-15 |

### Notes
- Confluence pages are **status = draft** (unpublished): they await the page owner's approval,
  final Id assignment, and section placement before going live in the manual tree.
- Technical architecture is **not** duplicated into Confluence — it remains in GitHub
  (`docs/RELEASE_0.9.11_BENEFITS_ARCHITECTURE.md` and the `app/services/*` modules); the
  Confluence pages are staff-facing operational guidance only.
- Scope guardrail honored: pages document **only** current, tested Phase 5 functionality; no
  Phase 6–8 features (staff benefits console, staff calendar UI, employer portal, employer
  notifications, live carrier/Betterment/payroll/HRIS integrations) are described as available.
