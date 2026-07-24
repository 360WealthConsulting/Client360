# ADR-061 — Enterprise Vendor Management & Third-Party Technology Governance: A Read-Only Composition, Not a Second Vendor/Procurement Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Vendor Management / Third-Party Risk / Technology Governance); Security /
Authorization (RBAC ownership); Reliability / Operations; Compliance; Business Operations Owner
(Michael Shelton).

## Context
The mandatory D.56 audit found the platform already owns the vendor / technology read surfaces — but no
procurement, contract, CMDB, asset-inventory, or software-subscription owner exists:

* **Integration Platform providers (D.24)** — `integration.connectors.list_providers(*, provider_type=)` +
  `list_connectors` + `list_credentials` (ciphertext-stripped). The provider registry (`integration_providers`,
  `PROVIDER_TYPES = custodian/crm/tax/payroll/recordkeeper/productivity/filing/accounting/government/other`)
  **is the vendor inventory of record.**
* **Security certificate & secret store (D.54)** — `security.secrets.list_certificates` (`not_after` expiry,
  status valid/expiring/expired), `overdue_rotations`, `metrics` → {overdue_secret_rotations,
  expired_certificates}. Certificates/credentials are the licensing/renewal signal (keys never stored/leaked).
* **Observability service catalog (D.26)** — `catalog.list_services` / `list_environment_profiles` /
  `list_deployment_references` / `metrics` — the production-systems / technology-lifecycle inventory.
* **Insurance licensing** — `insurance_licensing.list_licenses` (producer licenses, `expiry_date`). **Security
  incidents + Compliance Intelligence** — third-party risk counts.

There was **no vendor-management composition layer** unifying these into named, firm-wide views of vendors,
licensing, lifecycle, renewals, third-party risk, operational dependencies, and technology governance.
Building a second vendor-management platform, procurement system, contract repository, CMDB, asset inventory,
licensing platform, or risk engine would violate the "no second system" invariant and duplicate governed,
gated infrastructure.

## Decision
Phase D.56 adds a **governed, read-only vendor-management composition layer**
(`app/services/vendor_management/`) with NO new metrics, NO persistence, and NO mutation:

1. Two declarative **registries** (`registry.py`): `VENDOR_REGISTRY` (8 vendor classes — software vendors,
   custodians, tax providers, insurance carriers, cloud providers, communication providers, infrastructure
   providers, identity providers — each naming authoritative / integration / security / lifecycle owner +
   runtime gate + deep links) and `TECHNOLOGY_LIFECYCLE_REGISTRY` (8 classes — production systems, SaaS
   platforms, infrastructure services, subscriptions, licenses, certificates, integrations, identity
   providers — each naming owner + lifecycle / renewal / support owner + category), plus `PANEL_REGISTRY` (20
   panels) and `VENDOR_DASHBOARDS` (7 dashboards).
2. Normalized read-models (`model.py`): `PanelResult` + `VendorDashboard`, each explainable (explanation +
   source + deep link, a hard emit gate) and reference-only; **counts + status only, never a contract /
   credential / license key / secret / procurement payload**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Integration Platform provider registry, the Security certificate/secret store, the Observability
   service catalog, Insurance licensing, Security incidents, Compliance Intelligence). **Procurement /
   contracts / subscriptions have no authoritative owner** — those registry classes carry a `not_configured`
   owner (the D.55 precedent). Fail-closed; every panel self-restricts (risk panels require `security.view`).
4. The **vendor-management engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `vendor_summary`, plus `client_technology` / `household_technology` (the external vendors an entity depends
   on, from the Integration Hub per-entity read). Every dashboard carries generated timestamp, governing
   services, source inventory, explainable panels, and deep links. Dashboard-level authorization
   (`integration.view`).
5. **Runtime gates** (`vendor_management.enabled` + `lifecycle.enabled` + `licensing.enabled`), **policy
   composition**, **analytics reuse** (four operational counters registered into the ONE Analytics Registry —
   no second registry), internal **diagnostics** (`observability.audit`), and a read-only **governance**
   checker that forbids mutation, persistence, and any vendor / provider / connector / certificate / license /
   secret mutation (`create_provider`, `renew_certificate_reference`, `rotate_secret`, `create_license`, …).
   AI Assist may summarize vendor health but never approves purchases, renews contracts, terminates vendors,
   alters licensing, or modifies subscriptions.

No migration, no new table, no new capability (reuses `integration.view` + `security.view` +
`observability.audit`), no new metric, no new outbox contract. Single Alembic head stays `n5s6u7p8v9w0`.

