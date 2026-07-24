# Integration Hub (Phase D.53)

The **Integration Hub** layer (`app/services/integration_hub/`) is a governed, **read-only composition** that
gives operations leadership a single governed view of all external systems, integrations, synchronization
health, API connectivity, and connector status — **without** building a second integration platform, ESB, API
gateway, synchronization engine, webhook processor, message broker, or event bus. Every number is composed on
read from an **authoritative owner**; the layer owns no persistence, makes no outbound HTTP call, and never
mutates an external system, triggers synchronization, invokes an API, refreshes a token, reconnects a system,
or changes an integration setting. **Panels carry counts + status only — never a secret, token, credential,
or client payload.**

## What it composes (and never duplicates)

| Concern | Authoritative owner (composed) |
| --- | --- |
| Integration overview / providers / connectors / credentials | `app/services/integration/service.py` + `connectors.py` (D.24) |
| Synchronization health / runs / conflicts | `app/services/integration/sync.py` — `metrics`, `list_sync_runs`, `list_sync_profiles` |
| Webhooks (endpoints / deliveries) | `app/services/integration/webhooks.py` — `metrics`, `list_endpoints`, `list_deliveries` |
| API clients / usage | `app/services/integration/api.py` — `metrics`, `list_api_clients`, `list_usage` |
| Event catalog | `app/services/integration/events.py` — `list_definitions` |
| Event routing / outbox | `app/services/events/diagnostics.py` — `event_counts`, `subscriber_health` |
| External source provenance (per client) | `app/services/governance/mdm.py` — `person_lineage` (`source_contacts.source_system`) |

See [INTEGRATION_REGISTRY.md](INTEGRATION_REGISTRY.md) for the connected platforms,
[CONNECTOR_REGISTRY.md](CONNECTOR_REGISTRY.md) for the connectors, and
[INTEGRATION_GOVERNANCE.md](INTEGRATION_GOVERNANCE.md) for the enforced invariants.

## Modules

- `registry.py` — the declarative catalogs: `INTEGRATION_REGISTRY` (18 connected platforms),
  `CONNECTOR_REGISTRY` (9 connectors), `PANEL_REGISTRY` (19 panels), `INTEGRATION_DASHBOARDS` (7 dashboards).
- `model.py` — `PanelResult` + `IntegrationDashboard`. A panel is emitted only if `is_explainable`
  (explanation + source + deep link).
- `panels.py` — the per-panel compute functions. Read-only, fail-closed, **self-restricting** (a principal
  lacking `integration.view` gets a `restricted` panel, never its value). Counts + status only.
- `service.py` — the engine: `compose_dashboard`, `list_dashboards`, `get_panel`, `integration_summary`,
  `client_integrations`, `household_integrations`.
- `gate.py` — runtime gates (`integrations.enabled`, `connectors.enabled`, `synchronization.enabled`) + policy
  composition. No raw environment gating.
- `stats.py` / `metrics.py` — low-cardinality in-process counters, registered into the **single** Analytics
  Registry (`analytics.metrics`). No second metrics registry; never secrets/tokens/payloads.
- `diagnostics.py` — internal-only observability (`observability.audit`).
- `governance.py` — read-only invariant checker (never raises), including outbound-HTTP + mutation-call tells.

## Dashboards

`integrations`, `synchronization`, `authentication`, `webhooks`, `connectors`, `api_health`, `event_routing`.
Each carries a generated timestamp, governing services, source inventory, explainable panels, and deep links
to the authoritative connector-owner surface. Dashboards are gated by `integration.view`; each panel
additionally self-restricts to `integration.view`.

## Surfaces

- **HTTP** (`app/routes/integration_hub.py`, gated by `integration.view`; diagnostics by
  `observability.audit`): `/integration-hub` (HTML), `/api/v1/integration-hub/dashboards`, `/dashboard/{key}`,
  `/summary`, `/registry`, `/panel/{key}`, `/metrics`, `/integration-hub/diagnostics`.
- **Advisor Workspace** — the Integration Health panel (`integration_summary`).
- **Client 360 / Household 360** — the `external_integrations` section (`client_integrations` /
  `household_integrations`, external-source-system provenance from person lineage).
- **Executive Dashboard** — an `integration_health` dashboard (composed from existing D.48 widgets; no new
  widget), navigation deep-linking to `/integration-hub`.
- **AI Assist** — summarizes integration counts only; it never reconnects systems, refreshes tokens, triggers
  synchronization, invokes mutations, bypasses authentication, or changes integration settings.

## Invariants

No new persistence, no new metric, no new capability, no migration (single Alembic head unchanged). No
mutation, no synchronization trigger, no API invocation, no outbound HTTP, no outbox publication, no audit
write, no second platform. Every integration count comes from the Integration Platform; every dashboard panel
is explainable and deep-links to its authoritative surface. Enforced by
`app/services/integration_hub/governance.py` and `tests/test_integration_hub.py`. See
[ADR-058](adr/ADR-058-integration-hub.md).
