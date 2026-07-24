# ADR-060 — Enterprise Business Continuity & Operational Resilience: A Read-Only Composition, Not a Second Backup/Monitoring/DR Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Reliability / Operations / Business Continuity); Security /
Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.55 audit found the platform already owns the operational-resilience read surfaces —
infrastructure health, runtime readiness, scheduled jobs, notifications, incidents, maintenance windows — but
**no backup, restore, or disaster-recovery service exists anywhere** (zero matches for backup / restore / DR
/ RPO / RTO / replication / pg_dump in `app/services/` or `app/routes/`):

* **Observability domain** (`app/services/observability/`, D.26) — `service.overview_metrics(principal)` +
  submodule metrics: `catalog.metrics` (operational / degraded services), `health.metrics` (failed health
  checks / diagnostic failures), `incidents.metrics` (reliability incidents / findings), `alerts.metrics`
  (open alerts / active maintenance windows) + `list_maintenance_windows`, `telemetry.metrics_summary`.
* **Runtime engine** — `runtime.service.overview_metrics(principal)` (readiness `validation_ok` /
  `issue_count`), `runtime.coordination.cluster_state()` (worker health), `runtime.consumption.adoption_stats`.
* **Automation scheduler** (`automation.service.metrics`) — scheduled jobs / runs; **Communications**
  (`communications.service.metrics`) — notification / messaging activity.

There was **no business-continuity composition layer** unifying these into named, firm-wide views of backup
status, recovery readiness, restore validation, infrastructure health, runtime resilience, maintenance,
notifications, and operational readiness. Building a second backup platform, monitoring system,
disaster-recovery engine, scheduler, notification system, or incident manager would violate the "no second
system" invariant and duplicate governed, gated infrastructure.

## Decision
Phase D.55 adds a **governed, read-only business-continuity composition layer**
(`app/services/business_continuity/`) with NO new metrics, NO persistence, and NO mutation:

1. Two declarative **registries** (`registry.py`): `RESILIENCE_REGISTRY` (9 resilience domains — backup,
   restore, disaster recovery, high availability, infrastructure, monitoring, runtime, maintenance,
   notifications — each naming authoritative / health / monitoring owner + runtime gate + deep links) and
   `RECOVERY_REGISTRY` (8 recovery assets — database, file storage, documents, configuration, analytics,
   identity, communications, integrations — each naming owner + backup/restore owner + RPO + RTO), plus
   `PANEL_REGISTRY` (22 panels) and `CONTINUITY_DASHBOARDS` (8 dashboards).
2. Normalized read-models (`model.py`): `PanelResult` + `ContinuityDashboard`, each explainable (explanation
   + source + deep link, a hard emit gate) and reference-only; **counts + status only, never an
   infrastructure payload**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Observability domain, the Runtime engine, the Automation scheduler, Communications). **Backup /
   restore / DR have no authoritative owner in the platform today** — those panels report ``not_configured``
   honestly, never a fabricated backup status (the D.50/OCR precedent). Fail-closed; every panel
   self-restricts to `observability.view`.
4. The **business-continuity engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `continuity_summary`, plus `client_continuity` / `household_continuity` (the firm-level resilience posture
   surfaced in the client/household context). Every dashboard carries generated timestamp, governing
   services, source inventory, explainable panels, and deep links. Dashboard-level authorization
   (`observability.view`).
5. **Runtime gates** (`continuity.enabled` + `resilience.enabled` + `recovery.enabled`), **policy
   composition**, **analytics reuse** (four operational counters registered into the ONE Analytics Registry —
   no second registry), internal **diagnostics** (`observability.audit`), and a read-only **governance**
   checker that forbids mutation, persistence, and any backup / restore / monitoring / incident / scheduler
   execution call (`run_backup`, `restore_backup`, `run_due_scans`, `raise_alert`, `set_service_status`,
   `enqueue_run`, …). AI Assist may summarize resilience counts but never starts backups, restores data,
   acknowledges incidents, changes monitoring, alters runtime, or modifies infrastructure.

No migration, no new table, no new capability (reuses `observability.view` + `observability.audit`), no new
metric, no new outbox contract. Single Alembic head stays `n5s6u7p8v9w0`.

