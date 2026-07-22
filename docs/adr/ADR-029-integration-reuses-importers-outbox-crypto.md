# ADR-029 — Enterprise Integration reuses importers/OAuth/outbox/crypto; metadata-only, no broker, no plaintext secrets

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Integration); Security Architecture (credential handling);
Business Operations Owner (Michael Shelton — external-system connectivity requirements). Authorized
compliance reviewer: Not yet designated.

## Context
The platform already integrates with external systems through domain-specific parts: file importers
(`app/importers/*` → `import_jobs` + `source_contacts`), Microsoft 365 (`microsoft_identity` MSAL
OAuth, `microsoft_accounts.token_cache_encrypted` via Fernet, `record_sync_health`, sync jobs), an
in-code **disabled-port** pattern (`InsuranceIntegrationPort`/`benefits_providers` — `enabled=False`,
never touches payload), the transactional **outbox** (`outbox_events` + `publish`/`publish_event` +
the canonical `Envelope`) as the internal event bus, a notification provider registry + `RetryPolicy`,
and two Fernet field-crypto helpers (`token_crypto`, `benefits_crypto`). There was **no** generic
provider/connector/sync-profile/webhook/API-client table, **no** webhook or HMAC infrastructure, and
**no** API-key/rate-limit surface (the `/api/v1/*` surface is session-authed only). The risk of a new
integration domain is that it re-implements provider logic, stores plaintext secrets, or introduces a
broker.

## Decision
Enterprise Integration is a new authoritative **integration domain** that owns **integration
metadata only** and is **never a source of truth**. Business domains remain authoritative.
- **Owns:** `integration_providers`, `integration_connectors` (instance/config/status),
  `integration_credential_references`, `integration_sync_profiles`/`_sync_runs`/`_sync_conflicts`,
  `integration_webhook_endpoints`/`_subscriptions`/`_deliveries`, `integration_api_clients`/`_api_usage`,
  `integration_event_definitions`/`_event_subscriptions`, `integration_data_profiles` (import/export),
  and `integration_events` (an **append-only** audit ledger).
- **Reuses, never duplicates provider logic.** Sync runs **reference** the existing `import_jobs`,
  `automation_runs`, and `microsoft_accounts` ledgers (nullable ids) — they record run metadata; the
  actual data movement is the existing importers/M365 jobs. **Automation executes** scheduled sync (a
  new `integration_sync` job type added to the D.22 dispatch registry; the `JOB_TYPES` CHECK widened).
- **Event bus = the outbox, no broker.** Integration owns an event *catalog* (definitions +
  subscriptions as metadata) and publishes real events through `app.platform.outbox.publish_event`
  with a canonical `Envelope` and deterministic ids (workflow-events pattern). **No Kafka/RabbitMQ.**
- **Webhooks are metadata only.** Endpoints/subscriptions/deliveries model outbound webhook metadata
  with HMAC signing (signature computed in-process from a Fernet-encrypted secret); **no outbound HTTP
  is performed this phase** (mirroring D.18 delivery / D.21 export). Delivery retry is metadata
  (attempts + `available_at`).
- **No plaintext secrets.** Credential/webhook secrets are either a **pointer** to an existing
  encrypted store (`microsoft_accounts.id`) or **Fernet ciphertext** via a new `integration_crypto`
  helper (fail-closed, keyed by `INTEGRATION_SECRET_KEY`, never logged). Ciphertext is stripped from
  all API responses. Connector `config` is rejected if it contains secret-looking keys.
- **API platform is metadata.** Clients/usage/rate-limits are recorded metadata (aggregated usage
  windows, not per-request events) — the authentication middleware is **not** changed and **no
  plaintext API key** is stored.
- **Integrations:** **Workflow** may launch integration flows (`launch_workflow`; connectors carry a
  `workflow_instance_id`-style reference where relevant). **Automation** executes scheduled sync.
  **Data Governance** governs imported data quality (Integration records sync metadata + lineage-
  compatible references; Governance owns findings — Integration performs no governance decision).
  **Analytics** consumes integration statistics (sync failures, connector errors); Integration never
  depends on Analytics. **Timeline** receives approved, **client-anchored** lifecycle events
  (sync completed/failed on a client-scoped run); firm-level integration events record to
  `integration_events` only.
