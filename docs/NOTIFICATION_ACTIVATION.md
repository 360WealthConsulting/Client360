# Client360 — Notification Activation Layer (F5.8 / Epic 5)

The **activation** entry point, governed by
[ADR-017](architecture/adr/ADR-017-notifications-and-communications-architecture.md). F5.8 is
a stateless tick handler that invokes an **injected worker callable exactly once** and returns
an activation-level outcome:

```
activation occurred  →  invoke the worker exactly once  →  return activation-level outcome
```

`app/services/notification_activation.py` · no migration

## Scope — activation only
F5.8 owns **only** the act of invoking the worker and reporting an activation-level result. It
does **not**: select notifications, evaluate retry eligibility, read the ledger or
delivery-attempt history, perform retry/due-time arithmetic, construct claims, call the
retry-decision layer (F5.7), or access any database. It holds **no** state between activations
and keeps **no** wall-clock — it only times the invocation and reports worker success/failure.

## Worker contract — a generic injected callable
The worker is a **generic injected callable** `Callable[[], Any]` returning an opaque result.
F5.8 is deliberately **independent of any specific worker implementation** — the caller injects
the worker — so the activation mechanism is reusable and F5.8 imports no worker module.

```python
from app.services.notification_activation import activate
result = activate(worker=my_worker)          # my_worker() -> <opaque result>
```

The worker's return value is carried through **opaquely** in `ActivationResult.worker_result`;
F5.8 never reads or reinterprets it (its `to_dict()` delegates to the worker result's own
`to_dict()` if present, never re-deriving fields).

## `ActivationResult` (immutable, activation-level only)
```python
@dataclass(frozen=True)
class ActivationResult:
    started: bool
    completed: bool
    cancelled: bool
    worker_invoked: bool
    worker_ok: bool
    runtime_ms: float
    error_class: str | None      # content-free classification of an activation-level failure
    worker_result: Any           # opaque nested worker result; not inspected
```
Activation-level facts only. Dispatch, retry-decision, candidate, due/not-due, and
notification-outcome metrics are **not** here — each stays with its owning layer
(F5.5 / F5.6 / F5.7 / future F5.9).

## Activation model & failure handling
`activate(*, worker, stop=None)`:
1. If `stop` is provided and already requests a stop → return a **cancelled** outcome; the
   worker is not invoked.
2. Otherwise invoke `worker()` **exactly once**, timing it.
3. Success → `worker_ok=True`, opaque `worker_result` carried.
4. Worker raised → caught as an **activation-level failure** (`worker_ok=False`, `error_class`
   set, content-free log) **without** inspecting notifications.

F5.8 handles only activation-level failures (worker invocation raised, cannot start, driver
cancellation). Notification-, claim-, and dispatch-level failures remain owned by the worker
and the layers below it. F5.8 is stateless and safe to re-run.

## Layering
```
F5.8 Activation  →  F5.6 Worker  →  (future F5.9 ready-claim)  →  F5.5 Dispatch
                                         └── F5.7 retry decision + timing gate
```
F5.8 invokes the worker; the worker requests work and dispatches. F5.7 remains the
retry-decision authority; F5.6 the worker authority; F5.5 the dispatch authority. F5.8 modifies
none of them and imports none of them.

## Operational safety — recurring activation is BLOCKED pending F5.9
The `activate()` entry point may be invoked **manually and in tests** now. **No recurring
production driver may be enabled until the future F5.9 "Ready Notification Claim" feature is
complete.** Today's F5.6 worker claim (`claim_next_pending`) does **not** enforce retry-delay
timing, so a recurring activation would let the worker re-attempt transiently-failed
(still-`pending`) notifications immediately across cycles, ignoring `RetryDecision.retry_delay`.
F5.8 ships the activation mechanism, **not** a production schedule.

## Future work
- **F5.9 — Ready Notification Claim** (worker-layer): a `claim_next_ready(...)` abstraction that
  owns pending zero-attempt selection, F5.7 consultation for previously-attempted notifications,
  latest immutable attempt-timestamp retrieval, combining it with `RetryDecision.retry_delay`
  and wall-clock, returning only currently-ready claims, and updating F5.6 to use it. Only after
  F5.9 is a recurring driver safe to enable.
- **External deployment drivers** (out of scope, unimplemented): in-process interval loop,
  APScheduler job, cron → management command, serverless/timer trigger, K8s CronJob. Each is a
  thin adapter that calls `activate(worker=...)`; horizontal scaling additionally needs a
  claim-layer lease/SKIP LOCKED/advisory lock (F5.9+).

## Architecture compliance
- **ADR-017 / Pure Ledger / Model A / immutable execution history:** F5.8 writes nothing, reads
  nothing, and adds no lifecycle state.
- **Single responsibility / stateless:** invoke the worker once, report the outcome; no carried
  state, no clock, no DB.
- **Intent/disposition separation preserved:** F5.8 makes no notification-specific decision.

## Migration
None. Single Alembic head remains `f55d1s2p3t4c`.

## References
ADR-013, ADR-017; `docs/NOTIFICATION_WORKER.md` (F5.6), `docs/NOTIFICATION_RETRY.md` (F5.7),
`docs/NOTIFICATION_DISPATCH.md` (F5.5).
