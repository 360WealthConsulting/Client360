# Client360 — Notification Dispatch Worker (F5.6 / Epic 5)

The **activation** layer, governed by
[ADR-017](architecture/adr/ADR-017-notifications-and-communications-architecture.md). F5.6 is
a **worker-only mechanism** that locates pending notifications and hands each, one at a time,
to the existing **F5.5** dispatcher. It adds **no** dispatch, provider, eligibility, or
lifecycle logic — every execution semantic (provider call, immutable delivery-attempt
recording, atomic disposition transition, idempotency) lives in F5.5 and is reused unchanged.

`app/services/notification_worker.py` · no migration (reuses F5.1/F5.5)

## Worker-only mechanism
The worker is a plain **callable** — `run_dispatch_cycle(...)`. It is invoked **explicitly**
(by a caller or a test). F5.6 registers **no** cron job, wires **no** APScheduler job, adds
**no** startup hook, and adds **no** recurring-activation flag. Turning the worker into a
scheduled/recurring job is a later, separate activation step.

## `claim_next_pending()` abstraction
The worker acquires work **only** through `claim_next_pending(attempted_ids)`, which returns an
immutable **`PendingNotificationClaim`** value object (or `None`) — the worker operates on a
**claim**, not an integer. The initial single-instance implementation returns a claim for the
**oldest `pending`** notification whose id is not in `attempted_ids`. It is **read-only** — it
takes no lease/lock and starts no write transaction, so it introduces no batch-rollback
coupling.

```python
PendingNotificationClaim(notification_id=..., notification_uid=..., created_at=...)
# worker flow:  claim_next_pending() -> PendingNotificationClaim -> dispatch_notification(claim.notification_id)
```

Today the claim carries only the notification references. The value object exists to keep the
worker interface stable while a future scalable claim is swapped in behind it.

### Future claim implementations (documented, NOT implemented in F5.6)
A multi-worker deployment would replace **only** `claim_next_pending` — extending
`PendingNotificationClaim` with fields such as a lease token, lease expiration, claim
timestamp, queue partition, worker ownership, or priority — backed by a durable **lease**
column, `SELECT ... FOR UPDATE SKIP LOCKED`, a Postgres **advisory lock**, a **distributed
lock**, or a **queue-backed** claim. The worker loop, metrics, and F5.5 interaction stay
identical. None of those fields or mechanisms exist today.

## Single-instance assumption
The worker runs **single-instance**, matching the outbox dispatcher and F5.5's documented
assumption. F5.5 invokes the provider **before** its DB claim, so true multi-worker
concurrency could double-*send* (the database is protected regardless by the unique
`(notification_id, attempt_seq)` constraint and the conditional pending-only transition). One
active worker avoids duplicate external sends.

## Cycle-local attempted-id exclusion (no same-cycle transient retry)
A transient provider failure leaves the notification **`pending`** (F5.5, Model A). To avoid
re-dispatching the *same* notification repeatedly within one cycle, the worker keeps an
**in-memory** set of ids already attempted this cycle and passes it to `claim_next_pending`,
which excludes them. This is **cycle-local only** — it is **not** retry scheduling, backoff,
permanent suppression, a database lease, or durable state. When the cycle ends the set is
discarded; a **later** cycle (fresh set) may encounter the still-`pending` notification again
and attempt it once more. F5.6 performs **no** retry engine, backoff, dead-letter, failover,
or rate limiting.

## Worker flow (one cycle)
1. Cooperative **stop** check (before claiming — never mid-dispatch).
2. **Cycle-limit** check (`scanned >= cycle_limit` → stop).
3. **Claim** the next pending id via `claim_next_pending(attempted)` (timed → `poll_latency_ms`).
4. `None` → queue drained → end cycle.
5. Mark the id **attempted** (cycle-local) and dispatch it via F5.5 `dispatch_notification`.
6. Classify the `DispatchResult.outcome` into the metric counters; a worker-level exception is
   caught, counted, logged content-free, and the loop continues.
7. Repeat. On exit, compute `runtime_ms` and `idle`.

