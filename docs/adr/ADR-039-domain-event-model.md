# ADR-039 — Enterprise Domain Event Model: a typed, versioned, governed domain-event layer over the existing transactional outbox (no second event table)

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Events/Messaging); Reliability / Operations (governance);
Security / Authorization (RBAC ownership); Business Operations Owner (Michael Shelton — event-model
requirements). Authorized compliance reviewer: Not yet designated.

## Context
Through D.33 the platform gained configuration → runtime → policy → orchestration. The next missing
capability is a **standardized domain-event model** so every major business action can emit typed
domain events that other modules (timeline, notifications, automation, integrations, analytics, audit,
reporting, future plugins) consume asynchronously — without direct inter-module dependencies. This is
the natural successor to orchestration: workflows should *publish* domain events rather than directly
invoking every downstream service.

Critically, the platform **already has an internal event bus** — the transactional outbox
(`app/platform/outbox.py`): `subscribe`/`publish`/`publish_event`/`dispatch_pending`, at-least-once
delivery with idempotency (`outbox_processed_events`), exponential backoff, a dead-letter table
(`outbox_dead_letters`), and a versioned canonical `Envelope` (`app/platform/events.py`,
`schema_version` + `upgrade_envelope`). Only *workflow* and *runtime coordination* publish today. So the
genuine gap is not a bus — it is a **governed, typed contract model** that every producer adopts, plus
the registry / governance / diagnostics / analytics layer.

The architecture has a hard invariant every prior phase honored: *reuse, never duplicate; no second
event table; no message queue* (`PLATFORM_ARCHITECTURE.md §25`). Building a new parallel bus would
duplicate the outbox and break it.

## Decision
Phase D.34 introduces an **Enterprise Domain Event Model** (`app/services/events/`) — a typed,
versioned, governed domain-event layer **over the existing transactional outbox**. **The outbox remains
the sole internal event bus; the runtime engine remains the sole evaluator; the policy engine remains
the sole decision engine; RBAC remains the sole access authority.** No second event table is added: a
domain event is a contract-validated `Envelope` written to `outbox_events`.

- **Typed event contracts** (`contracts.py`, from a shared pure-data seed) — a contract declares the
  event type, category, producer, schema version, and a **references-only** payload schema (ids/codes
  only; never PII or secrets).