## Alternatives considered
- **A second vendor-management platform / procurement system / contract repository / CMDB / asset inventory /
  licensing platform / risk engine.** Rejected: the Integration Platform provider registry, the Security
  certificate store, the Observability service catalog, and Insurance licensing are the authoritative owners;
  D.56 composes them. Governance forbids a second store and any vendor/licensing mutation. Where no owner
  exists (procurement / contracts / subscriptions), the layer declares `not_configured` rather than inventing
  one.
- **A second metrics registry.** Rejected: vendor counts come from the owners' reads; the layer registers
  only operational counters (about itself) into the single Analytics Registry — the house style.
- **Persisting composed vendor state.** Rejected: dashboards are a deterministic function of the authoritative
  data at read time; a store would be a vendor warehouse to reconcile, and the layer must never hold vendors,
  contracts, or licenses.

## Reasons for the decision
Vendor / technology governance needs one operational view; the Integration Platform, Security store, and
Observability catalog already own every number with the correct scoping. A read-only composition gives that
view with full explainability (source + deep link) while every vendor stays owned by the Integration Platform
provider registry, every certificate/license by the Security store / Insurance licensing, and every service
by the Observability catalog. Deep links (never inline renewal) route the operator to the authoritative
surface to act. Emitting counts + status only keeps contract contents, credentials, license keys, secrets,
and procurement payloads out of the layer entirely.

## Rationale for avoiding a second vendor/platform management system
A second vendor / procurement platform would require duplicated vendor records, contracts, software
inventories, and licenses, plus its own renewal + risk model — duplicating governed infrastructure and
creating reconciliation + drift + shadow-inventory risk, with no benefit the composition does not already
provide. Composing over the single Integration Platform provider registry keeps one source of truth for every
vendor and zero duplicated inventory.

## Consequences

### Positive consequences
- One firm-wide vendor / technology surface with no second vendor platform, procurement system, contract
  repository, CMDB, licensing platform, or risk engine.
- Record scope + capability are inherited from the composed owner reads; a non-`integration.view` principal
  sees restricted panels, never values; risk panels additionally require `security.view`.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Technology & Vendor Health panel + Client 360 / Household 360 Technology Dependencies
  sections + an Executive Technology Governance dashboard (reusing existing widgets) + AI summarize-only, all
  from one layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- Procurement / contracts / subscriptions panels declare `not_configured` owners until an authoritative
  procurement owner is added to the platform — honest, not a fabricated inventory.
- The layer's coverage is bounded by the owners' read surface; a genuinely new vendor signal is added to the
  owning domain first, then surfaces here.

## Enforcement
`tests/test_vendor_management.py` (two registries + single ownership; explainable dashboard composition;
authorization — unauthorized → None, unentitled panel restricted never valued, risk panels require
`security.view`; runtime + policy gates; the firm summary + client/household rollups; analytics reuse — the 4
counters in the ONE registry; diagnostics; routes registered + capability-gated; AI summarize-only; and the
architecture invariants — no second vendor/licensing/contract system, no duplicated inventories, no mutation,
vendor reads composed from `integration.connectors`, every dashboard deep-links).
`app/services/vendor_management/governance.py` enforces the invariants at runtime. Route count, section
registries, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate (Integration provider registry, Observability catalog metrics) are
exposed only within dashboards whose required capability (`integration.view`) the principal holds; each panel
additionally self-restricts to its own capability. Third-party-risk panels require `security.view`; licensing
panels that compose Insurance licensing require `insurance.licensing.read` internally and fail closed
otherwise.

## Revisit conditions
Revisit when an authoritative procurement / contract / software-asset owner is added to the platform (compose
it here, replacing the `not_configured` classes — never a second vendor platform), or if a materialized vendor
read-model is ever justified (it would be a governed projection, never a second CMDB).

## References
- `app/services/vendor_management/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/vendor_management.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Technology & Vendor Health panel in
  `app/services/workspace/service.py`; Executive Technology Governance dashboard in
  `app/services/executive_intelligence/registry.py`; AI grounding in `app/services/ai_assist/context.py`;
  analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/integration/{connectors,sync,service}.py`, `app/services/security/{secrets,incidents}.py`,
  `app/services/observability/catalog.py`, `app/services/insurance_licensing.py`,
  `app/services/compliance_intelligence/*`, `app/services/integration_hub/*`
- `docs/VENDOR_MANAGEMENT.md`, `docs/VENDOR_REGISTRY.md`, `docs/TECHNOLOGY_LIFECYCLE_REGISTRY.md`,
  `docs/VENDOR_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_vendor_management.py`; relates to ADR-024, ADR-026, ADR-046 through ADR-060