## Alternatives considered
- **A second backup platform / monitoring system / DR engine / scheduler / notification system / incident
  manager.** Rejected: the Observability domain, the Runtime engine, the Automation scheduler, and
  Communications are the authoritative owners; D.55 composes them. Governance forbids a second platform and
  any execution call. Where no owner exists (backup / restore / DR), the layer reports `not_configured`
  rather than fabricating one.
- **A second metrics registry.** Rejected: resilience counts come from the owners' `metrics()` reads; the
  layer registers only operational counters (about itself) into the single Analytics Registry — the house
  style.
- **Persisting composed resilience state.** Rejected: dashboards are a deterministic function of the
  authoritative data at read time; a store would be a resilience warehouse to reconcile.

## Reasons for the decision
Reliability leadership needs one operational-resilience view; the Observability + Runtime owners already own
every number with the correct scoping. A read-only composition gives that view with full explainability
(source + deep link) while every service health signal stays owned by Observability, every runtime readiness
signal by the Runtime engine, every job by the Automation scheduler, and every notification by
Communications. Deep links (never inline execution) route the operator to the authoritative surface to act.
Reporting backup/restore/DR as `not_configured` is the honest posture — the platform has no such owner, and
fabricating a green backup status would be a safety hazard.

## Rationale for avoiding a second backup/monitoring platform
A second backup / monitoring / DR platform would require duplicated backup metadata, restore records,
monitoring data, incidents, and maintenance schedules, plus its own execution + alerting — duplicating
governed infrastructure and creating reconciliation + false-confidence risk, with no benefit the composition
does not already provide. Composing over the single Observability + Runtime owners keeps one source of truth
for every health signal and zero fabricated backup status.

## Consequences

### Positive consequences
- One firm-wide operational-resilience surface with no second backup platform, monitoring system, DR engine,
  scheduler, notification system, or incident manager.
- Record scope + capability are inherited from the composed Observability/Runtime reads; a
  non-`observability.view` principal sees restricted panels, never values.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Operational Resilience panel + Client 360 / Household 360 Business Continuity sections +
  an Executive Operational Resilience dashboard (reusing existing widgets) + AI summarize-only, all from one
  layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- Backup / restore / DR panels report `not_configured` until an authoritative backup owner is added to the
  platform — honest, but not a live backup status.
- The layer's coverage is bounded by the Observability/Runtime read surface; a genuinely new resilience
  signal is added to the owning domain first, then surfaces here.

## Enforcement
`tests/test_business_continuity.py` (two registries + single ownership; explainable dashboard composition;
the honest not-configured backup panels; authorization — unauthorized → None, unentitled panel restricted
never valued; runtime + policy gates; the firm summary + client/household rollups; analytics reuse — the 4
counters in the ONE registry; diagnostics; routes registered + capability-gated; AI summarize-only; and the
architecture invariants — no second backup/monitoring/DR/scheduler/notification/incident system, no mutation,
resilience reads composed from the Observability owner, every dashboard deep-links, every panel names an
authoritative owner). `app/services/business_continuity/governance.py` enforces the invariants at runtime
(including backup/restore/monitoring execution-call tells). Route count, section registries, and migration
head are guarded by `tests/test_platform_architecture.py` + `tests/test_client360_workspace.py` +
`tests/test_household360_workspace.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate (Observability overview / metrics, Runtime readiness) are exposed only
within dashboards whose required capability (`observability.view`) the principal holds; each panel
additionally self-restricts to `observability.view`.

## Revisit conditions
Revisit when an authoritative backup / restore / DR owner is added to the platform (compose it here, replacing
the `not_configured` panels — never a second backup engine), or if a materialized resilience read-model is
ever justified (it would be a governed projection, never a second monitoring system).

## References
- `app/services/business_continuity/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/business_continuity.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Operational Resilience panel in
  `app/services/workspace/service.py`; Executive Operational Resilience dashboard in
  `app/services/executive_intelligence/registry.py`; AI grounding in `app/services/ai_assist/context.py`;
  analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/observability/*` (`service.py`, `catalog.py`, `health.py`, `incidents.py`,
  `alerts.py`), `app/services/runtime/{service,coordination,consumption}.py`,
  `app/services/automation/service.py`, `app/services/communications/service.py`
- `docs/BUSINESS_CONTINUITY.md`, `docs/RECOVERY_REGISTRY.md`, `docs/RESILIENCE_REGISTRY.md`,
  `docs/BUSINESS_CONTINUITY_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_business_continuity.py`; relates to ADR-026, ADR-028, ADR-046 through ADR-059