- **Security:** `integration.view/manage/execute/audit*/admin*` (`*` = sensitive), gated in-route
  (`/integration` matches no middleware RULE). Sync/verify/publish/status require `integration.execute`.

## Alternatives considered
1. **Add Kafka/RabbitMQ.** Rejected: the phase forbids an external broker; the outbox is the event
   bus. Integration publishes through it.
2. **Store provider secrets in Integration tables (plaintext).** Rejected: secrets are pointers or
   Fernet ciphertext; the connector config is scrubbed of secret-looking keys.
3. **Re-implement provider connectivity (perform sync I/O here).** Rejected: the importers/M365 jobs
   own provider logic; Integration records run metadata and Automation triggers execution.
4. **Perform outbound webhook HTTP now.** Rejected for this metadata-first phase; deliveries are
   metadata (matching D.18/D.21). A real HTTP delivery worker is a future phase (new ADR).
5. **Change the auth middleware to enforce API keys/rate limits.** Rejected: API clients/usage/limits
   are metadata this phase; enforcement middleware is a bigger, separately-approved change.

## Reasons for the decision
The firm needs one authoritative model of *which external systems are connected, how they sync,
which webhooks/API clients exist, and what events flow* — with credential governance and observability
— without a broker, without duplicating provider logic, and without ever holding a plaintext secret. A
metadata domain that reuses the importers/OAuth/outbox/crypto delivers this while preserving every ADR
and the D.5 golden.

## Consequences
### Positive consequences
- One authoritative integration-metadata domain (providers/connectors/credentials/sync/webhooks/API/
  events) reusing the existing importers, M365 OAuth, outbox, and Fernet crypto.
- No broker, no duplicated provider logic, no plaintext secrets; the auth middleware is unchanged.
- Automation can execute scheduled sync; Analytics gains integration metrics; the timeline receives
  only approved client-anchored events.

### Negative consequences and tradeoffs
- Webhook deliveries and API usage are metadata only — no outbound HTTP and no API-key enforcement
  this phase (documented limitations; future phases + ADRs).
- Sync runs record metadata that references `import_jobs`/`automation_runs`; the actual data movement
  remains in the existing jobs (two run ledgers, by design).
- The D.22 `JOB_TYPES` CHECK constraints were widened again (a cross-domain migration touch) to admit
  `integration_sync` — documented and reversible.
- The 12 seeded providers are **disabled by default** (mirroring the disabled-port pattern); no
  provider logic activates until a concrete adapter + credentials are added.

## Enforcement
- `app/database/integration_tables.py::define_integration_tables` (registered in
  `app/database/schema.py`; reflected in `app/db.py`). Migration `v2a3b4c5d6e7` (15 tables +
  append-only trigger on `integration_events` + 5 `integration.*` capabilities + widened automation
  `JOB_TYPES` + 12 disabled providers + 2 event definitions). Services
  `app/services/integration/{common,connectors,sync,webhooks,api,events,service}.py`;
  `app/security/integration_crypto.py` (Fernet, fail-closed). Routes `app/routes/integration.py`
  (in-route `integration.*` gating; `/integration` matches no middleware RULE). Automation
  `integration_sync` handler in `app/services/automation/dispatch.py`. The importers, M365 OAuth, the
  outbox, the Fernet helpers, the auth middleware, and the D.5 golden are untouched. Integration is
  registered in `source_producer_modules` (must not import composition layers). Tests:
  `tests/test_integration_platform.py`; manifest / platform-architecture / route-count guards updated.

## Exceptions
None currently approved.

## Revisit conditions
Implementing outbound webhook HTTP delivery, API-key/rate-limit enforcement middleware, a real
provider connectivity engine inside Integration, or an external message broker would each warrant a
new or superseding ADR (and, for credential-handling changes, security sign-off).

## References
- `app/services/integration/`, `app/routes/integration.py`, `app/database/integration_tables.py`,
  `app/security/integration_crypto.py`, migration
  `migrations/versions/v2a3b4c5d6e7_integration_platform.py`
- Reused infra: `app/importers/*` (`import_jobs`), `app/services/microsoft_identity.py`,
  `app/platform/outbox.py` + `app/platform/events.py`, `app/security/token_crypto.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_integration_platform.py`; relates to ADR-002, ADR-005, ADR-009, ADR-016, ADR-022,
  ADR-027, ADR-028
