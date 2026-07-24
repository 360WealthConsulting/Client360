# Vendor Registry (Phase D.56)

The **vendor registry** (`VENDOR_REGISTRY` in `app/services/vendor_management/registry.py`) is the declarative
catalog of the firm's vendor classes and, for each, the **authoritative owners** it is composed from. It is
metadata only: the Vendor Management layer owns no vendor records — the vendor inventory of record is the
Integration Platform provider registry. The layer references the owners and explains the result with a deep
link.

## Vendor classes

Each vendor class declares its `authoritative_owner` (the vendor record owner), `integration_owner`,
`security_owner`, `lifecycle_owner`, the Integration Platform `provider_type` it maps to, `runtime_gate`, and
`deep_links`.

| Vendor class | Authoritative owner | Provider type | Lifecycle owner |
| --- | --- | --- | --- |
| `software_vendors` | integration.connectors | productivity | observability.catalog |
| `custodians` | integration.connectors | custodian | observability.catalog |
| `tax_providers` | integration.connectors | tax | observability.catalog |
| `insurance_carriers` | organization_service | other | insurance_licensing |
| `cloud_providers` | integration.connectors | other | observability.catalog |
| `communication_providers` | integration.connectors | productivity | observability.catalog |
| `infrastructure_providers` | integration.connectors | other | observability.catalog |
| `identity_providers` | security.providers | other | observability.catalog |

## The vendor inventory of record

The authoritative vendor inventory is the **Integration Platform provider registry**
(`integration.connectors.list_providers`, backed by `integration_providers`; `PROVIDER_TYPES = custodian /
crm / tax / payroll / recordkeeper / productivity / filing / accounting / government / other`). The
`vendor_inventory` / `connected_vendors` panels compose it directly. Insurance carriers are modeled as
organization entities (there is no standalone carrier registry read); identity providers are owned by
`security.providers`. No vendor is duplicated here.

## Ownership boundaries (never re-implemented here)

- **Vendor records + connectors** are owned by the Integration Platform (`integration.connectors`). The
  registry names the integration owner; the layer **never calls** `create_provider` / `create_connector` /
  `set_connector_status` — governance forbids it.
- **Certificates / secrets** (a licensing/renewal signal) are owned by `security.secrets` (keys/ciphertext
  never leaked). The layer never renews a certificate or rotates a secret.
- **Producer licenses** are owned by `insurance_licensing`. The layer never creates or renews a license.
- **Technology lifecycle** is owned by the Observability service catalog (see
  [TECHNOLOGY_LIFECYCLE_REGISTRY.md](TECHNOLOGY_LIFECYCLE_REGISTRY.md)).

## How the registry is used

The vendors dashboard composes `vendor_inventory` (Integration providers), `registered_vendors` (this
registry), and `connected_vendors`. Governance validates that every vendor class declares all owner fields +
provider type + runtime gate + deep links, that keys are unique, and that the layer contains no vendor
**mutation** call.

See [TECHNOLOGY_LIFECYCLE_REGISTRY.md](TECHNOLOGY_LIFECYCLE_REGISTRY.md), [VENDOR_MANAGEMENT.md](VENDOR_MANAGEMENT.md),
and [ADR-061](adr/ADR-061-vendor-management.md).