## Interaction with F5.5 transaction boundaries
F5.6 opens **no** transaction of its own for dispatch. Each notification is dispatched through
`dispatch_notification(notification_id=…)`, whose attempt-insert + disposition-update are
atomic **inside F5.5's own transaction** — one notification = one transaction. The worker
deliberately does **not** wrap multiple notifications in a shared transaction, so one item's
failure never rolls back another's work. The `claim_next_pending` lookup is a separate,
read-only query and introduces no rollback coupling.

## Graceful shutdown
A cooperative `stop` predicate is checked **between** notifications, never mid-dispatch. When
a stop is requested the current (in-flight) F5.5 dispatch — already atomic — finishes, the
worker claims **no** further notification, and the cycle returns with `stopped = true`.

## Error handling
A failure processing one notification never aborts the cycle. Per notification the worker:
catches unexpected worker-level errors, increments `worker_errors`, logs only content-free
identifiers (the integer `notification_id`) and an error **classification** (exception class
name), marks the id attempted for the cycle, and continues to the next eligible pending
notification. F5.5's provider-error normalization is **not** altered (provider failures never
surface as worker errors — F5.5 returns them as structured `failed`/`provider_unavailable`
outcomes).

## Metrics (structured, content-free)
`DispatchCycleMetrics.to_dict()` returns counts/timings only — never recipient info,
subject/body, contact details, provider payloads, or client-bearing exception messages.

| Metric | Definition |
|---|---|
| `scanned` | notifications claimed and processed this cycle (`== dispatched + worker_errors`) |
| `dispatched` | notifications handed to F5.5 that returned a result (did not raise at the worker level) |
| `delivered` | dispatched with a terminal `delivered` disposition |
| `failed` | dispatched with a terminal `failed` disposition |
| `transient_failures` | dispatched with a transient provider outcome (left `pending`) |
| `rejected` | dispatched but rejected by F5.5 (not pending / no provider) — ~0 in steady state |
| `worker_errors` | claims whose dispatch raised an unexpected worker-level error (caught, non-fatal) |
| `runtime_ms` | total cycle wall-clock, milliseconds |
| `poll_latency_ms` | cumulative time in `claim_next_pending` this cycle, milliseconds |
| `idle` | true iff `scanned == 0` with no stop/limit termination (empty queue) |
| `stopped` | true iff the cycle ended due to a cooperative stop request |
| `cycle_limit_reached` | true iff the cycle ended because the configured cycle limit was reached |

## At-least-once external delivery limitation
Because F5.5 calls the provider before its DB commit, a crash after a successful external send
but before commit leaves the notification `pending`, so a later cycle re-dispatches it →
**duplicate external delivery is possible** (at-least-once). Single-instance operation bounds
this; the currently enabled channel (`in_app`) has no external duplicate cost. Provider-side
idempotency keys (a future retry/dispatch-worker concern) are the durable defense.

## Architecture compliance
- **ADR-017 / Pure Ledger / Model A:** the worker writes **nothing** to the ledger; all writes
  flow through F5.5, which writes only durable disposition changes and never mutates a row on
  a transient failure.
- **Intent/disposition separation:** the worker consumes disposition (`pending`) and delegates;
  it creates no intents, evaluates no eligibility, and adds no statuses/transitions.
- **Immutable execution history:** attempt rows are written solely by F5.5 into the append-only,
  trigger-protected table.
- **Existing lifecycle unchanged:** `pending → delivered | failed`; transient stays `pending`.
  No migration; single head `f55d1s2p3t4c`.

## Out of scope (later phases)
Retry engine, exponential backoff, dead-letter processing, provider failover, rate limiting,
batching beyond a bounded single cycle, recurring schedules / cron / APScheduler wiring,
priority queues, distributed locking, external queue infrastructure, and any notification
API/admin surface (F5.7).

## References
ADR-013, ADR-017; `docs/NOTIFICATION_DISPATCH.md` (F5.5), `docs/NOTIFICATIONS.md` (F5.1),
`docs/NOTIFICATION_PROVIDERS.md` (F5.2); `app/services/notification_dispatch.py`.
