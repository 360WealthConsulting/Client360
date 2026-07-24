# ADR-058 — Enterprise Integration Hub & Connected Platform Governance: A Read-Only Composition, Not a Second Integration Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Integrations / Connected Platforms); Reliability / Operations; Security /
Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.53 audit found the platform already owns every authoritative integration owner, connector,
API surface, synchronization owner, and event source — anchored by the **D.24 Integration Platform**
(`app/services/integration/`), which is explicitly metadata-only and composition-friendly:

* **Integration overview / providers / connectors / credentials** — `integration/service.overview_metrics`
  + `integration/connectors.py` (`list_providers`, `list_connectors`, `list_credentials` — ciphertext/
  pointers only, never plaintext).
* **Synchronization** — `integration/sync.py` (`list_sync_profiles`, `list_sync_runs`, `list_conflicts`,
  `metrics`) over `integration_sync_runs` (run metadata only — the actual data movement is owned by the file
  importers / M365 Graph jobs / portfolio_import, which sync runs reference, never duplicate).
* **Webhooks** — `integration/webhooks.py` (`list_endpoints`, `list_deliveries`, `metrics` — signing secret
  stripped). **API clients** — `integration/api.py` (`list_api_clients`, `list_usage`, `metrics`).
* **Event catalog** — `integration/events.py`; **event routing** — the Event outbox
  (`events/diagnostics.event_counts`, `subscriber_health`) + Event registry.
* **Real connectors** — Microsoft 365 / Graph (`app/connectors/microsoft365/`, `microsoft_identity`), OIDC
  (`app/integrations/identity/`), the portal signature provider registry (`app/portal/providers.py`), and
  the disabled insurance / tax-filing ports. File importers (Schwab / Wealthbox / AssetMark / Dave Ramsey)
  are the only ingested external sources; source provenance lives on `source_contacts.source_system`.

There was **no integration-hub composition layer** unifying these into named, firm-wide views of
integrations, synchronization, authentication, webhooks, connectors, API health, and event routing. Building
a second integration platform, ESB, API gateway, synchronization engine, webhook processor, message broker,
or event bus would violate the "no second system" invariant and duplicate governed, gated infrastructure.

## Decision
Phase D.53 adds a **governed, read-only integration-hub composition layer**
(`app/services/integration_hub/`) with NO new metrics, NO persistence, and NO mutation:

1. Two declarative **registries** (`registry.py`): `INTEGRATION_REGISTRY` (18 connected platforms — Schwab,
   AssetMark, Wealthbox, TaxDome, Drake, Betterment, Guideline, ADP, Microsoft 365, Google Workspace,
   DocuSign, QuickBooks, IRS, State e-file, carrier APIs, CRM connectors, email providers, calendar providers
   — each naming authoritative / connection / authentication / synchronization owner + runtime gate + deep
   links) and `CONNECTOR_REGISTRY` (9 connectors — protocol, authentication, polling / webhook / retry /
   monitoring owner + runtime gate), plus `PANEL_REGISTRY` (19 panels) and `INTEGRATION_DASHBOARDS` (7
   dashboards).
2. Normalized read-models (`model.py`): `PanelResult` + `IntegrationDashboard`, each explainable (explanation
   + source + deep link, a hard emit gate) and reference-only; **counts + status only, never a secret /
   token / credential / client payload**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Integration Platform service / sync / connectors / webhooks / api / events, the Event outbox +
   registry). Fail-closed; every panel self-restricts to `integration.view`.
4. The **integration-hub engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `integration_summary`, plus `client_integrations` / `household_integrations` (the external source systems
   an entity connected from, composed from the authoritative person lineage). Every dashboard carries
   generated timestamp, governing services, source inventory, explainable panels, and deep links.
   Dashboard-level authorization (`integration.view`).
5. **Runtime gates** (`integrations.enabled` + `connectors.enabled` + `synchronization.enabled`), **policy
   composition**, **analytics reuse** (four operational counters registered into the ONE Analytics Registry —
   no second registry), internal **diagnostics** (`observability.audit`), and a read-only **governance**
   checker that forbids mutation, persistence, any integration / sync / webhook / API mutation call
   (`run_sync`, `create_connector`, `record_delivery`, `get_microsoft_access_token`, …), and any outbound
   HTTP client (`httpx` / `requests` / `aiohttp`). AI Assist may summarize integration counts but never
   reconnects systems, refreshes tokens, triggers synchronization, invokes mutations, bypasses
   authentication, or changes integration settings.

No migration, no new table, no new capability (reuses `integration.view` + `observability.audit`), no new
metric, no new outbox contract. Single Alembic head stays `n5s6u7p8v9w0`.

