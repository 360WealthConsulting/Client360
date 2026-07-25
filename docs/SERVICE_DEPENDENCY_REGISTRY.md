# Service & Dependency Registry (Phase D.60)

Two declarative catalogs in `app/services/operational_resilience/registry.py` describe the operational
services whose health the layer composes and the operational dependencies between them. Both are metadata only
— the layer owns no service inventory (the Observability service catalog is the CMDB of record) and no
dependency graph.

## Operational services (`OPERATIONAL_SERVICE_REGISTRY`)

Each service class declares `owner`, `runtime_gate`, `capabilities`, `deep_links`, and `config_status`.

| Service class | Authoritative owner |
| --- | --- |
| `core_services` | observability.catalog |
| `infrastructure_services` | observability.catalog |
| `integration_services` | integration.service |
| `vendor_services` | vendor_management |
| `automation_services` | automation_orchestration |
| `security_services` | security.incidents |

Service health is composed from `observability.catalog.metrics` (operational / total / degraded services) —
**the Observability service catalog is the service inventory of record (the CMDB); the layer never creates a
service or a second CMDB.**

## Operational dependencies (`OPERATIONAL_DEPENDENCY_REGISTRY`)

Each dependency class declares `owner`, `runtime_gate`, `capabilities`, `deep_links`, and `config_status`.

| Dependency class | Authoritative owner |
| --- | --- |
| `service_dependencies` | observability.catalog |
| `integration_dependencies` | integration.service |
| `vendor_dependencies` | vendor_management |
| `external_dependencies` | integration_hub |

Dependency health is composed from `observability.catalog.list_dependencies` (the declared service-dependency
graph) + the Integration Platform + Vendor Management. The layer never adds or mutates a dependency.

## Record-scoped client / household impact

The Client 360 / Household 360 **Operational Impact** section composes ONLY the genuinely record-scoped
Integration Hub per-entity read (`client_integrations` / `household_integrations` → `source_systems`): the
external services / vendors the client / household depends on. **Firm-wide operational information (incidents,
alerts, service health) is never exposed at client/household scope**; per-entity incident impact has no
authoritative owner and is reported `not_configured`.

## How the registry is used

The `service_health` + `dependency_health` dashboards compose `service_health`, `degraded_services`,
`failed_health_checks`, `dependency_health`, `service_dependencies` (DERIVED), and `operational_service_inventory`
(DERIVED). Governance validates completeness + single ownership (unique keys).

See [ENTERPRISE_OPERATIONAL_RESILIENCE.md](ENTERPRISE_OPERATIONAL_RESILIENCE.md),
[INCIDENT_REGISTRY.md](INCIDENT_REGISTRY.md), [BUSINESS_CONTINUITY_REGISTRY.md](BUSINESS_CONTINUITY_REGISTRY.md),
and [ADR-065](adr/ADR-065-operational-resilience.md).
