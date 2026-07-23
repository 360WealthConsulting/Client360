# Event Authoring Guide (Phase D.34)

How to add or change a domain event. An event has two halves that governance keeps in sync: the
**pure-data seed** (`app/database/event_seed.py`, mirrored into the registry by an Alembic migration)
and the **executable contract** (built from the seed in `app/services/events/contracts.py`).

## 1. Design the contract

A domain event describes *something that happened*, past tense, as a **references-only** payload
(ids/codes) — **never PII or secrets** (the payload is stored in the outbox and delivered to consumers).
Choose:

- `event_type` — a dotted, stable routing key (e.g. `benefits.enrollment_completed`).
- `category` — the event domain.
- `producer` — the emitting subsystem (e.g. `benefits.domain`).
- `payload_schema` — `{field: type}` with primitive types (`int`/`str`/`float`/`bool`/`list`/`dict`).
- at least one `consumer` — an event nobody consumes is flagged by governance (`producer_without_consumer`).

Do **not** put business decisions or configuration reads in an event — routing/behavior stay in the
policy/runtime engines. An event is a fact; consumers decide what to do with it.

## 2. Add the seed entries (`app/database/event_seed.py`)

Append a contract tuple to `DOMAIN_EVENT_CONTRACTS_SEED` and at least one subscription tuple to
`DOMAIN_EVENT_SUBSCRIPTIONS_SEED`. Keep the payload schema references-only and the version supported by
`app/platform/events.py` (`SUPPORTED_VERSIONS`).

## 3. Seed the registry rows (a new Alembic migration)

Add a migration that inserts the contract + subscriptions into `domain_event_contracts` /
`domain_event_subscriptions` (mirror `zb1c2d3e4f5a`). Keep a **single Alembic head**. Governance
reconciles the registry rows against the in-code contracts — an orphan row or an unregistered contract
is flagged.

## 4. Publish from the producer

At the business action, publish through the standardized API — **keeping the capability check** at the
call site (publishing never bypasses RBAC):

```python
from app.services.events import publisher
publisher.publish("benefits.enrollment_completed",
                  {"enrollment_id": eid, "plan_id": pid, "status": "completed"},
                  conn=conn, producer="benefits.domain", subject_ref=f"enrollment:{eid}")
```

Pass `conn=` to commit the event atomically with the business change; use `publish_safe(...)` for an
additive, best-effort emission that must never break the caller. Do **not** write to a new event table —
the outbox is the log.

## 5. Subscribe a consumer

Add the consumer's `outbox.subscribe(event_type, handler)` to the appropriate `register_*_consumers()`
(invoked in the scheduler's gated outbox block, so it is dark-launched with the dispatcher). The handler
receives `{event_id, name, payload}` and **must be idempotent** (at-least-once delivery; the outbox
tracks processed events).

## 6. Validate + test

Run `governance.validate()` — it must report `ok: True` (no unregistered/orphan contracts, no orphan
subscriptions, no producer-without-consumer, no schema violation, no version drift). Add tests: a
`publisher.publish` happy path + a contract-violation rejection, the diagnostics/replay view, and the
governance/coverage state. Bump the route-count guard only if you added routes; keep the manifest +
platform architecture in sync.

## Deprecating an event

`registry.deprecate(event_type, reason=…)` then, once no producer/consumer references it,
`registry.retire(event_type)`. Governance flags any active contract that still depends on — or
subscription that still targets — a deprecated/retired event.
