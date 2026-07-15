# Release 0.9.11 — Employer Operations & Employee Benefits

**Status:** release candidate — validated by [RC14](RC14_VALIDATION.md) (**SAFE TO MERGE**,
0 defects); **PR #22 draft, not merged; not tagged**. **Alembic head:** `u1f9c0i9h8g7`
(baseline v0.9.10 `q7b58f6c5d4e`). **Design:**
[Architecture](RELEASE_0.9.11_BENEFITS_ARCHITECTURE.md) (ADR-18).

Release 0.9.11 delivers a usable **Employer Operations** product built on shared Client360
concepts — Organizations, permanent relationship roles, service lines, a universal Engagement
model, and Employee **Benefits + Retirement** (first-class, Betterment-ready). Everything
reuses the existing platform (Person/Household, Documents + Document Intelligence, Work
Management, Timeline, Audit, the Exception Engine, the Portal, and the scheduler) — **no
second engine, scheduler, portal, workflow, reporting framework, or data model.** Tax is
untouched.

## What's new (by phase)

- **Organization foundation (P1–2).** Organization = the existing `relationship_entities` +
  a 1:1 `organization_profiles` (EIN encrypted at rest); permanent relationship roles reuse
  `relationship_types`; ownership is a typed 1:1 detail on the existing `relationships` edge
  (percentages, voting, direct/indirect, unknown %); service lines; a universal `engagements`
  model (tax converges later, documented). Canonical services with Organization-anchored
  record scope and audit on every mutation; **disabled** carrier / recordkeeper (Betterment) /
  payroll / HRIS provider ports.
- **Benefits & retirement (P1–2).** 17 plan types (health + retirement), plans/plan-years,
  employments (reusing `people`), enrollments, retirement deferral elections; Betterment
  seeded as the first recordkeeper (no integration).
- **Detectors (P3).** 18 health + retirement detectors translate stored data into
  `domain='benefits'` exceptions (idempotent, auto-resolve, reopen); documented inert gaps
  (contribution-deposit lateness, etc.) — **never inferred**.
- **Work Management (P4).** Benefits exceptions project through the canonical `work_items()`
  into seven benefits queues; assignment reuses `assignment_rules`; a scheduled detector scan
  on the existing scheduler (overlap-prevented, idempotent, per-org failure isolation, honest
  metrics).
- **Compliance & renewal obligations + SLA + notifications (P5).** A minimal obligation model
  (templates + instantiated obligations with **verified** dates); date-driven detector; the
  **shared** SLA sweep extended to benefits (internal-only escalation/notifications, honest
  outcomes) — no benefits-specific SLA engine.
- **Staff API & consoles (P6).** Thin `/api/v1/organizations` + `/api/v1/benefits` API and
  `/organizations` + `/benefits` consoles on the modern shell (**names, not IDs**; EIN gated
  by `benefits.sensitive.read`).
- **Employer portal (P7).** Organization-scoped employer "Action Needed" (strict PII-free
  allowlist), census upload (reuses documents; clears the census exception), secure messages,
  and auditable employer notifications — reusing the existing portal stack.
- **Dashboards & reporting (P8).** Proportional, decision-oriented benefits dashboard (book,
  participation, compliance/renewal calendar, exception metrics) — authorization-filtered,
  stored-data-only, reusing `exception_reporting`. No decorative panels.

## Capabilities & roles

New least-privilege families: `organization.*` and `benefits.*` (`read`/`write`/`enroll`/
`compliance`/`sensitive.read`); benefits exceptions reuse `exception.*`. New roles
`benefits_advisor` / `benefits_operations` / `benefits_compliance`. **No role widened; no new
`record.read_all` grant.**

## Migrations (additive, reversible; single head `u1f9c0i9h8g7`)

`r8c69f7e6d5c` (foundation schema) → `s9d7a8g7f6e5` (EIN widened for encryption) →
`t0e8b9h8g7f6` (benefits work queues, data-only) → `u1f9c0i9h8g7` (obligations). Sentinel
preservation verified across a v0.9.10 down/up cycle ([RC14 §2](RC14_VALIDATION.md)).

## Validation

Full suite **520 passed / 5 skipped**; migration lifecycle, cross-org isolation, staff
capability, employer-portal privacy, scheduler/overlap, and reporting authorization all
verified. See [RC14](RC14_VALIDATION.md) — **SAFE TO MERGE**.

## Known limitations

- Tax convergence onto the shared `engagements` model is documented but **deferred** (its own
  sprint/RC).
- Carrier / Betterment / payroll / HRIS integrations are **disabled** ports (no live data).
- Contribution-deposit lateness, full ACA measurement, and cross-service opportunity detection
  are **out of scope** (documented data gaps; nothing inferred).
- Revenue capture has schema (`service_revenue`) but no entry/reporting path yet (deferred to
  avoid a decorative dashboard).
- Employer-facing notifications are enabled but not yet auto-triggered on a schedule
  (`notify_employer` is reused where staff/actions dispatch it).
