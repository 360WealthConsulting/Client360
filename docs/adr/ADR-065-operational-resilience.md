# ADR-065 — Enterprise Operational Resilience, Incident Management & Service Continuity Intelligence: A Read-Only Composition, Not a Second Incident / Monitoring / DR Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Operational Resilience / Incident Management / Service Continuity);
Reliability / Operations; Security; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.60 audit inventoried every operational-resilience owner:

* **Observability domain (D.26)** — the service inventory of record. `observability.catalog.metrics`
  (`operational_services` / `total_services` / `degraded_services`) + `list_dependencies`;
  `observability.health.metrics` (`failed_health_checks` / `diagnostic_failures`);
  `observability.incidents.metrics` (`reliability_incidents` / `reliability_findings`);
  `observability.alerts.metrics` (`open_alerts` / **`active_maintenance_windows`**) + `list_maintenance_windows`.
* **Security incidents (D.25)** — `security.incidents.metrics` (`open_incidents` / `open_findings` /
  `pending_exceptions`). **Integration Platform (D.53)** — `integration.service.overview_metrics` /
  `integration.sync.metrics`. **Vendor Management (D.56)** — `vendor_summary`. **Automation Orchestration
  (D.51)** — `automation_summary` (workflow escalations / failed runs). **Business Continuity (D.55)** —
  `continuity_summary` (resilience posture, infrastructure availability, backup coverage, RPO/RTO).
* **Genuinely absent (not_configured):** there is **no backup / restore / disaster-recovery owner, no
  DR-runbook or recovery-testing or failover owner, no outage-history / uptime / availability owner, and no
  dedicated vendor-incident owner** (D.55 already declares backup/restore/DR not_configured). There are **no
  `resilience.*` / `incident.*` / `operational.*` capabilities** — `observability.view` (the D.55 precedent)
  expresses the boundary.

**Maintenance windows and alerting ARE owned** (both by `observability.alerts`) — they are CONFIGURED, not
absent. There was **no operational-resilience composition layer** unifying these into named, firm-wide views of
service health, incident inventory, alerts, maintenance, continuity coverage, recovery readiness, dependency
health, and vendor operational status. Building a second incident-management platform, ticketing system,
monitoring platform, help desk, DR platform, change-management platform, CMDB, scheduler, or alerting engine
would violate the "no second system" invariant and duplicate governed infrastructure.

## Decision
Phase D.60 adds a **governed, read-only operational-resilience composition layer**
(`app/services/operational_resilience/`) with NO new capability, NO new metric, NO persistence, and NO
mutation:

1. Five declarative **registries** (`registry.py`): `OPERATIONAL_SERVICE_REGISTRY` (6),
   `INCIDENT_CATEGORY_REGISTRY` (7 — vendor incidents not_configured), `CONTINUITY_CAPABILITY_REGISTRY` (7 —
   backup/restore/DR not_configured), `RECOVERY_OBJECTIVE_REGISTRY` (5 — recovery testing/failover
   not_configured), `OPERATIONAL_DEPENDENCY_REGISTRY` (4), each naming key + owner + runtime gate +
   capabilities + deep links + config status. Plus `PANEL_REGISTRY` (24) and `RESILIENCE_DASHBOARDS` (8).
2. Normalized read-models (`model.py`): `PanelResult` + `ResilienceDashboard`, each explainable (a hard emit
   gate), carrying `derived` / `config_status`; **counts, status, and coverage only, never a sensitive
   operational payload**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner;
   fail-closed; every panel self-restricts to its source capability. Backup / restore / DR / recovery-testing /
   failover / vendor-incident panels are emitted `available=False` with `config_status='not_configured'` —
   honest, never a fabricated status. The `executive_operational_status` panel is a **DERIVED** operational
   posture (labeled `derived`) that describes operational posture, **never a certification that production is
   healthy or continuity assured**, and never interprets an absent incident as health.
4. The **resilience engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `resilience_summary`, plus `client_operational_impact` / `household_operational_impact` — composed from
   ONLY the genuinely record-scoped owner (the Integration Hub per-entity read); **firm-wide operational
   information is never exposed at client/household scope** (per-entity incident impact has no authoritative
   owner → not_configured). Dashboard-level authorization admits **operations OR an executive**
   (`observability.view` / `analytics.executive`, via `require_any_capability`).
5. **Runtime gates** (`operational_resilience.enabled`, `incident_intelligence.enabled`,
   `continuity_intelligence.enabled`, `resilience_ai_summary.enabled`) + the runtime gate of every composed
   source, **policy composition**, **analytics reuse** (four operational counters into the ONE Analytics
   Registry — no second registry), internal **diagnostics** (`observability.audit`), and a read-only
   **governance** checker that forbids mutation, persistence, any incident/alert/maintenance/monitoring
   mutation (`open_incident`, `raise_alert`, `create_maintenance_window`, …), a second metrics registry, and a
   fabricated operational status. AI Assist may summarize operational health but never declares production
   healthy, certifies continuity, infers recovery success, fabricates incidents, or generates alerts.

