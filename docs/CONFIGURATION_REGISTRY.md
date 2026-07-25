# Configuration Registry (Phase D.63)

`CONFIGURATION_REGISTRY` in `app/services/change_management/registry.py` is a declarative catalog of the **13
configuration domains**, each with a **sensitivity classification** and a verification owner. Metadata only —
it exposes counts / status / drift / verification metadata, **never a sensitive configuration value, credential,
token, connection string, or environment variable**. All 13 are configured (each has an authoritative owner).

| # | Entry | Owner | Scope | Sensitivity | Capability |
| --- | --- | --- | --- | --- | --- |
| 1 | runtime_flags | runtime | firm | operational | observability.view |
| 2 | policy_settings | policy | firm | operational | observability.view |
| 3 | route_registration | architecture_manifest | firm | operational | observability.view |
| 4 | capability_inventory | identity | firm | **sensitive** | observability.view |
| 5 | integration_configuration | integration.service | firm | **sensitive** | integration.view |
| 6 | observability_configuration | observability.catalog | firm | operational | observability.view |
| 7 | security_configuration | security_operations | firm | **sensitive** | security.view |
| 8 | automation_configuration | automation_orchestration | firm | operational | automation.view |
| 9 | document_classifications | document_platform | firm | operational | documents.view |
| 10 | analytics_registration | analytics.metrics | firm | operational | analytics.executive |
| 11 | migration_state | observability.health | firm | operational | observability.view |
| 12 | environment_metadata | observability.catalog | **environment** | **sensitive** | observability.view |
| 13 | maintenance_configuration | observability.alerts | firm | operational | observability.view |

## Sensitivity + drift

Entries classified **sensitive** (capability_inventory, integration_configuration, security_configuration,
environment_metadata) are surfaced as **counts / status only** — the layer never emits their values. The
`configuration_drift_availability` panel reports which configuration domains have a **live** verification owner
(route_registration via `len(app.routes)`, migration_state via `_expected_head()`) versus reference-only; drift
is always **declared-vs-live**, never a fabricated configuration state.

## References
- `app/services/change_management/registry.py` (`CONFIGURATION_REGISTRY`, `_cfg`)
- `docs/ENTERPRISE_CHANGE_MANAGEMENT.md`, `docs/CHANGE_GOVERNANCE.md`, ADR-068
