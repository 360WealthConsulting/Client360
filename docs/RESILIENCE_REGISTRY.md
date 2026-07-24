# Resilience Registry (Phase D.55)

The **resilience registry** (`RESILIENCE_REGISTRY` in `app/services/business_continuity/registry.py`) is the
declarative catalog of the firm's resilience domains and, for each, the **authoritative owners** whose live
health is composed. It is metadata only: the Business Continuity layer owns no monitoring, incident, or
maintenance state — it references the owners and explains the result with a deep link.

## Resilience domains

Each domain declares its `authoritative_owner` (or `not_configured` for declarative-only domains),
`health_owner` (the live-health proxy owner), `monitoring_owner`, `runtime_gate`, and `deep_links`.

| Domain | Authoritative owner | Health owner | Monitoring owner |
| --- | --- | --- | --- |
| `backup` | not_configured | observability | observability |
| `restore` | not_configured | observability | observability |
| `disaster_recovery` | not_configured | observability | observability |
| `high_availability` | runtime.coordination | runtime.coordination | observability |
| `infrastructure` | observability.catalog | observability.catalog | observability |
| `monitoring` | observability | observability | observability |
| `runtime` | runtime.service | runtime.service | observability |
| `maintenance` | observability.alerts | observability.alerts | observability |
| `notifications` | communications | communications | observability |

## The backup / restore / DR gap (reported honestly)

`backup`, `restore`, and `disaster_recovery` have **no authoritative owner in the platform today** (the D.55
audit confirmed zero backup / restore / DR / RPO / RTO / replication services). Rather than fabricate a
green backup status — a safety hazard — those domains are declared `authoritative_owner = not_configured`,
and their panels (`last_successful_backup`, `failed_backups`, `restore_test_status`) report a
`{"status": "not_configured"}` value with a deep link to the continuity surface. This mirrors the D.50/OCR
precedent: report the owner's real state, never invent one. When an authoritative backup owner is added to
the platform, those panels compose it (replacing `not_configured`) — never a second backup engine.

## Ownership boundaries (never re-implemented here)

- **Infrastructure / monitoring / incidents / alerts / maintenance** are owned by the Observability domain
  (`app/services/observability/`). The registry names the health/monitoring owner; the layer **never calls**
  `set_service_status` / `raise_alert` / `set_incident_status` / `set_maintenance_status` — governance forbids
  it.
- **High availability / runtime** are owned by the Runtime engine (`runtime.service` readiness,
  `runtime.coordination` cluster). The layer never converges a worker or heartbeats.
- **Notifications** are owned by Communications. The layer composes its read-only metrics.

## How the registry is used

The restore-validation + operational-readiness dashboards compose `resilience_domains` (this registry) plus
the Observability/Runtime health panels. Governance validates that every domain declares all fields (owner,
health owner, monitoring owner, runtime gate, deep links), that keys are unique, and that the layer contains
no backup/monitoring/incident **execution** call.

See [RECOVERY_REGISTRY.md](RECOVERY_REGISTRY.md), [BUSINESS_CONTINUITY.md](BUSINESS_CONTINUITY.md), and
[ADR-060](adr/ADR-060-business-continuity.md).
