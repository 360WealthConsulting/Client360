# ADR-066 — Enterprise Capacity Planning, Workforce Operations & Resource Intelligence: A Read-Only Composition, Not a Second HR / Scheduling / Workforce / PSA Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Capacity Planning / Workforce Operations / Resource Intelligence);
Operations; Reliability; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.61 audit inventoried every workforce / staffing / utilization / capacity owner:

* **Operations capacity owner** — `operations.capacity.capacity_overview` (`resource_count` /
  `over_capacity_count`) + `resource_utilization` + `list_capacity_plans`. The authoritative firm-utilization /
  capacity-plan owner (firm-level, no client scope).
* **Work Queue (D.39)** — `work_queue.summary.work_queue_summary` (`my_overdue` / `sla_breaches` /
  `unassigned_team` / `by_domain`) + `compose_queue`. **Practice Management (D.49)** — `practice_summary`
  (staffing recommendations, advisory only). **Automation Orchestration (D.51)** — `automation_summary` +
  the scheduled-job engine. **Identity** — `users` / `teams` / `team_memberships` (the employee/team owner) and
  `object_security.resolve_assignments` (record-scoped who-services-a-record).
* **Genuinely absent (not_configured):** there is **no PTO / availability owner, no time-tracking / time-entry
  owner, and no payroll / compensation owner** — confirmed absent. A full HR employee directory + contractors,
  and meeting / onboarding / planning capacity, likewise have no authoritative owner for a capacity view. There
  are **no `workforce.*` / `staffing.*` / `utilization.*` capabilities**, and `capacity` appears only as
  runtime gates + the existing `capacity.read` capability — which expresses the boundary.

There was **no capacity-planning composition layer** unifying these into named, firm-wide views of staffing,
workload, queue health, utilization, capacity forecasts, and assignment distribution. Building a second HR
platform, HCM, scheduling application, calendar system, project-management system, PSA, time-tracking platform,
payroll platform, or workforce-management system would violate the "no second system" invariant and duplicate
governed infrastructure.

## Decision
Phase D.61 adds a **governed, read-only capacity-planning composition layer**
(`app/services/capacity_planning/`) with NO new capability, NO new metric, NO persistence, and NO mutation:

1. Three declarative **registries** (`registry.py`): `WORKFORCE_REGISTRY` (8 workforce classes — advisors,
   tax / insurance professionals, operations + admin staff, contractors [not_configured], automation workers,
   shared resources), `CAPACITY_REGISTRY` (9 capacity categories — meeting / onboarding / planning capacity
   not_configured), and `UTILIZATION_REGISTRY` (5 utilization / workload / staffing / queue / assignment
   indicators), each naming owner + runtime gate + capabilities + deep links + config status. Plus
   `PANEL_REGISTRY` (23) and `RESOURCE_DASHBOARDS` (8).
2. Normalized read-models (`model.py`): `PanelResult` + `ResourceDashboard`, each explainable (a hard emit
   gate), carrying `derived` / `config_status`; **counts, status, and coverage only, never an employee detail,
   payroll, HR record, calendar content, time entry, or sensitive staffing datum**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Operations capacity owner via `capacity.read`, the Work Queue via `work.read`, Practice Management,
   Automation Orchestration via `automation.view`); fail-closed; every panel self-restricts. PTO / availability
   / contractors / meeting-onboarding-planning capacity panels are emitted `available=False` with
   `config_status='not_configured'` — honest, never a fabricated staffing / availability figure. The
   `executive_workforce_status` panel is a **DERIVED** operational summary (labeled `derived`) — never a
   certified staffing / utilization figure and never an HR record.
4. The **resource-intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `capacity_summary`, plus `client_staffing` / `household_staffing` — composed from ONLY the record-scoped
   authorization owner (`object_security.resolve_assignments`: who is assigned to service the record).
   **Employee workload, firm utilization, and unrelated staffing data are never exposed at client/household
   scope.** Dashboard-level authorization admits **operations OR an executive** (`capacity.read` /
   `analytics.executive`, via `require_any_capability`).
5. **Runtime gates** (`capacity.enabled`, `workforce.enabled`, `resource_intelligence.enabled`,
   `capacity_ai_summary.enabled`) + the runtime gate of every composed source, **policy composition**,
   **analytics reuse** (four operational counters into the ONE Analytics Registry — no second registry),
   internal **diagnostics** (`observability.audit`), and a read-only **governance** checker that forbids
   mutation, persistence, any staffing/scheduling/assignment/capacity mutation (`assign_work`, `assign_record`,
   `create_meeting`, `book_resource`, `create_capacity_plan`, …), a second metrics registry, and a fabricated
   staffing / utilization figure. AI Assist may summarize workload / utilization / staffing / capacity / queue
   health but never assigns work, approves staffing, schedules employees, modifies assignments, infers
   availability, or fabricates utilization.

