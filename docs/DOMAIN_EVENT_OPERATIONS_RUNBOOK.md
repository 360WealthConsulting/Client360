# Domain Event Operations Runbook (Phase D.34 + D.35)

Operating the domain-event model over the transactional outbox. The outbox is the **sole** internal
event bus and event log; there is no external broker.

## Surfaces

- **Dashboard:** `GET /events` (`observability.view`).
- **Registry:** `GET /events/registry`, `/subscriptions`, `/contracts/{event_type}`, `/graph`,
  `/adoption` (`observability.view`).
- **Producer adoption:** `GET /events/producers` (`observability.view`) — active vs stale producers,
  adoption coverage, per-domain event flow (published / awaiting delivery / dead-lettered).
- **Governance:** `GET /events/governance` (`observability.audit`),
  `POST /events/governance/validate` (`observability.execute`).
- **Flow diagnostics:** `GET /events/diagnostics`, `/dead-letters`, `/{event_id}`,
  `/{event_id}/replay` (`observability.audit`; replay is a read-only reconstruction).

## Delivery is dark-launched by default

The outbox dispatcher is gated OFF (`OUTBOX_DISPATCHER_ENABLED`, `app/config.py`). With it off, domain
events are persisted `pending` in `outbox_events` and **no consumer runs** — producer adoption changes
nothing about runtime behavior. Enabling the dispatcher registers the consumers (in the scheduler's
gated block) and begins at-least-once delivery with idempotency + dead-lettering.

## Enabling a consumer for a domain event

1. Write an **idempotent** handler `def on_x(event_view): ...` (receives `{event_id, name, payload}`;
   at-least-once delivery — the outbox tracks processed events per consumer).
2. Register it in the appropriate `register_*_consumers()` invoked in `app/jobs/scheduler.py`'s
   `if outbox_dispatcher_enabled():` block (dark-launched with the dispatcher).
3. Add/activate the `domain_event_subscriptions` row (registry metadata) so governance sees the
   consumer. Run `POST /events/governance/validate`.

## Monitoring

- **Awaiting delivery:** `domain_events_awaiting_delivery` (analytics) / `GET /events/diagnostics`
  `by_status.pending`. A growing pending count with the dispatcher ON indicates a stuck/slow consumer.
- **Dead letters:** `GET /events/dead-letters` and `domain_events_dead_lettered` (analytics) — an event
  that exhausted `MAX_ATTEMPTS` (5) after exponential backoff. Inspect the payload + `error`, fix the
  consumer, then re-dispatch via `GET /events/{event_id}/replay?deliver=true` (idempotent).
- **Producer health:** `GET /events/producers` — `active_producers` vs `stale_producers` (a registered
  producer with no publishing site is stale); `adoption_pct` should be 100%.
- **Publish failures:** `domain_event_publish_failures` (analytics) — an unregistered event type or a
  sensitive-field / contract violation was attempted and dropped by `publish_safe`. Investigate the
  producer; the business mutation was NOT affected.

## Governance checklist (should be 0 issues)

`GET /events/governance` must report `ok: true`: no unregistered/orphan contracts, no orphan
subscriptions, no producer-without-consumer, no producer-without-publishing-site, no unregistered
publish site, no schema/version drift, **no sensitive-field violation**, no duplicate semantic contract,
no deprecated contract still published.

## Deprecating an event

`registry.deprecate(event_type, reason=…)`, remove its publishing site(s), then
`registry.retire(event_type)`. Governance flags a deprecated contract that is still published
(`deprecated_contract_published`).

## Invariants (never violate)

The outbox is the sole bus + log (no second event table, no broker). Payloads are references-only (no
PII/secrets/financials/health/tax/document contents). Producers publish only through the standardized
publisher, after the mutation, without corrupting it. Consumers are never the system of record. RBAC is
never bypassed. Runtime is the sole evaluator; policy the sole decision engine; orchestration the
coordinator.