No migration, no new table, no new capability, no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`.

## Alternatives considered
- **A second incident-management / ticketing / monitoring / help-desk / DR / change-management / CMDB /
  scheduler / alerting platform.** Rejected: the Observability domain, Security incidents, the Integration
  Platform, Vendor Management, Automation Orchestration, and Business Continuity are the authoritative owners;
  D.60 composes them. Governance forbids a second store and any incident/alert/maintenance mutation. Where no
  owner exists (backup, restore, DR, recovery testing, failover, outage history, vendor incidents), the entry
  declares `not_configured`.
- **A resilience-scoring engine that implies production health.** Rejected: any status comes from an
  authoritative source; the one derived summary is deterministic, labeled `derived`, keeps
  configured/not_configured visible, describes operational posture (not certification), and never interprets an
  absent incident as health.
- **A new `resilience.*` capability.** Rejected: the audit proved `observability.view` (the D.55 precedent for
  the whole operational-resilience surface) expresses the boundary.

## Reasons for the decision
Operational resilience needs one operational view; the Observability domain and the D.51–D.56 layers already
own every signal with the correct scoping. A read-only composition gives that view with full explainability
(source + deep link) while every incident stays owned by Observability / Security, every alert + maintenance
window by Observability alerts, and every continuity signal by Business Continuity. Emitting counts / status /
coverage only keeps sensitive operational payloads out of the layer entirely.

## Rationale for avoiding a second incident or monitoring platform
A second incident / monitoring platform would require duplicated incidents, alerts, maintenance records,
monitoring data, and dependency inventories, plus its own alerting + scheduling model — duplicating governed
infrastructure and creating reconciliation + drift + shadow-monitoring risk, and (worst of all) risking a
fabricated operational status or a generated alert. Composing over the single Observability domain keeps one
source of truth for every operational signal and zero fabricated status.

## Consequences

### Positive consequences
- One firm-wide operational-resilience surface with no second incident / monitoring / DR / scheduler / CMDB /
  alerting platform.
- Record scope + capability inherited from composed owners; a restricted panel leaks no value or count;
  client/household sections expose only record-scoped dependency impact, never firm-wide operational data.
- Zero schema change; Advisor Workspace Operational Status panel + Client 360 / Household 360 Operational
  Impact sections + an Executive Enterprise Operational Resilience dashboard + AI summarize-only.
- Backup / restore / DR / recovery-testing / failover / outage-history / vendor-incidents reported
  `not_configured` — honest; operational posture is never a certification that production is healthy.

### Negative consequences and tradeoffs
- Dashboards recompute per request (no persistence).
- Coverage is bounded by the owners' read surface; a genuinely new operational signal is added to the owning
  domain first, then surfaces here.
- Recovery testing / failover / outage history stay `not_configured` until an authoritative owner exists.

## Enforcement
`tests/test_operational_resilience.py` (five registries + integrity + duplicate-key prevention +
configured-owner validation + honest not_configured; explainable composition; authorization — unauthorized →
None, unentitled panel restricted; runtime + policy gates; the firm summary + record-scoped client/household
rollups that hide firm-wide data; analytics reuse; diagnostics; routes registered + capability-gated operations
OR executive; AI summarize-only; the no-fabricated-operational-status invariant; and the architecture
invariants — no second monitoring/incident platform, no persistence, no mutation, no sensitive operational
data). `app/services/operational_resilience/governance.py` enforces the invariants at runtime. Route count,
section registries, ADR count, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` +
`tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate are exposed only within dashboards whose required capability
(`observability.view` / `analytics.executive`) the principal holds; each panel additionally self-restricts.
Client-scoped sections compose ONLY the Integration Hub per-entity read — firm-wide operational information is
never exposed at client/household scope.

## Revisit conditions
Revisit when an authoritative backup / restore / DR / recovery-testing / failover / outage-history / uptime /
vendor-incident owner is added (compose it here, replacing the `not_configured` entries — never a second
incident/monitoring platform).

## References
- `app/services/operational_resilience/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/operational_resilience.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Operational Status panel in
  `app/services/workspace/service.py`; Executive dashboard in `app/services/executive_intelligence/registry.py`;
  AI grounding in `app/services/ai_assist/context.py`; analytics counters in
  `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/observability/{catalog,health,incidents,alerts}.py`, `app/services/security/incidents.py`,
  `app/services/integration/{service,sync}.py`, `app/services/vendor_management/*`,
  `app/services/automation_orchestration/*`, `app/services/business_continuity/*`, `app/services/integration_hub/*`,
  the Runtime + Policy engines
- `docs/ENTERPRISE_OPERATIONAL_RESILIENCE.md`, `docs/INCIDENT_REGISTRY.md`, `docs/SERVICE_DEPENDENCY_REGISTRY.md`,
  `docs/BUSINESS_CONTINUITY_REGISTRY.md`, `docs/OPERATIONAL_RESILIENCE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_operational_resilience.py`; relates to ADR-025, ADR-026, ADR-027, ADR-053, ADR-055, ADR-056,
  ADR-060 through ADR-064