No migration, no new table, no new capability, no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`.

## Alternatives considered
- **A second HR platform / HCM / scheduling / calendar / project-management / PSA / time-tracking / payroll /
  workforce-management system.** Rejected: the Operations capacity owner, the Work Queue, Practice Management,
  Automation Orchestration, and Identity are the authoritative owners; D.61 composes them. Governance forbids a
  second store and any staffing/scheduling/assignment mutation. Where no owner exists (HR directory,
  contractors, PTO / availability, time-tracking, payroll, meeting / onboarding / planning capacity), the entry
  declares `not_configured`.
- **A utilization-scoring engine that implies certified staffing.** Rejected: any figure comes from an
  authoritative source; the one derived summary is deterministic, labeled `derived`, keeps
  configured/not_configured visible, and is an operational summary — never a certified staffing / utilization
  figure and never an HR record.
- **A new `workforce.*` capability.** Rejected: the audit proved `capacity.read` / `work.read` /
  `automation.view` express the boundary.

## Reasons for the decision
Capacity planning needs one operational view; the Operations capacity owner, the Work Queue, and Practice
Management already own every signal with the correct scoping. A read-only composition gives that view with full
explainability (source + deep link) while every resource stays owned by Operations capacity, every work item by
the Work Queue, and every assignment by the authorization owner. Emitting counts / status / coverage only keeps
employee details, payroll, HR records, calendar contents, and time entries out of the layer entirely.

## Rationale for avoiding a second HR, scheduling, workforce, or PSA platform
A second HR / scheduling / workforce / PSA platform would require duplicated employees, calendars, schedules,
PTO, staffing, assignments, utilization, time entries, and payroll, plus its own scheduling + assignment model
— duplicating governed infrastructure and creating reconciliation + drift + shadow-directory risk, with no
benefit the composition does not already provide. Composing over the single Operations capacity owner + the
Work Queue keeps one source of truth for every workforce signal and zero fabricated utilization.

## Consequences

### Positive consequences
- One firm-wide workforce / capacity / utilization surface with no second HR / scheduling / workforce / PSA /
  time-tracking / payroll platform.
- Record scope + capability inherited from composed owners; a restricted panel leaks no value or count;
  client/household sections expose only record-scoped servicing-team counts, never firm utilization or
  employee workload.
- Zero schema change; Advisor Workspace Capacity & Workload panel + Client 360 / Household 360 Servicing Team
  sections + an Executive Enterprise Workforce & Capacity dashboard + AI summarize-only.
- HR directory / contractors / PTO / availability / time-tracking / payroll / meeting-onboarding-planning
  capacity reported `not_configured` — honest; operational summaries are never certified staffing figures.

### Negative consequences and tradeoffs
- Dashboards recompute per request (no persistence).
- Coverage is bounded by the owners' read surface; a genuinely new workforce signal is added to the owning
  domain first, then surfaces here.
- PTO / availability / time-tracking / payroll stay `not_configured` until an authoritative owner exists.

## Enforcement
`tests/test_capacity_planning.py` (three registries + integrity + duplicate-key prevention + configured-owner
validation + honest not_configured; explainable composition; authorization — unauthorized → None, unentitled
panel restricted; runtime + policy gates; the firm summary + record-scoped client/household staffing rollups
that hide firm data; analytics reuse; diagnostics; routes registered + capability-gated operations OR
executive; AI summarize-only; the no-fabricated-utilization/staffing invariant; and the architecture invariants
— no second HR/scheduling/workforce platform, no persistence, no mutation, no sensitive workforce data).
`app/services/capacity_planning/governance.py` enforces the invariants at runtime. Route count, section
registries, ADR count, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` +
`tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate are exposed only within dashboards whose required capability
(`capacity.read` / `analytics.executive`) the principal holds; each panel additionally self-restricts.
Client-scoped sections compose ONLY the record-scoped `object_security.resolve_assignments` read — firm
utilization and employee workload are never exposed at client/household scope.

## Revisit conditions
Revisit when an authoritative HR directory / contractor / PTO / availability / time-tracking / payroll /
meeting-onboarding-planning-capacity owner is added (compose it here, replacing the `not_configured` entries —
never a second HR/scheduling/workforce platform).

## References
- `app/services/capacity_planning/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/capacity_planning.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Capacity & Workload panel in
  `app/services/workspace/service.py`; Executive dashboard in `app/services/executive_intelligence/registry.py`;
  AI grounding in `app/services/ai_assist/context.py`; analytics counters in
  `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/operations/capacity.py`, `app/services/work_queue/*`, `app/services/practice_management/*`,
  `app/services/automation_orchestration/*`, `app/security/object_security.py`, the Runtime + Policy engines
- `docs/ENTERPRISE_CAPACITY_PLANNING.md`, `docs/WORKFORCE_REGISTRY.md`, `docs/CAPACITY_REGISTRY.md`,
  `docs/UTILIZATION_REGISTRY.md`, `docs/RESOURCE_INTELLIGENCE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_capacity_planning.py`; relates to ADR-039, ADR-049, ADR-051, ADR-054, ADR-060, ADR-065