- **Standardized publishing service** (`publisher.py`) — the single publish entry point: validate
  against the contract → wrap in the versioned envelope → write to the outbox (atomically with the
  caller's transaction when a `conn` is supplied). A best-effort variant lets additive background call
  sites publish without risk.
- **Event registry** (`registry.py`, `domain_event_contracts` + `domain_event_subscriptions`) —
  discovery, versioning, lifecycle status, ownership + producer, the subscriber set per event, the
  dependency (causation) graph, deprecation tracking, and coverage.
- **Event governance** (`governance.py`) — unregistered/orphan contracts, orphan subscriptions,
  producers without consumers, schema violations, contract/version drift, deprecated references,
  invalid ownership → a governance report.
- **Event diagnostics + replay** (`diagnostics.py` / `replay.py`) — read-only inspection of the outbox
  log (per-type counts, delivery status, dead-letters, subscriber health, replay-readiness) and
  deterministic reconstruction of an event from the log (a pure read; an explicit, idempotent
  re-dispatch is capability-gated). Neither introduces a new transport.
- **Delivery guarantees / dead-letter / versioning are REUSED** from the outbox + envelope, not rebuilt.
- **Orchestration adopts the model** — the orchestration engine publishes an `orchestration.lifecycle`
  domain event on launch + terminal outcomes (additive, best-effort, dark-launched), so processes
  publish domain events rather than invoking downstream services directly.
- **Never bypasses RBAC/audit** — the `/events` surface reuses the D.26 `observability.*` capabilities
  (no new capabilities, no RBAC changes); the live outbox subscription for the new event is
  dark-launched in the scheduler's gated consumer block (behavior unchanged by default); only
  low-frequency admin/governance actions touch the audit hash-chain (never per published event).

## Alternatives considered
1. **Build a new standalone event bus / delivery / dead-letter.** Rejected: duplicates the transactional
   outbox and violates the "no second event table / no message queue" invariant.
2. **Publish from every business action now.** Deferred: D.34 establishes the model + governance and
   wires orchestration as the flagship producer; broadening publication to more domains is incremental
   and additive (each is behavior-preserving because the dispatcher is gated off by default).
3. **A durable message broker (Kafka/Rabbit).** Rejected: Client360 is a single-process SSR app with a
   proven in-DB transactional outbox; an external broker adds operational surface with no need.
4. **Store domain events in a new table.** Rejected: the outbox is the event log; a second event table
   is explicitly prohibited.

## Reasons for the decision
Domain events must be typed, versioned, discoverable, governed, and adopted by producers — without a
second bus or event table, without bypassing the runtime/policy engines or RBAC, and without changing
behavior. A governed contract model over the one outbox delivers this while preserving
ADR-013/033/036/037/038.

## Consequences
### Positive consequences
- 5 typed contracts across 3 domains (workflow, orchestration, runtime), each with a registered
  consumer (100% domain coverage, 100% consumer + producer coverage, 0 governance issues). Orchestration
  publishes domain events. The bus, delivery guarantees, idempotency, dead-letter, and versioning are
  reused, not duplicated. Analytics/observability expose the event flow; the "no event bus" doc line is
  reconciled (the outbox is a transactional outbox, not an external broker).

### Negative consequences and tradeoffs
- Only orchestration + the pre-existing workflow/runtime flows publish so far; broadening to more
  business actions is future incremental work.
- The live subscription for the new event is dark-launched (the dispatcher is off by default), so
  end-to-end delivery is exercised only when the outbox dispatcher is enabled — consistent with every
  other outbox consumer.

## Enforcement
- `app/services/events/{contracts,publisher,registry,subscriptions,governance,diagnostics,replay,
  common}.py`; the pure-data seed `app/database/event_seed.py`; migration
  `migrations/versions/zb1c2d3e4f5a_domain_events.py` (creates + seeds `domain_event_contracts` /
  `domain_event_subscriptions`); schema `app/database/event_tables.py` registered in `schema.py`;
  `db.py` exposes the tables. Orchestration adoption in `app/services/orchestration/engine.py`
  (`_publish_domain_event`). Dark-launched consumer wired in `app/jobs/scheduler.py`. Routes
  `app/routes/events.py` (`/events`, reusing `observability.*`). Analytics metrics
  (`sources.py`/`metrics.py`). Event modules registered in `source_producer_modules`. The outbox, the
  envelope, the runtime/policy/orchestration engines, RBAC, and infrastructure config are untouched.
  Tests: `tests/test_domain_events.py`; manifest / platform-architecture / route-count / ADR-count
  guards updated.

## Exceptions
The live subscription is dark-launched (gated with the outbox dispatcher). Broadening producer adoption
beyond orchestration is deferred, incremental, and additive. `administrator`/`record.read_all` scope
bypass remains as defined by ADR-004.

## Revisit conditions
Adopting an external broker, adding a second event log, broadening producer publication to more domains,
or evolving the envelope schema version would each warrant a new or superseding ADR.

## References
- `app/services/events/*`, `app/routes/events.py`, `app/database/event_tables.py`,
  `app/database/event_seed.py`, migration `migrations/versions/zb1c2d3e4f5a_domain_events.py`,
  `app/platform/outbox.py`, `app/platform/events.py`, `docs/DOMAIN_EVENT_MODEL.md`,
  `docs/EVENT_REGISTRY.md`, `docs/EVENT_GOVERNANCE_GUIDE.md`, `docs/EVENT_AUTHORING_GUIDE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_domain_events.py`; relates to ADR-013, ADR-033, ADR-036, ADR-037, ADR-038
