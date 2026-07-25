# Change Domain Registry (Phase D.63)

`CHANGE_DOMAIN_REGISTRY` in `app/services/change_management/registry.py` is a declarative catalog of the **15
change domains** the firm's platform actually has. Metadata only — it defines no ITSM / change-management
system. Each entry names its authoritative owner, source repository/service, **read surface** (the
authoritative read composed), **prohibited mutation surface** (the mutating entry point this layer must NEVER
call), evidence source, capabilities, runtime gate, deep links, and config status.

| # | Domain | Owner | Read surface | Prohibited mutation | Capability |
| --- | --- | --- | --- | --- | --- |
| 1 | application_code | continuous_integration | architecture_manifest + app.routes | git push / merge | observability.view |
| 2 | database_schema | observability.health | `list_runtime_snapshots` | `alembic upgrade` | observability.view |
| 3 | runtime_configuration | runtime | `adoption_stats` | `set_flag` | observability.view |
| 4 | feature_flags | runtime | `feature_enabled` | `set_flag` | observability.view |
| 5 | security_configuration | security_operations | `security_summary` | `security.manage` | security.view |
| 6 | integrations | integration.service | `overview_metrics` | `create_connector` | integration.view |
| 7 | infrastructure | observability.catalog | `list_environment_profiles` | `create_environment_profile` | observability.view |
| 8 | documentation | knowledge_management | `knowledge_summary` | `create_document` | documents.view |
| 9 | compliance_rules | compliance_rule_catalog | `list_rules` | `record_decision` | compliance.supervise |
| 10 | workflow_definitions | automation_orchestration | `automation_summary` | `create_template` | automation.view |
| 11 | automation_rules | automation_orchestration | `automation_summary` | `create_job` | automation.view |
| 12 | vendor_configuration | vendor_management | `vendor_summary` | `create_provider` | integration.view |
| 13 | client_facing_behavior | integration_hub | `integration_summary` | `run_sync` | integration.view |
| 14 | reporting_analytics | analytics.metrics | `list_metrics` | n/a | analytics.executive |
| 15 | data_governance_rules | data_governance | `governance_summary` | `create_retention_assignment` | governance.view |

All 15 domains are **configured** — each has an authoritative owner in the platform. The prohibited-mutation
column documents exactly what the composition layer must never call; `governance.py`'s `_FORBIDDEN_CALLS` scan
enforces it at runtime (the layer only composes reads).

Every domain's value is composed on **read** by its owner, which enforces its own capability + record scope AND
its own runtime gate. The change layer adds `change_management.enabled` on top and never bypasses either.

## References
- `app/services/change_management/registry.py` (`CHANGE_DOMAIN_REGISTRY`, `_cd`)
- `docs/ENTERPRISE_CHANGE_MANAGEMENT.md`, `docs/CHANGE_GOVERNANCE.md`, ADR-068
