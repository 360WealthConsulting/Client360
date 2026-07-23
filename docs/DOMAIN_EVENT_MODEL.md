# Enterprise Domain Event Model (Phase D.34)

The **Enterprise Domain Event Model** (`app/services/events/`) lets every major business action emit
typed, versioned domain events that other modules consume asynchronously — without direct inter-module
dependencies. It **reuses the existing transactional outbox** as the internal event bus and adds **no
second event table**: a domain event is a contract-validated `Envelope` written to `outbox_events`.

## Architecture (reuse, never duplicate)

```
   Producers (orchestration engine, workflow, runtime coordination, …)
        │  publisher.publish(event_type, payload)   ← validate against the typed contract
        ▼
   Canonical Envelope (app/platform/events.py — schema_version + upgrade_envelope)
        ▼
   Transactional outbox (app/platform/outbox.py)   ← the ONE bus: atomicity, at-least-once,
        │                                              idempotency, backoff, dead-letter
        ▼
   Consumers (notification intents, workflow automation, runtime workers, observability sink, …)
```

The bus, delivery guarantees, idempotency (`outbox_processed_events`), dead-letter
(`outbox_dead_letters`), and envelope versioning **already exist** — D.34 adds the typed contract model,
the registry, governance, diagnostics, and producer adoption on top.

## Components

| File | Responsibility |
|---|---|
| `contracts.py` | The executable typed-contract catalog (from the shared pure-data seed). A contract = event type + category + producer + schema version + a references-only payload schema. |
| `publisher.py` | The standardized publish API — validate against the contract → versioned envelope → outbox (atomic with the caller's `conn` when supplied). |
| `registry.py` | Contract + subscription discovery / versioning / lifecycle / coverage. |
| `subscriptions.py` | The durable subscription registry + the dark-launched live consumer (observability sink). |
| `governance.py` | Read-only validation of the event model → a governance report. |
| `diagnostics.py` | Read-only event-flow inspection (per-type counts, delivery status, dead-letters, subscriber health, replay readiness). |
| `replay.py` | Deterministic reconstruction from the outbox log (pure read) + an explicit idempotent re-dispatch. |

## The envelope contract

Every event is a canonical `Envelope`: `event_type`, `payload` (references only — no PII/secrets),
`event_id` (idempotency key), `schema_version` (+ `upgrade_envelope` for backward-compatible evolution),
`occurred_at`, `correlation_id` / `causation_id`, `subject_ref`, `producer`, `metadata`. The publisher
validates the payload against the typed contract before writing.

## Persistence (no second event table)

- `outbox_events` / `outbox_dead_letters` / `outbox_processed_events` — **the event log** (existing;
  reused).
- `domain_event_contracts` — the typed contract registry (metadata only).
- `domain_event_subscriptions` — the durable subscription registry (metadata only).

## Seeded contracts

| Event type | Category | Producer | Consumer |
|---|---|---|---|
| `workflow.transition` | workflow | `workflow.execution` | `notification.dispatch` |
| `workflow.approval` | workflow | `workflow.approvals` | `notification.dispatch` |
| `workflow.sla` | workflow | `workflow.sla` | `workflow.automation` |
| `orchestration.lifecycle` | orchestration | `orchestration.engine` | `observability.sink` |
| `runtime.coordination` | runtime | `runtime.coordination` | `runtime.worker` |

The workflow.* and runtime.coordination contracts formalize event flows that already exist;
`orchestration.lifecycle` is the new D.34 event the orchestration engine publishes on launch + terminal
outcomes (additive, best-effort, dark-launched).

## Publishing

```python
from app.services.events import publisher
publisher.publish("orchestration.lifecycle",
                  {"instance_id": 42, "definition": "automation.dispatch",
                   "event": "completed", "stage": "completed"},
                  conn=conn, producer="orchestration.engine", subject_ref="orchestration:42")
```

Pass `conn=` for atomicity with the business change; use `publish_safe(...)` for additive background
call sites. The capability check stays at the call site — publishing never bypasses RBAC.

## Delivery, replay, dead-letter

Delivery, at-least-once semantics, idempotency, backoff, and dead-lettering are the outbox's — unchanged.
`diagnostics` reads the outbox log; `replay` reconstructs an event deterministically (pure read) and can
re-dispatch idempotently. The live subscription is dark-launched with the outbox dispatcher (off by
default), so runtime behavior is unchanged until enabled.
