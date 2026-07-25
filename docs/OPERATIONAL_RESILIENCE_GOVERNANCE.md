# Operational Resilience Governance (Phase D.60)

`app/services/operational_resilience/governance.py` is a read-only checker that verifies the resilience layer
stays a **composition** over the authoritative operational-resilience owners and never becomes a second
incident-management platform, ticketing system, monitoring platform, help desk, disaster-recovery platform,
change-management platform, CMDB, scheduler, or alerting engine. It returns `{ok, issue_count, findings}` and
**never raises** into normal use. `validate_operational_resilience()` is surfaced through the internal
diagnostics endpoint (`/operational-resilience/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe`), or writes
   audit events (`write_audit(`). No `rm_*` projection table is read directly.
2. **No second incident / monitoring / DR / scheduler / alerting engine — no mutation.** No module calls an
   incident / alert / maintenance / service / monitoring **mutation** — `open_incident(`,
   `set_incident_status(`, `create_finding(`, `raise_alert(`, `acknowledge_alert(`, `resolve_alert(`,
   `create_rule(`, `create_suppression(`, `create_maintenance_window(`, `set_maintenance_status(`,
   `create_service(`, `set_service_status(`, `add_dependency(`, `capture_runtime_snapshot(`. The layer composes
   **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every operational-service / incident-category /
   continuity-capability / recovery-objective / operational-dependency / panel / dashboard key is unique; every
   **configured** entry names an authoritative owner.
5. **No fabricated operational status.** Any status / posture / readiness panel **derived from the layer's own
   registries/compose** must be labeled `derived` (`unlabeled_derived_status` otherwise). The
   `executive_operational_status` panel is a DERIVED operational posture and never a certification that
   production is healthy or continuity assured.
6. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in both
   `model.py` and `panels.py`.
7. **No raw environment gating.** Gates flow through the Runtime + Policy engines — never `os.getenv` /
   `os.environ`.

## No sensitive operational payloads + honest not_configured

Panels and summaries carry **counts, status, and coverage only** — never sensitive operational payloads. The
composed owners already strip payloads; the resilience layer surfaces only aggregates. Backup, restore,
disaster recovery, recovery testing, failover, outage-history, and vendor incidents have **no authoritative
owner today** and are declared `not_configured` — reported honestly, never fabricated. **Operational posture is
never a certification** that production is healthy or continuity assured, and an absent incident is never
interpreted as health.

## Authorization & least privilege

- Resilience routes admit **operations OR an executive** (`observability.view` / `analytics.executive`, via
  `require_any_capability`); diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities`; otherwise
  `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to its authoritative-source capability. A principal lacking the panel capability
  receives a `restricted` panel with `value = None`, no hidden count, and no leaking metadata.
- Client-scoped sections compose ONLY the record-scoped Integration Hub per-entity read — firm-wide operational
  information is never exposed at client/household scope.

## AI Assist boundary

AI Assist may **summarize** operational health, resilience coverage, continuity gaps, and recovery readiness
(fact class `DERIVED`, counts only, deep links only). It **never** declares production healthy, certifies
continuity, infers recovery success, fabricates incidents, or generates alerts.

## Enforcement

`tests/test_operational_resilience.py` exercises the five registries, integrity + duplicate-key prevention +
configured-owner validation + honest not_configured, explainable composition, authorization (`None` +
restricted), gate/policy behavior, the record-scoped client/household rollups that hide firm-wide data, the
analytics-counter reuse, diagnostics, the routes (registered + capability-gated operations OR executive), AI
summarize-only, the no-fabricated-operational-status invariant, and the architecture invariants (no second
monitoring/incident platform, no persistence, no mutation, no sensitive operational data). Route count, section
registries, ADR count, and the single migration head are guarded by `tests/test_platform_architecture.py`,
`tests/test_client360_workspace.py`, `tests/test_household360_workspace.py`,
`tests/test_architecture_decision_records.py`, and the manifest.

See [ENTERPRISE_OPERATIONAL_RESILIENCE.md](ENTERPRISE_OPERATIONAL_RESILIENCE.md),
[INCIDENT_REGISTRY.md](INCIDENT_REGISTRY.md), [SERVICE_DEPENDENCY_REGISTRY.md](SERVICE_DEPENDENCY_REGISTRY.md),
[BUSINESS_CONTINUITY_REGISTRY.md](BUSINESS_CONTINUITY_REGISTRY.md), and
[ADR-065](adr/ADR-065-operational-resilience.md).

**Composed by D.63:** the **Change Management** layer (`/change-management`) composes this layer's governance
checker into its derived `governance_status` panel (a count of clean vs failing governance checkers across the
read-only layers) — read-only, `observability.view`. It never acknowledges an incident, schedules maintenance,
or alters resilience state; Operational Resilience remains the authoritative owner. Change management is an
operational-readiness view, **not** production certification. See
[ENTERPRISE_CHANGE_MANAGEMENT.md](ENTERPRISE_CHANGE_MANAGEMENT.md) and
[ADR-068](adr/ADR-068-change-management.md).
