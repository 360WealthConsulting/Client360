# Environment Registry (Phase D.64)

`ENVIRONMENT_REGISTRY` and `PLATFORM_REGISTRY` in `app/services/environment_management/registry.py` are
declarative catalogs of the **8 environment domains** and **9 platform domains** the firm's platform actually
has. Metadata only — they define no CMDB, asset inventory, or environment manager. Each entry names its
authoritative owner, read surface, **prohibited mutation surface** (the mutating entry point this layer must
NEVER call), evidence source, capabilities, runtime gate, environment scope, deep links, and config status.

## Environment domains (8)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| environment_inventory | observability.catalog | `list_environment_profiles` | `create_environment_profile` | configured |
| environment_types | observability.catalog | `ENVIRONMENTS` (production/staging/development/test) | `create_environment_profile` | configured |
| environment_regions | observability.catalog | `list_environment_profiles` (region) | `create_environment_profile` | configured |
| environment_activation | observability.catalog | `list_environment_profiles` (active) | `create_environment_profile` | configured |
| environment_runtime_readiness | observability.health | `list_runtime_snapshots` | `capture_runtime_snapshot` | configured |
| environment_health | observability.service | `overview_metrics` | `set_service_status` | configured |
| environment_configuration_scope | runtime | `adoption_stats` | `set_flag` | configured |
| cloud_environment_provisioning | not_configured | n/a | n/a | **not_configured** |

## Platform domains (9)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| platform_inventory | observability.catalog | `list_services` | `create_service` | configured |
| platform_types | observability.catalog | `SERVICE_TYPES` | `create_service` | configured |
| platform_criticality | observability.catalog | `list_services` (criticality) | `create_service` | configured |
| platform_status | observability.catalog | `list_services` (status) | `set_service_status` | configured |
| platform_ownership | observability.catalog | `list_services` (owner_user_id) | `create_service` | configured |
| platform_references | observability.catalog | `list_services` (reference_type) | `create_service` | configured |
| cloud_resources | not_configured | n/a | n/a | **not_configured** |
| servers_hosts | not_configured | n/a | n/a | **not_configured** |
| containers_vms | not_configured | n/a | n/a | **not_configured** |

## The honest gaps

Cloud provisioning, cloud resources, servers / hosts, and containers / VMs have **no authoritative owner** in
the platform — services are logical services, not infrastructure hosts, and there is no cloud-resource
inventory. These are declared `not_configured` and reported honestly, never a fabricated environment or
infrastructure. **Environment metadata (a profile's code / region / active flag) is not live infrastructure.**

## References
- `app/services/environment_management/registry.py` (`ENVIRONMENT_REGISTRY`, `PLATFORM_REGISTRY`, `_e`)
- `docs/ENTERPRISE_ENVIRONMENT_MANAGEMENT.md`, `docs/ENVIRONMENT_GOVERNANCE.md`, ADR-069
