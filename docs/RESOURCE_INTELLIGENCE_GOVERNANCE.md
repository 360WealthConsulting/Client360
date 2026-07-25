# Resource Intelligence Governance (Phase D.61)

`app/services/capacity_planning/governance.py` is a read-only checker that verifies the capacity layer stays a
**composition** over the authoritative workforce / capacity / utilization owners and never becomes a second HR
platform, HCM, scheduling application, calendar system, project-management system, PSA, time-tracking platform,
payroll platform, or workforce-management system. It returns `{ok, issue_count, findings}` and **never raises**
into normal use. `validate_capacity_planning()` is surfaced through the internal diagnostics endpoint
(`/capacity-planning/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe`), or writes
   audit events (`write_audit(`). No `rm_*` projection table is read directly.
2. **No second HR / scheduling / workforce / PSA engine — no mutation.** No module calls a staffing /
   scheduling / assignment / capacity **mutation** — `assign_work(`, `assign_record(`, `assign_reviewer(`,
   `invite_user(`, `set_user_status(`, `assign_role(`, `add_team_membership(`, `create_meeting(`,
   `update_meeting(`, `reschedule(`, `book_resource(`, `add_attendee(`, `create_capacity_plan(`, `claim(`,
   `complete(`. The layer composes **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every workforce / capacity / utilization / panel / dashboard
   key is unique; every **configured** entry names an authoritative owner.
5. **No fabricated utilization / staffing.** Any status / readiness / forecast / gaps panel **derived from the
   layer's own registries/compose** must be labeled `derived` (`unlabeled_derived_summary` otherwise). The
   `executive_workforce_status` panel is a DERIVED operational summary and never a certified staffing /
   utilization figure or an HR record.
6. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in both
   `model.py` and `panels.py`.
7. **No raw environment gating.** Gates flow through the Runtime + Policy engines — never `os.getenv` /
   `os.environ`.

## No sensitive workforce data + honest not_configured

Panels and summaries carry **counts, status, and coverage only** — never employee details, payroll, HR
records, calendar contents, time entries, or sensitive staffing data. The composed owners already strip
payloads; the capacity layer surfaces only aggregates (resource counts, not the Identity directory's PII rows).
A full HR employee directory + contractors, PTO / availability, time-tracking, payroll, and meeting / onboarding
/ planning capacity have **no authoritative owner today** and are declared `not_configured` — reported
honestly, never fabricated.

## Authorization & least privilege

- Capacity routes admit **operations OR an executive** (`capacity.read` / `analytics.executive`, via
  `require_any_capability`); diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities`; otherwise
  `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to its authoritative-source capability (workload / queue panels `work.read`,
  automation panels `automation.view`, executive panel `analytics.executive`). A principal lacking the panel
  capability receives a `restricted` panel with `value = None`, no hidden count, and no leaking metadata.
- Client-scoped sections compose ONLY the record-scoped `object_security.resolve_assignments` read — employee
  workload and firm utilization are never exposed at client/household scope.

## AI Assist boundary

AI Assist may **summarize** workload, utilization, staffing, capacity, and queue health (fact class `DERIVED`,
counts only, deep links only). It **never** assigns work, approves staffing, schedules employees, modifies
assignments, infers availability, or fabricates utilization.

## Enforcement

`tests/test_capacity_planning.py` exercises the three registries, integrity + duplicate-key prevention +
configured-owner validation + honest not_configured, explainable composition, authorization (`None` +
restricted), gate/policy behavior, the record-scoped client/household staffing rollups that hide firm data, the
analytics-counter reuse, diagnostics, the routes (registered + capability-gated operations OR executive), AI
summarize-only, the no-fabricated-utilization/staffing invariant, and the architecture invariants (no second
HR/scheduling/workforce platform, no persistence, no mutation, no sensitive workforce data). Route count,
section registries, ADR count, and the single migration head are guarded by
`tests/test_platform_architecture.py`, `tests/test_client360_workspace.py`,
`tests/test_household360_workspace.py`, `tests/test_architecture_decision_records.py`, and the manifest.

See [ENTERPRISE_CAPACITY_PLANNING.md](ENTERPRISE_CAPACITY_PLANNING.md),
[WORKFORCE_REGISTRY.md](WORKFORCE_REGISTRY.md), [CAPACITY_REGISTRY.md](CAPACITY_REGISTRY.md),
[UTILIZATION_REGISTRY.md](UTILIZATION_REGISTRY.md), and [ADR-066](adr/ADR-066-capacity-planning.md).
