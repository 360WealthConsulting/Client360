# Platform Lifecycle Registry (Phase D.64)

`LIFECYCLE_REGISTRY` in `app/services/environment_management/registry.py` is a declarative catalog of the **8
platform-lifecycle domains**. Metadata only — it defines no lifecycle-management system and never mutates
lifecycle state, deprecates, retires, or decommissions anything. Four domains are **configured** (composed as a
lifecycle proxy); four are genuinely absent (`not_configured`).

## Configured (lifecycle proxy)

| Domain | Owner | Read surface | Prohibited mutation |
| --- | --- | --- | --- |
| runtime_readiness_state | observability.health | `list_runtime_snapshots` (summary: ready / not_ready) | `capture_runtime_snapshot` |
| environment_activation_lifecycle | observability.catalog | `list_environment_profiles` (active) | `create_environment_profile` |
| service_operational_lifecycle | observability.catalog | `list_services` (status) | `set_service_status` |
| migration_lifecycle_state | observability.health | `_expected_head` + snapshots | `capture_runtime_snapshot` |

These are a **proxy** for lifecycle: a service's operational `status` (operational / degraded / down /
maintenance / unknown), an environment's `active` flag, and a runtime snapshot's `ready / not_ready` summary.
**A status is not a formal lifecycle state, and an active flag is not a lifecycle guarantee.**

## Not configured (no authoritative owner — honest)

| Domain | Why not_configured |
| --- | --- |
| formal_lifecycle_state | no planned → active → deprecated → retired record owner |
| deprecation_records | no deprecation owner |
| retirement_records | no platform-retirement owner |
| decommission_schedule | no environment-decommission-schedule owner |

The `lifecycle_readiness` panel is a **DERIVED** operational-readiness summary (runtime readiness + migration
alignment + active environments − not_configured / not_ready areas) — **operational readiness only, never a
certified lifecycle or retirement decision**. The `operational_lifecycle_state` panel presents the status +
activation proxy while explicitly declaring formal lifecycle state `not_configured`. The `retirement_readiness`
panel is emitted `available=False` — **no retirement status is ever fabricated.**

## References
- `app/services/environment_management/registry.py` (`LIFECYCLE_REGISTRY`, `_e`)
- `docs/ENTERPRISE_ENVIRONMENT_MANAGEMENT.md`, `docs/ENVIRONMENT_GOVERNANCE.md`, ADR-069
