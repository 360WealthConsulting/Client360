# Incident Category Registry (Phase D.60)

The **incident category registry** (`INCIDENT_CATEGORY_REGISTRY` in
`app/services/operational_resilience/registry.py`) is the declarative catalog of the operational-resilience
layer's incident / alert categories and, for each, the **authoritative owner**. It is metadata only — the
layer owns no incident lifecycle and never creates, acknowledges, or closes an incident or alert.

## Incident categories

Each category declares `owner` (or `not_configured`), `runtime_gate`, `capabilities`, `deep_links`, and
`config_status`.

| Category | Authoritative owner | Config |
| --- | --- | --- |
| `reliability_incidents` | observability.incidents | configured |
| `security_incidents` | security.incidents | configured |
| `service_alerts` | observability.alerts | configured |
| `health_check_failures` | observability.health | configured |
| `integration_failures` | integration.sync | configured |
| `workflow_escalations` | automation_orchestration | configured |
| `vendor_incidents` | **not_configured** | **not_configured** |

## The not_configured category (reported honestly)

**Vendor incidents** have **no dedicated authoritative owner** — vendor / third-party risk is surfaced through
Security incidents (D.56 reuses `security.incidents`), but there is no purpose-built vendor-incident engine.
Rather than fabricate a vendor-incident feed, the category is declared `not_configured` and reported honestly
in the `incident_category_inventory` panel. When an authoritative vendor-incident owner is added, the layer
composes it — never a second incident manager.

## Ownership boundaries (never re-implemented here)

- **Reliability incidents / findings** are owned by `observability.incidents` (`metrics` →
  `reliability_incidents` / `reliability_findings`). The layer never opens or resolves an incident.
- **Security incidents** are owned by `security.incidents`. The layer never creates or acknowledges an
  incident.
- **Alerts + maintenance windows** are owned by `observability.alerts` (`metrics` → `open_alerts` /
  `active_maintenance_windows`). The layer never generates, acknowledges, or resolves an alert, and never
  schedules maintenance — no second alerting engine or scheduler.

## How the registry is used

The `incident_readiness` dashboard composes `reliability_incidents`, `security_incidents`, `open_alerts`, and
`incident_category_inventory` (DERIVED). Governance validates that every category declares its fields, that
every **configured** category names an authoritative owner, and that keys are unique.

See [ENTERPRISE_OPERATIONAL_RESILIENCE.md](ENTERPRISE_OPERATIONAL_RESILIENCE.md),
[SERVICE_DEPENDENCY_REGISTRY.md](SERVICE_DEPENDENCY_REGISTRY.md),
[BUSINESS_CONTINUITY_REGISTRY.md](BUSINESS_CONTINUITY_REGISTRY.md), and
[ADR-065](adr/ADR-065-operational-resilience.md).