## Alternatives considered
- **A second integration platform / ESB / API gateway / synchronization engine / webhook processor / event
  bus.** Rejected: the D.24 Integration Platform + the Event outbox are the authoritative owners; D.53
  composes them. Governance forbids a second platform, outbound HTTP, and any mutation call.
- **A second metrics registry.** Rejected: integration counts come from the Integration Platform's
  `metrics()`/`overview_metrics()` reads; the layer registers only operational counters (about itself) into
  the single Analytics Registry — the house style.
- **Persisting composed integration state.** Rejected: dashboards are a deterministic function of the
  authoritative data at read time; a store would be an integration warehouse to reconcile, and the layer must
  never hold connector/sync state (that is the Integration Platform's job).

## Reasons for the decision
Operations leadership needs one connected-platform view; the Integration Platform already owns every number
with the correct scoping. A read-only composition gives that view with full explainability (source + deep
link) while every connector stays owned by the Integration Platform, every sync run by its sync engine, every
webhook by its delivery ledger, every API client by its client registry, and every event by the outbox. Deep
links (never inline reconnect/sync) route the operator to the authoritative surface to act. Emitting counts +
status only keeps secrets, tokens, credentials, and client payloads out of the layer entirely.

## Rationale for avoiding a second integration platform
A second integration platform / ESB / API gateway would require duplicated connector + credential state, a
parallel sync + webhook + event-routing model, its own outbound HTTP + auth, and its own access model —
duplicating governed, gated infrastructure and creating reconciliation + drift + double-sync + credential-
sprawl risk, with no benefit the composition does not already provide. Composing over the single Integration
Platform keeps one source of truth for every connector and sync, one place credentials live, and zero
outbound HTTP from this layer.

## Consequences

### Positive consequences
- One firm-wide integration surface with no second integration platform, ESB, API gateway, sync engine,
  webhook processor, or event bus.
- Record scope + capability are inherited from the composed Integration Platform reads; a
  non-`integration.view` principal sees restricted panels, never values, and never a secret/token/payload.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Integration Health panel + Client 360 / Household 360 External Integrations sections + an
  Executive Integration Health dashboard (reusing existing widgets) + AI summarize-only, all from one layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- Per-client / per-household integration rollups compose the external source systems from person lineage
  (source_contacts.source_system) — a provenance view, not a live connector-health-per-client view (no such
  per-client read exists).
- The layer's coverage is bounded by the Integration Platform's read surface; a genuinely new connector
  signal is added to the Integration Platform first, then surfaces here.

## Enforcement
`tests/test_integration_hub.py` (two registries + single ownership; explainable dashboard composition;
authorization — unauthorized → None, unentitled panel restricted never valued; runtime + policy gates; the
firm summary + client/household rollups; analytics reuse — the 4 counters in the ONE registry; diagnostics;
routes registered + capability-gated; AI summarize-only; and the architecture invariants — no second
integration platform / sync engine / webhook processor / API gateway, no mutation, no outbound HTTP,
integration reads composed from the Integration Platform, every dashboard deep-links, every sync summary
names an authoritative owner). `app/services/integration_hub/governance.py` enforces the invariants at
runtime (including outbound-HTTP + mutation-call tells). Route count, section registries, and migration head
are guarded by `tests/test_platform_architecture.py` + `tests/test_client360_workspace.py` +
`tests/test_household360_workspace.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate (Integration Platform overview/metrics, Event outbox diagnostics) are
exposed only within dashboards whose required capability (`integration.view`) the principal holds; each panel
additionally self-restricts to `integration.view`, so a value is never shown to a principal lacking that
capability.

## Revisit conditions
Revisit when a new connector signal is required (add it to the Integration Platform), when live per-client
connector health is needed (add a scoped read to the Integration Platform, never a second sync engine), or if
a materialized integration read-model is ever justified (it would be a governed projection, never a second
integration platform).

## References
- `app/services/integration_hub/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/integration_hub.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Integration Health panel in
  `app/services/workspace/service.py`; Executive Integration Health dashboard in
  `app/services/executive_intelligence/registry.py`; AI grounding in `app/services/ai_assist/context.py`;
  analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/integration/*` (`service.py`, `sync.py`, `connectors.py`, `webhooks.py`, `api.py`,
  `events.py`), `app/services/events/*`, `app/services/governance/mdm.py`
- `docs/INTEGRATION_HUB.md`, `docs/INTEGRATION_REGISTRY.md`, `docs/CONNECTOR_REGISTRY.md`,
  `docs/INTEGRATION_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_integration_hub.py`; relates to ADR-024, ADR-034, ADR-046 through ADR-057
