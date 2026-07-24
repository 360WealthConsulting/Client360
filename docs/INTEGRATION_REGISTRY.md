# Integration Registry (Phase D.53)

The **integration registry** (`INTEGRATION_REGISTRY` in `app/services/integration_hub/registry.py`) is the
declarative catalog of the firm's connected platforms and, for each, the **authoritative owners** it is
composed from. It is metadata only: the Integration Hub layer owns no connector, no credential, and no sync
state — it references the owners and explains the result with a deep link.

## Connected platforms

Each integration declares its `authoritative_owner` (the integration record owner), `connection_owner` (the
authoritative connector owner), `authentication_owner` (the authoritative credential/auth owner),
`synchronization_owner`, `provider_type`, `runtime_gate`, and `deep_links`.

| Integration | Provider type | Connection owner | Sync owner |
| --- | --- | --- | --- |
| `schwab` | custodian | integration.connectors | integration.sync |
| `assetmark` | custodian | integration.connectors | integration.sync |
| `wealthbox` | crm | integration.connectors | integration.sync |
| `taxdome` | tax | integration.connectors | integration.sync |
| `drake` | tax | integration.connectors | integration.sync |
| `betterment` | custodian | integration.connectors | integration.sync |
| `guideline` | recordkeeper | integration.connectors | integration.sync |
| `adp` | payroll | integration.connectors | integration.sync |
| `microsoft_365` | productivity | integration.connectors | microsoft_sync |
| `google_workspace` | productivity | integration.connectors | integration.sync |
| `docusign` | productivity | portal.providers | integration.sync |
| `quickbooks` | recordkeeper | integration.connectors | integration.sync |
| `irs` | filing | integration.connectors | tax_filing_providers |
| `state_efile` | filing | integration.connectors | tax_filing_providers |
| `carrier_apis` | custodian | insurance_integrations | integration.sync |
| `crm_connectors` | crm | integration.connectors | integration.sync |
| `email_providers` | productivity | integration.connectors | communications |
| `calendar_providers` | productivity | integration.connectors | scheduling |

## Ownership boundaries (never re-implemented here)

- **Connectors + credentials** are owned by the D.24 Integration Platform (`integration.connectors` —
  providers seeded disabled; credentials stored as ciphertext/pointers, never plaintext). The registry names
  the connection/auth owner; the layer **never calls** `create_connector` / `set_connector_status` /
  `create_provider`.
- **Synchronization** is owned by `integration.sync` (`run_sync` / `run_due_syncs` — writes never called);
  Microsoft Graph sync by `microsoft_sync`; the actual data movement by the file importers / Graph jobs.
- **Authentication** for M365 is owned by `microsoft_identity` (`get_microsoft_access_token` — never called);
  e-sign by `portal.providers`; insurance ports by `insurance_integrations` (disabled).
- **The integration record** is owned by the Integration Platform; the layer references it.

## How the registry is used

The integrations + connectors dashboards compose `registered_integrations`, `integration_overview`,
`connected_connectors`, `providers`, and `connector_status`. Governance validates that every integration
declares all six owner fields + runtime gate + deep links, that keys are unique, and that the layer contains
no connector/sync/webhook/API **mutation** call and no outbound HTTP client.

See [CONNECTOR_REGISTRY.md](CONNECTOR_REGISTRY.md), [INTEGRATION_HUB.md](INTEGRATION_HUB.md), and
[ADR-058](adr/ADR-058-integration-hub.md).
