# Business Continuity (Phase D.55)

The **Business Continuity** layer (`app/services/business_continuity/`) is a governed, **read-only
composition** that gives reliability leadership a unified operational view of platform resilience — backup
status, recovery readiness, restore validation, infrastructure health, runtime resilience, maintenance,
notifications, and operational readiness — **without** building a second backup platform, monitoring system,
disaster-recovery engine, scheduler, notification system, or incident manager. Every number is composed on
read from an **authoritative owner**; the layer owns no persistence and never starts a backup, restores data,
acknowledges an incident, changes monitoring, alters runtime, or modifies infrastructure. **Panels carry
counts + status only — never an infrastructure payload.**

## What it composes (and never duplicates)

| Concern | Authoritative owner (composed) |
| --- | --- |
| Infrastructure availability | `app/services/observability/catalog.py` — `metrics` |
| Health checks / diagnostics | `app/services/observability/health.py` — `metrics` |
| Reliability incidents / findings | `app/services/observability/incidents.py` — `metrics` |
| Alerts / maintenance windows | `app/services/observability/alerts.py` — `metrics`, `list_maintenance_windows` |
| Operational overview | `app/services/observability/service.py` — `overview_metrics` |
| Runtime readiness / cluster / adoption | `app/services/runtime/{service,coordination,consumption}.py` |
| Scheduled jobs | `app/services/automation/service.py` — `metrics` |
| Notifications | `app/services/communications/service.py` — `metrics` |
| Backup / Restore / DR | **NONE EXISTS** — reported `not_configured` honestly (never fabricated) |

See [RESILIENCE_REGISTRY.md](RESILIENCE_REGISTRY.md) for the resilience domains,
[RECOVERY_REGISTRY.md](RECOVERY_REGISTRY.md) for the recovery assets, and
[BUSINESS_CONTINUITY_GOVERNANCE.md](BUSINESS_CONTINUITY_GOVERNANCE.md) for the enforced invariants.

## Modules

- `registry.py` — the declarative catalogs: `RESILIENCE_REGISTRY` (9 resilience domains),
  `RECOVERY_REGISTRY` (8 recovery assets), `PANEL_REGISTRY` (22 panels), `CONTINUITY_DASHBOARDS` (8
  dashboards).
- `model.py` — `PanelResult` + `ContinuityDashboard`. A panel is emitted only if `is_explainable`
  (explanation + source + deep link).
- `panels.py` — the per-panel compute functions. Read-only, fail-closed, **self-restricting** (a principal
  lacking `observability.view` gets a `restricted` panel, never its value). Backup / restore panels report
  `not_configured`. Counts + status only.
- `service.py` — the engine: `compose_dashboard`, `list_dashboards`, `get_panel`, `continuity_summary`,
  `client_continuity`, `household_continuity`.
- `gate.py` — runtime gates (`continuity.enabled`, `resilience.enabled`, `recovery.enabled`) + policy
  composition. No raw environment gating.
- `stats.py` / `metrics.py` — low-cardinality in-process counters, registered into the **single** Analytics
  Registry (`analytics.metrics`). No second metrics registry; never infrastructure payloads.
- `diagnostics.py` — internal-only observability (`observability.audit`).
- `governance.py` — read-only invariant checker (never raises), including backup/monitoring execution-call
  tells.

## Dashboards

`backup_status`, `recovery_readiness`, `restore_validation`, `infrastructure_health`, `runtime_resilience`,
`maintenance`, `notifications`, `operational_readiness`. Each carries a generated timestamp, governing
services, source inventory, explainable panels, and deep links to the authoritative resilience-owner surface.
Dashboards are gated by `observability.view`; each panel additionally self-restricts to `observability.view`.

## Surfaces

- **HTTP** (`app/routes/business_continuity.py`, gated by `observability.view`; diagnostics by
  `observability.audit`): `/business-continuity` (HTML), `/api/v1/business-continuity/dashboards`,
  `/dashboard/{key}`, `/summary`, `/registry`, `/panel/{key}`, `/metrics`, `/business-continuity/diagnostics`.
- **Advisor Workspace** — the Operational Resilience panel (`continuity_summary`).
- **Client 360 / Household 360** — the `business_continuity` section (`client_continuity` /
  `household_continuity`, the firm-level resilience posture surfaced in context).
- **Executive Dashboard** — an `operational_resilience` dashboard (composed from existing D.48 widgets; no
  new widget), navigation deep-linking to `/business-continuity`.
- **AI Assist** — summarizes resilience readiness only; it never starts backups, restores data, acknowledges
  incidents, changes monitoring, alters runtime, or modifies infrastructure.

## Invariants

No new persistence, no new metric, no new capability, no migration (single Alembic head unchanged). No
mutation, no backup/restore execution, no monitoring/incident/scheduler change, no outbox publication, no
audit write, no second store. Every resilience count comes from an authoritative owner; every dashboard panel
is explainable and deep-links to its authoritative surface. Enforced by
`app/services/business_continuity/governance.py` and `tests/test_business_continuity.py`. See
[ADR-060](adr/ADR-060-business-continuity.md).

**Related (D.56):** the **Vendor Management** layer (`/vendor-management`) composes the technology-lifecycle /
vendor / third-party-risk view over the same Observability + Runtime owners plus the Integration Platform
provider registry, Security certificate & secret store, and Insurance licensing. Continuity governs
*infrastructure availability + recovery readiness*; Vendor Management governs *who supplies the technology and
whether it is current / licensed / renewed* — two read-only composition views over overlapping authoritative
owners, neither a second store. See [VENDOR_MANAGEMENT.md](VENDOR_MANAGEMENT.md) and
[ADR-061](adr/ADR-061-vendor-management.md).

**Related (D.58):** the **Enterprise Risk Management** layer (`/enterprise-risk`) composes this layer's
`continuity_summary` for its resilience-risk panels (continuity gaps + backup/recovery configuration) —
read-only, `observability.view`. It never backs up, restores, or alters infrastructure; Business Continuity
remains the authoritative owner (backup / restore / DR themselves stay `not_configured`). See
[ENTERPRISE_RISK_MANAGEMENT.md](ENTERPRISE_RISK_MANAGEMENT.md) and
[ADR-063](adr/ADR-063-enterprise-risk-management.md).
