# Connector Registry (Phase D.53)

The **connector registry** (`CONNECTOR_REGISTRY` in `app/services/integration_hub/registry.py`) is the
declarative catalog of the firm's connector classes and, for each, the **authoritative owners** that operate
it. It is metadata only: the Integration Hub layer operates no connector, polls nothing, delivers no webhook,
and retries nothing — it references the owners and explains the result with a deep link.

## Connectors

Each connector declares its `protocol`, `authentication`, `polling_owner`, `webhook_owner`, `retry_owner`,
`monitoring_owner`, and `runtime_gate`.

| Connector | Protocol | Auth | Polling owner | Webhook owner |
| --- | --- | --- | --- | --- |
| `file_import` | file | none | importers | none |
| `rest_api` | rest | oauth2 | integration.sync | integration.webhooks |
| `graph_api` | graph | msal | microsoft_sync | integration.webhooks |
| `oidc_identity` | oidc | oauth2 | none | none |
| `webhook_inbound` | webhook | hmac | none | integration.webhooks |
| `webhook_outbound` | webhook | hmac | none | integration.webhooks |
| `esign_provider` | rest | oauth2 | portal.providers | portal.signatures |
| `insurance_port` | port | disabled | insurance_integrations | none |
| `tax_filing_port` | port | disabled | tax_filing_providers | none |

## Ownership boundaries (never re-implemented here)

- **Polling / synchronization** is owned by `integration.sync` (+ `microsoft_sync`, `importers`). The registry
  names the polling owner; the layer never polls or runs a sync.
- **Webhooks** are owned by `integration.webhooks` (endpoints, HMAC signing, delivery ledger). The registry
  names the webhook owner; the layer **never calls** `create_endpoint` / `record_delivery` / `verify_endpoint`
  — governance forbids it.
- **Retry** is owned by the sync engine / `import_jobs` / provider ports. The registry names the retry owner;
  the layer never retries a delivery or run.
- **Monitoring** is owned by `integration.service` / `microsoft_identity` / the provider ports. The layer
  composes their read-only status; it never performs an outbound HTTP call (governance forbids `httpx` /
  `requests` / `aiohttp` in this layer).

## How the registry is used

The connectors + webhooks + api_health + event_routing dashboards compose `registered_connectors`,
`providers`, `connector_status`, `webhook_metrics`, `webhook_endpoints`, `webhook_deliveries`, `api_metrics`,
`api_clients`, `api_usage`, `event_activity`, `event_subscribers`, and `integration_events`. Governance
validates that every connector declares all seven fields (protocol, authentication, polling / webhook / retry
/ monitoring owner + runtime gate), that keys are unique, and that the layer contains no webhook/API mutation
call and no outbound HTTP.

See [INTEGRATION_REGISTRY.md](INTEGRATION_REGISTRY.md), [INTEGRATION_HUB.md](INTEGRATION_HUB.md), and
[ADR-058](adr/ADR-058-integration-hub.md).
