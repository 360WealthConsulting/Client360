# Enterprise Operational Resilience, Incident Management & Service Continuity Intelligence (Phase D.60)

`app/services/operational_resilience/` is a governed, **read-only composition** that provides a unified,
governed view of firm operational resilience — operational service health, incident inventory, alerts,
maintenance windows, continuity coverage, recovery readiness, dependency health, and vendor operational status.
It is **not** a second incident-management platform, ticketing system, monitoring platform, help desk,
disaster-recovery platform, change-management platform, CMDB, scheduler, or alerting engine: **no new
capability, no new metric, no persistence, no mutation, no duplicated operational data, no migration** (single
Alembic head `n5s6u7p8v9w0`).

> **Operational posture is NOT a certification** that production is healthy or continuity assured, and an
> absent incident is never interpreted as health.

## What it composes (existing owners only)

| Signal | Authoritative owner | Composed read | Capability |
| --- | --- | --- | --- |
| Service health / degraded services | Observability service catalog (D.26) | `catalog.metrics` | `observability.view` |
| Health checks / diagnostics | Observability health | `health.metrics` | `observability.view` |
| Reliability incidents | Observability incidents | `incidents.metrics` | `observability.view` |
| Alerts / maintenance windows | Observability alerts | `alerts.metrics` | `observability.view` |
| Security incidents | Security incidents (D.25) | `security.incidents.metrics` | `security.view` |
| Integration / sync failures | Integration Platform (D.53) | `service.overview_metrics` / `sync.metrics` | `integration.view` |
| Vendor operational status | Vendor Management (D.56) | `vendor_summary` | `integration.view` |
| Workflow escalations | Automation Orchestration (D.51) | `automation_summary` | `automation.view` |
| Continuity posture / coverage / RPO-RTO | Business Continuity (D.55) | `continuity_summary` | `observability.view` |
| Service dependencies | Observability catalog | `catalog.list_dependencies` | `observability.view` |

## The not_configured domains (reported honestly)

The D.60 audit confirmed several domains have **no authoritative owner** and are declared `not_configured`
(the D.55 precedent), never fabricated: **backup, restore, disaster recovery, recovery testing, failover,
outage-history / uptime / availability reporting, and vendor incidents** (which reuse Security incidents; no
dedicated vendor-incident owner). **Maintenance windows and alerting ARE owned** (both by
`observability.alerts`) — they are configured, not absent.

## Registries, panels, dashboards

Five declarative registries — Operational Services (6) + Incident Categories (7) + Continuity Capabilities (7)
+ Recovery Objectives (5) + Operational Dependencies (4) — plus 24 panels and 8 dashboards (operational_resilience,
incident_readiness, service_health, continuity_coverage, recovery_readiness, dependency_health,
vendor_operational_health, executive_operational_status). See [INCIDENT_REGISTRY.md](INCIDENT_REGISTRY.md),
[SERVICE_DEPENDENCY_REGISTRY.md](SERVICE_DEPENDENCY_REGISTRY.md), and
[BUSINESS_CONTINUITY_REGISTRY.md](BUSINESS_CONTINUITY_REGISTRY.md). Every dashboard carries a generated
timestamp, governing services, source inventory, explainable panels, deep links, and its configured /
not_configured domain lists.

## Panels — counts, status, coverage only

Panels carry counts, status, and coverage only. They **never** return sensitive operational payloads. The
`executive_operational_status` panel is a DERIVED operational posture (labeled `derived`) — never a certified
score, never a claim that production is healthy or continuity assured.

## Authorization

- Routes + dashboards admit **operations OR an executive** (`observability.view` / `analytics.executive`, via
  `require_any_capability`); diagnostics by `observability.audit`.
- Each **panel self-restricts** to its authoritative-source capability (security panels `security.view`,
  integration/vendor panels `integration.view`, workflow panels `automation.view`, executive panel
  `analytics.executive`). A principal lacking the panel capability receives a `restricted` panel with `value =
  None`, no hidden count, and no leaking metadata.
- Client-scoped sections compose ONLY the genuinely record-scoped Integration Hub per-entity read — firm-wide
  operational information is never exposed at client/household scope.

## Runtime, governance, analytics, observability

Every surface is gated through the Runtime Engine (`operational_resilience.enabled`,
`incident_intelligence.enabled`, `continuity_intelligence.enabled`, `resilience_ai_summary.enabled`) **and**
the runtime gate of every composed source, plus the Policy Engine — **no environment bypass**. Governance
(`validate_operational_resilience()`) returns `{ok, issue_count, findings}` and forbids persistence, mutation,
any incident/alert/maintenance/monitoring mutation, a second metrics registry, and a fabricated operational
status — see [OPERATIONAL_RESILIENCE_GOVERNANCE.md](OPERATIONAL_RESILIENCE_GOVERNANCE.md). Four low-cardinality
counters register into the **single** Analytics Registry. Internal diagnostics
(`/operational-resilience/diagnostics`, `observability.audit`) report registry coverage, configured vs
not_configured counts, dependency coverage, panel availability, and the governance summary.

## Surfaces

- **Advisor Workspace** — an **Operational Status** panel (`ws["operational_status"]`) showing active
  operational issues, planned maintenance, degraded services, and continuity advisories.
- **Client 360 / Household 360** — an **Operational Impact** section (`observability.view`): the external
  services / vendors the client / household depends on (record-scoped Integration Hub read only); firm-wide
  operational information is never exposed at client/household scope.
- **Executive Dashboard** — an **Enterprise Operational Resilience** dashboard reusing existing widgets
  (`operational_health`, `runtime_health` — no new widget).
- **AI Assist** — summarizes operational health / resilience coverage / continuity gaps / recovery readiness.
  It **never** declares production healthy, certifies continuity, infers recovery success, fabricates
  incidents, or generates alerts.

## Routes

`/operational-resilience` (HTML) + `/api/v1/operational-resilience/{dashboards, dashboard/{key}, summary,
registry, panel/{key}, metrics}` + `/operational-resilience/diagnostics`.

See [INCIDENT_REGISTRY.md](INCIDENT_REGISTRY.md), [SERVICE_DEPENDENCY_REGISTRY.md](SERVICE_DEPENDENCY_REGISTRY.md),
[BUSINESS_CONTINUITY_REGISTRY.md](BUSINESS_CONTINUITY_REGISTRY.md),
[OPERATIONAL_RESILIENCE_GOVERNANCE.md](OPERATIONAL_RESILIENCE_GOVERNANCE.md), and
[ADR-065](adr/ADR-065-operational-resilience.md).
