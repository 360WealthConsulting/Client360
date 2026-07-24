# Technology Lifecycle Registry (Phase D.56)

The **technology lifecycle registry** (`TECHNOLOGY_LIFECYCLE_REGISTRY` in
`app/services/vendor_management/registry.py`) is the declarative catalog of the firm's technology-lifecycle
classes and, for each, the **authoritative owner** plus its lifecycle / renewal / support owners. It is
metadata only: the Vendor Management layer owns no lifecycle, renewal, or subscription state — it references
the owners and explains the result with a deep link.

## Technology-lifecycle classes

Each class declares its `category`, `owner` (or `not_configured`), `lifecycle_owner`, `renewal_owner` (or
`not_configured`), `support_owner`, and `runtime_gate`.

| Class | Category | Owner | Renewal owner |
| --- | --- | --- | --- |
| `production_systems` | production_system | observability.catalog | not_configured |
| `saas_platforms` | saas | integration.connectors | not_configured |
| `infrastructure_services` | infrastructure | observability.catalog | not_configured |
| `subscriptions` | subscription | not_configured | not_configured |
| `licenses` | license | insurance_licensing | insurance_licensing |
| `certificates` | certificate | security.secrets | security.secrets |
| `integrations` | integration | integration.connectors | integration.connectors |
| `identity_providers` | identity | security.providers | (n/a) |

## The subscriptions / procurement gap (reported honestly)

There is **no authoritative software-subscription, procurement, or contract owner in the platform today** (the
D.56 audit confirmed zero procurement / contract / subscription / CMDB / software-asset services). Rather than
fabricate a subscription inventory, the `subscriptions` class (and the procurement-adjacent owners) are
declared `owner = not_configured` / `renewal_owner = not_configured`. When an authoritative procurement owner
is added to the platform, the layer composes it — never a second procurement system. This mirrors the D.55
backup precedent: report the owner's real state, never invent one.

## Ownership boundaries (never re-implemented here)

- **Production systems / infrastructure** are owned by the Observability service catalog
  (`observability.catalog`). The `production_systems` / `service_environments` panels compose it — no second
  CMDB.
- **Licenses** are owned by `insurance_licensing` (producer licenses, `expiry_date` = renewal signal). The
  layer never renews a license.
- **Certificates** are owned by `security.secrets` (`not_after` = expiry). The layer never renews a
  certificate.
- **Integrations** are owned by the Integration Platform (`integration.connectors` / `integration.sync`). The
  layer never alters an integration.

## How the registry is used

The lifecycle + renewals + technology-governance dashboards compose `registered_lifecycle` (this registry),
`production_systems`, `service_environments`, `expiring_certificates`, `overdue_rotations`,
`expiring_licenses`, and `technology_health`. Governance validates that every class declares all six fields
(category, owner, lifecycle owner, renewal owner, support owner, runtime gate), and that keys are unique.

See [VENDOR_REGISTRY.md](VENDOR_REGISTRY.md), [VENDOR_MANAGEMENT.md](VENDOR_MANAGEMENT.md), and
[ADR-061](adr/ADR-061-vendor-management.md).
