# Deployment Topology Registry (Phase D.64)

`DEPLOYMENT_TOPOLOGY_REGISTRY` and `INFRASTRUCTURE_DEPENDENCY_REGISTRY` in
`app/services/environment_management/registry.py` are declarative catalogs of the **7 deployment-topology
domains** and **7 infrastructure-dependency domains**. Metadata only — they define no deployment orchestrator,
CMDB, or monitoring platform, and never execute a deployment or provision infrastructure.

## Deployment topology domains (7)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| deployment_references | observability.catalog | `list_deployment_references` | `create_deployment_reference` | configured |
| deployment_versions | observability.catalog | `list_deployment_references` (version) | `create_deployment_reference` | configured |
| deployment_migration_head | observability.catalog | `list_deployment_references` (migration_head) | `create_deployment_reference` | configured |
| deployment_environment_mapping | observability.catalog | `list_deployment_references` (environment_profile_id) | `create_deployment_reference` | configured |
| deployment_release_timeline | observability.catalog | `list_deployment_references` (released_at) | `create_deployment_reference` | configured |
| deployment_execution_status | not_configured | n/a | n/a | **not_configured** |
| deployment_rollout_status | not_configured | n/a | n/a | **not_configured** |

Deployment references are **declared deployment metadata** — a deployment reference is **not a deployment**.
Deployment **execution / rollout status** has no authoritative owner (`not_configured`). The
`deployment_migration_alignment` panel checks whether a deployment reference's recorded migration head matches
the **live** Alembic head — a matching head does not prove the deployment ran.

## Infrastructure dependency domains (7)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| service_dependency_graph | observability.catalog | `list_dependencies` | `add_dependency` | configured |
| dependency_types | observability.catalog | `DEPENDENCY_TYPES` (hard/soft/runtime) | `add_dependency` | configured |
| integration_dependencies | integration.service | `overview_metrics` | `create_connector` | configured |
| runtime_configuration_dependencies | runtime | `adoption_stats` | `set_flag` | configured |
| infrastructure_host_metadata | not_configured | n/a | n/a | **not_configured** |
| network_topology | not_configured | n/a | n/a | **not_configured** |
| cloud_resource_dependencies | not_configured | n/a | n/a | **not_configured** |

The service dependency graph is a **logical** service-to-service graph — **not a network topology**.
Infrastructure host metadata, network topology, and cloud-resource dependencies have no authoritative owner
(`not_configured`); **private topology is never exposed.**

## References
- `app/services/environment_management/registry.py` (`DEPLOYMENT_TOPOLOGY_REGISTRY`,
  `INFRASTRUCTURE_DEPENDENCY_REGISTRY`, `_e`)
- `docs/ENTERPRISE_ENVIRONMENT_MANAGEMENT.md`, `docs/ENVIRONMENT_GOVERNANCE.md`, ADR-069
