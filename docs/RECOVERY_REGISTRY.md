# Recovery Registry (Phase D.55)

The **recovery registry** (`RECOVERY_REGISTRY` in `app/services/business_continuity/registry.py`) is the
declarative catalog of the firm's recovery assets and, for each, the **authoritative owner** plus its
declared recovery objectives (RPO / RTO). It is metadata only: the Business Continuity layer owns no backup,
restore, or recovery state — it references the owners and explains the result with a deep link.

## Recovery assets

Each asset declares its `owner` (the authoritative owner of the data), `backup_owner`, `restore_owner`
(both `not_configured` today — no authoritative backup/restore service exists), `rpo` (recovery point
objective), `rto` (recovery time objective), and `runtime_gate`.

| Asset | Owner | Backup owner | RPO | RTO |
| --- | --- | --- | --- | --- |
| `database` | postgres | not_configured | 1 hour | 4 hours |
| `file_storage` | document_platform | not_configured | 24 hours | 8 hours |
| `documents` | document_platform | not_configured | 24 hours | 8 hours |
| `configuration` | configuration | not_configured | 24 hours | 2 hours |
| `analytics` | analytics | not_configured | 24 hours | 8 hours |
| `identity` | identity | not_configured | 1 hour | 2 hours |
| `communications` | communications | not_configured | 24 hours | 8 hours |
| `integrations` | integration | not_configured | 24 hours | 8 hours |

## RPO / RTO are declarative targets

The RPO / RTO values are **declared recovery objectives** for each asset — the targets a future backup owner
would be measured against. They are metadata, not live measurements. The `backup_coverage` panel reports how
many recovery assets currently have a declared backup owner (zero today — honestly reported), and the
`rpo_targets` / `rto_targets` panels surface the declared objectives per asset. When an authoritative backup
owner is added to the platform, the layer composes its real backup/restore status against these objectives —
never a second backup engine.

## Ownership boundaries (never re-implemented here)

- **The data of record** for each asset is owned by its owner service (postgres, document_platform,
  configuration, analytics, identity, communications, integration). The registry names the owner; the layer
  never touches the data.
- **Backup / restore execution** has no authoritative owner today; the registry declares the assets and their
  objectives, and the layer reports `not_configured` — it never runs a backup or restore (governance forbids
  `run_backup` / `restore_backup`).

## How the registry is used

The recovery-readiness + backup-status dashboards compose `recovery_assets`, `rpo_targets`, `rto_targets`,
and `backup_coverage` (this registry) plus the honest not-configured backup panels. Governance validates that
every asset declares all six fields (owner, backup owner, restore owner, RPO, RTO, runtime gate), and that
keys are unique.

See [RESILIENCE_REGISTRY.md](RESILIENCE_REGISTRY.md), [BUSINESS_CONTINUITY.md](BUSINESS_CONTINUITY.md), and
[ADR-060](adr/ADR-060-business-continuity.md).
