# Vendor Management (Phase D.56)

The **Vendor Management** layer (`app/services/vendor_management/`) is a governed, **read-only composition**
that gives vendor / technology governance one operational view of vendors, software, platforms, licensing,
lifecycle, and third-party risk — **without** building a second vendor-management platform, procurement
system, contract repository, CMDB, asset inventory, licensing platform, or risk engine. Every number is
composed on read from an **authoritative owner**; the layer owns no persistence and never modifies a vendor,
renews a license, terminates a contract, alters an integration, or changes a subscription. **Panels carry
counts + status only — never a contract, credential, license key, secret, or procurement payload.**

## What it composes (and never duplicates)

| Concern | Authoritative owner (composed) |
| --- | --- |
| Vendor inventory (providers/connectors) | `app/services/integration/connectors.py` — `list_providers`, `list_connectors`, `list_credentials` (ciphertext-stripped) |
| Certificates / secrets (licensing/renewal) | `app/services/security/secrets.py` — `list_certificates`, `overdue_rotations`, `metrics` |
| Producer licenses | `app/services/insurance_licensing.py` — `list_licenses` (requires insurance.licensing.read) |
| Technology lifecycle (production systems) | `app/services/observability/catalog.py` — `metrics`, `list_environment_profiles`, `list_dependencies` |
| Integration dependency health | `app/services/integration/{sync,service}.py` — `metrics`, `overview_metrics` |
| Third-party risk | `app/services/security/incidents.py` — `metrics`; `app/services/compliance_intelligence` — `supervisory_dashboard` |
| Per-client vendor dependencies | `app/services/integration_hub` — `client_integrations` / `household_integrations` |
| Procurement / contracts / subscriptions | **NONE EXISTS** — declared `not_configured`, never fabricated |

See [VENDOR_REGISTRY.md](VENDOR_REGISTRY.md) for the vendor classes,
[TECHNOLOGY_LIFECYCLE_REGISTRY.md](TECHNOLOGY_LIFECYCLE_REGISTRY.md) for the lifecycle classes, and
[VENDOR_GOVERNANCE.md](VENDOR_GOVERNANCE.md) for the enforced invariants.

## Modules

- `registry.py` — the declarative catalogs: `VENDOR_REGISTRY` (8 vendor classes),
  `TECHNOLOGY_LIFECYCLE_REGISTRY` (8 lifecycle classes), `PANEL_REGISTRY` (20 panels), `VENDOR_DASHBOARDS` (7
  dashboards).
- `model.py` — `PanelResult` + `VendorDashboard`. A panel is emitted only if `is_explainable` (explanation +
  source + deep link).
- `panels.py` — the per-panel compute functions. Read-only, fail-closed, **self-restricting** (a principal
  lacking the panel capability gets a `restricted` panel, never its value; risk panels require `security.view`).
  Counts + status only.
- `service.py` — the engine: `compose_dashboard`, `list_dashboards`, `get_panel`, `vendor_summary`,
  `client_technology`, `household_technology`.
- `gate.py` — runtime gates (`vendor_management.enabled`, `lifecycle.enabled`, `licensing.enabled`) + policy
  composition. No raw environment gating.
- `stats.py` / `metrics.py` — low-cardinality in-process counters, registered into the **single** Analytics
  Registry (`analytics.metrics`). No second metrics registry; never contract/credential/key/secret payloads.
- `diagnostics.py` — internal-only observability (`observability.audit`).
- `governance.py` — read-only invariant checker (never raises), including vendor/licensing mutation-call
  tells.

## Dashboards

`vendors`, `licensing`, `lifecycle`, `renewals`, `third_party_risk`, `operational_dependencies`,
`technology_governance`. Each carries a generated timestamp, governing services, source inventory, explainable
panels, and deep links to the authoritative vendor-owner surface. Dashboards are gated by `integration.view`;
each panel additionally self-restricts to its own capability.

## Surfaces

- **HTTP** (`app/routes/vendor_management.py`, gated by `integration.view`; diagnostics by
  `observability.audit`): `/vendor-management` (HTML), `/api/v1/vendor-management/dashboards`,
  `/dashboard/{key}`, `/summary`, `/registry`, `/panel/{key}`, `/metrics`, `/vendor-management/diagnostics`.
- **Advisor Workspace** — the Technology & Vendor Health panel (`vendor_summary`).
- **Client 360 / Household 360** — the `technology_dependencies` section (`client_technology` /
  `household_technology`, the external vendors an entity depends on).
- **Executive Dashboard** — a `technology_governance` dashboard (composed from existing D.48 widgets; no new
  widget), navigation deep-linking to `/vendor-management`.
- **AI Assist** — summarizes vendor health / dependency counts only; it never approves purchases, renews
  contracts, terminates vendors, alters licensing, or modifies subscriptions.

## Invariants

No new persistence, no new metric, no new capability, no migration (single Alembic head unchanged). No
mutation, no vendor/licensing change, no outbox publication, no audit write, no second store. Every vendor
count comes from an authoritative owner; every dashboard panel is explainable and deep-links to its
authoritative surface. Enforced by `app/services/vendor_management/governance.py` and
`tests/test_vendor_management.py`. See [ADR-061](adr/ADR-061-vendor-management.md).
