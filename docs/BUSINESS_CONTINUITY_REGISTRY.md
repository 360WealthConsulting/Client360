# Continuity & Recovery Registry (Phase D.60)

Two declarative catalogs in `app/services/operational_resilience/registry.py` describe the continuity
capabilities and recovery objectives the layer composes from the D.55 Business Continuity layer + Observability.
Both are metadata only — the layer owns no continuity documentation, no recovery plans, and no backup/restore
state.

> This registry is the D.60 *operational-resilience* view of continuity; the authoritative continuity owner is
> the D.55 Business Continuity layer. Backup / restore / disaster recovery have no authoritative owner in the
> platform (the D.55 precedent) and are reported `not_configured`, never fabricated.

## Continuity capabilities (`CONTINUITY_CAPABILITY_REGISTRY`)

Each capability declares `owner` (or `not_configured`), `runtime_gate`, `capabilities`, `deep_links`, and
`config_status`.

| Capability | Authoritative owner | Config |
| --- | --- | --- |
| `resilience_posture` | business_continuity | configured |
| `infrastructure_availability` | business_continuity | configured |
| `monitoring` | observability | configured |
| `maintenance` | observability.alerts | configured |
| `backup` | **not_configured** | **not_configured** |
| `restore` | **not_configured** | **not_configured** |
| `disaster_recovery` | **not_configured** | **not_configured** |

## Recovery objectives (`RECOVERY_OBJECTIVE_REGISTRY`)

| Objective | Authoritative owner | Config |
| --- | --- | --- |
| `recovery_assets` | business_continuity | configured |
| `rpo_targets` | business_continuity | configured |
| `rto_targets` | business_continuity | configured |
| `recovery_testing` | **not_configured** | **not_configured** |
| `failover` | **not_configured** | **not_configured** |

## The not_configured continuity / recovery domains (reported honestly)

**Backup, restore, disaster recovery, recovery testing, and failover** have **no authoritative owner in the
platform today** (the D.55 audit confirmed backup/restore/DR are not_configured; RPO/RTO exist only as
declarative targets). Rather than fabricate a backup / restore / DR / recovery-test / failover status, those
capabilities are declared `not_configured` and reported honestly in the `continuity_coverage`,
`recovery_readiness`, and `recovery_test_coverage` panels. **Maintenance windows ARE owned** by
`observability.alerts` (`active_maintenance_windows`) — configured, not absent.

## How the registry is used

The `continuity_coverage` + `recovery_readiness` dashboards compose `continuity_coverage`,
`infrastructure_availability`, `active_maintenance_windows`, `recovery_readiness` (DERIVED), `rpo_targets`
(DERIVED), `rto_targets` (DERIVED), and `recovery_test_coverage` (not_configured). Governance validates
completeness + single ownership + honest not_configured.

See [ENTERPRISE_OPERATIONAL_RESILIENCE.md](ENTERPRISE_OPERATIONAL_RESILIENCE.md),
[INCIDENT_REGISTRY.md](INCIDENT_REGISTRY.md), [SERVICE_DEPENDENCY_REGISTRY.md](SERVICE_DEPENDENCY_REGISTRY.md),
the D.55 [BUSINESS_CONTINUITY.md](BUSINESS_CONTINUITY.md), and
[ADR-065](adr/ADR-065-operational-resilience.md).
