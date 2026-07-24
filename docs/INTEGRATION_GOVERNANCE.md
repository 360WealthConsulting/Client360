# Integration Hub Governance (Phase D.53)

`app/services/integration_hub/governance.py` is a read-only checker that verifies the Integration Hub layer
stays a **composition** over the authoritative integration owners and never becomes a second integration
platform, ESB, API gateway, synchronization engine, webhook processor, message broker, or event bus. It
returns `{ok, issue_count, findings}` and **never raises** into normal use. `validate_integration_hub()` is
surfaced through the internal diagnostics endpoint (`/integration-hub/diagnostics`, gated by
`observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit_event`). No `rm_*` projection table is read
   directly.
2. **No second integration platform / no mutation / no sync trigger / no API invocation.** No module calls an
   integration / sync / webhook / API / connector **mutation** — `run_sync(`, `run_due_syncs(`,
   `create_connector(`, `create_provider(`, `create_endpoint(`, `record_delivery(`, `verify_endpoint(`,
   `create_subscription(`, `create_api_client(`, `set_connector_status(`, `invoke_port(`,
   `get_microsoft_access_token(`, `record_sync_health(`, `apply_signature_event(`,
   `create_signature_request(`, `publish_safe(`. The layer composes **reads** only.
3. **No outbound HTTP (no second API gateway / connector).** No module contains an outbound HTTP tell —
   `httpx`, `requests.get/post/put/delete`, `aiohttp`. Connector I/O stays with the authoritative connectors.
4. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
5. **Registry completeness + single ownership.** Every integration declares authoritative + connection +
   authentication + synchronization owner + provider type + runtime gate + deep links; every connector
   declares protocol + authentication + polling + webhook + retry + monitoring owner + runtime gate; every
   dashboard declares owner + audience + runtime gate + navigation + panels + required capabilities +
   governing services, and references only registered panels; every panel declares owner + source + deep link
   + explainability + permission; all registry keys are unique.
6. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in
   both `model.py` and `panels.py`; a non-explainable panel is never emitted.
7. **No raw environment gating.** Gates flow through the Runtime Engine (`runtime.consumption.feature_enabled`)
   and policy through the Policy Engine — never `os.getenv` / `os.environ`.

## No secrets, tokens, credentials, or client payloads, ever

Panels and summaries carry **counts + status only** — never secrets, tokens, credentials, or client payloads.
Credential references are composed as counts only (the Integration Platform already strips ciphertext/signing
secrets from its reads). Diagnostics and analytics counters are low-cardinality aggregates about the layer
itself.

## Authorization & least privilege

- Integration routes are gated by `integration.view`; diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities` (`integration.view`);
  otherwise `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to `integration.view`: a principal lacking it receives a `restricted` panel
  with `value = None` — never leaked.
- All composed reads inherit the record scope + capability checks of their authoritative owner (the
  Integration Platform's per-submodule reads, the Event outbox diagnostics).

## AI Assist boundary

AI Assist may **summarize** integration counts (connector health, synchronization status, authentication
warnings, webhook health, API availability) — fact class `DERIVED`, counts only, deep links only. It **never**
reconnects systems, refreshes tokens, triggers synchronization, invokes mutations, bypasses authentication, or
changes integration settings — every fact comes from a composed section/summary.

## Enforcement

`tests/test_integration_hub.py` exercises the registries, explainable composition, authorization (`None` +
restricted), gate/policy behavior, the analytics-counter reuse, diagnostics, the routes (registered +
capability-gated), AI summarize-only, and the architecture invariants (no second integration platform / sync
engine / webhook processor / API gateway, no mutation, no outbound HTTP, integration reads composed from the
Integration Platform, every dashboard deep-links, every sync summary names an authoritative owner). Route
count, section registries, ADR count, and the single migration head are guarded by
`tests/test_platform_architecture.py`, `tests/test_client360_workspace.py`,
`tests/test_household360_workspace.py`, `tests/test_architecture_decision_records.py`, and the manifest.

See [INTEGRATION_HUB.md](INTEGRATION_HUB.md), [INTEGRATION_REGISTRY.md](INTEGRATION_REGISTRY.md),
[CONNECTOR_REGISTRY.md](CONNECTOR_REGISTRY.md), and [ADR-058](adr/ADR-058-integration-hub.md).
