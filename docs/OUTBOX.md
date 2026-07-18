# Client360 — Transactional Outbox & Dispatcher (E1.6 / Backlog F1.3)

A domain-agnostic primitive for **reliable event publication**. Added in-place
(ADR-013) as additive infrastructure: it introduces new tables and a small
platform module, changes no existing behavior, and is **OFF by default**.

## What it is (and isn't)
- **Is:** a low-level delivery mechanism — write an event *in the producer's
  transaction*, then a dispatcher delivers it asynchronously, at-least-once, with
  idempotency, exponential backoff, and a dead-letter table.
- **Is not:** a domain event log or a rules engine. It **complements** the
  existing `workflow_events` (domain audit) and `automation_triggers` /
  `automation_actions` (rules) — it does not replace them (reconciliation per
  ADR-013).

## Components
- **Tables** (`app/database/outbox_tables.py`, migration `e1m2o3x4b5t6`):
  - `outbox_events` — `event_id` (uuid), `name`, `payload` (json), `status`
    (`pending`→`dispatched`|`dead`), `attempts`, `available_at` (backoff),
    `last_error`, timestamps. Indexed on `(status, available_at)`.
  - `outbox_dead_letters` — events that exhausted `MAX_ATTEMPTS`.
  - `outbox_processed_events` — idempotency ledger, PK `(event_id, consumer)`.
- **Code** (`app/platform/outbox.py`):
  - `publish(conn, name, payload) -> event_id` — writes using the **caller's
    connection**, so the event commits atomically with the business change.
  - `subscribe(name, handler)` / `clear_subscribers()` — in-process handler
    registry. A handler is `Callable[[event_view], None]` and must be idempotent.
  - `dispatch_pending(...) -> summary` — polls due `pending` events and delivers
    them; on handler failure, backs off and retries; after `MAX_ATTEMPTS`,
    dead-letters. Each consumer's success is recorded so retries don't replay it.
- **Scheduler** (`app/jobs/scheduler.py`): `run_outbox_dispatch()` is registered
  as an APScheduler interval job **only when enabled** (reuses the existing
  scheduler rather than adding a new background mechanism).

## Guarantees
- **Atomicity:** an event exists iff the producer's transaction commits.
- **At-least-once delivery:** delivery is retried until success or dead-letter.
- **Idempotency:** consumers are keyed in `outbox_processed_events`; a handler is
  never re-run for an event it already processed. Handlers must still be written
  idempotently.
- **Backoff:** `available_at` advances by `BACKOFF_BASE_SECONDS · 2^(attempts-1)`.
- **Dead-letter:** after `MAX_ATTEMPTS` (5), the event is copied to
  `outbox_dead_letters` and marked `dead` (operator-visible, never silently lost).

## Configuration
| Variable | Default | Effect |
|---|---|---|
| `OUTBOX_DISPATCHER_ENABLED` | `false` | Register the dispatcher scheduler job |
| `OUTBOX_DISPATCH_INTERVAL_SECONDS` | `30` (min 5) | Poll cadence |

**Default OFF:** the mechanism ships and is fully tested, but nothing publishes
events yet, so enabling the dispatcher changes nothing until producers and
subscribers are added by later backlog items.

## Usage (for future producers/consumers)
```python
from app.platform import publish, subscribe

# Producer — inside an existing transaction:
with engine.begin() as conn:
    ...  # business writes
    publish(conn, "AccountFunded", {"account_id": account_id})   # never put PII in payload

# Consumer — idempotent handler:
def on_account_funded(event: dict) -> None:
    ...  # event = {"event_id", "name", "payload"}
subscribe("AccountFunded", on_account_funded)
```
> Payloads carry references, never secrets/PII/return data (Constitution §9).

## Scope boundary
F1.3 delivers the **mechanism** only. The canonical **event envelope + schema
versioning** is Backlog **F1.4**, and the **workflow template registry / event
producers** are **F1.5+** — not implemented here.

## Operations
- Inspect backlog: `SELECT status, count(*) FROM outbox_events GROUP BY status;`
- Dead letters: `SELECT * FROM outbox_dead_letters ORDER BY failed_at DESC;`
- Re-drive a dead letter: reset an `outbox_events` row to `status='pending',
  attempts=0, available_at=now()` after fixing the handler (manual, deliberate).
