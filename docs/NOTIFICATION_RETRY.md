# Client360 — Notification Retry Orchestration (F5.7 / Epic 5)

The retry **decision** layer, governed by
[ADR-017](architecture/adr/ADR-017-notifications-and-communications-architecture.md). F5.7
decides **whether** a still-`pending` notification is eligible for another delivery attempt
and, **relatively**, how long to wait — derived **exclusively** from the immutable F5.5
delivery-attempt history plus the notification's disposition. It is a pure, timeless,
read-only decision function.

`app/services/notification_retry.py` · no migration (reuses F5.1/F5.5)

## Responsibilities & boundaries
F5.7 inspects immutable delivery-attempt history, determines retry eligibility, computes a
**relative** retry delay, honors a retry **policy**, and identifies terminal exhaustion. It is
**not** responsible for dispatch (F5.5), initial-dispatch selection (F5.6), provider
communication, notification creation (F5.4), or eligibility/consent (F5.3). It introduces
**no** scheduler/cron/queue/lease/worker, **no** provider call, **no** lifecycle state, and
**no** retry counter or summary field on notification rows. It accepts **no** wall-clock
`now` and returns **no** absolute timestamp — a **future scheduler** combines the relative
`retry_delay` with time.

## Relative, timeless decision
The decision carries only **relative/ordinal** facts, never `next_attempt_at`. Combining
`last_attempt_completed_at + retry_delay <= now?` is the future scheduler's job, so F5.7 has
no time dependency and is fully deterministic: same `(history, policy)` → same decision.

## Delay indexing (unambiguous — no off-by-one)
Three distinct quantities:
- **`completed_attempts`** — count of immutable attempt rows (always *computed*, never stored on the notification).
- **`retry_ordinal`** — which retry this would be; `1` = first retry after the initial attempt.
- **`next_attempt_number`** — the delivery attempt that would follow; `= completed_attempts + 1`.

```
completed_attempts = 1  →  retry_ordinal = 1  →  next_attempt_number = 2  →  delay_for_retry(1)  (first retry)
completed_attempts = 2  →  retry_ordinal = 2  →  next_attempt_number = 3  →  delay_for_retry(2)
```

## Attempt-cap semantics
`max_attempts` = the maximum **total** provider delivery attempts, **including the initial
attempt**. With `max_attempts = 4`: attempt 1 (initial) + retries 1/2/3 (attempts 2/3/4);
after 4 completed transient attempts → **EXHAUSTED**. A policy therefore needs exactly
`max_attempts - 1` retry delays (ordinals `1 … max_attempts-1`), **validated at construction**.

## `RetryPolicy` (immutable, injectable, versioned)
A frozen value object; delay values live **only** here, never in the decision engine.
```python
@dataclass(frozen=True)
class RetryPolicy:
    policy_id: str = "default.v1"
    max_attempts: int = 4
    retry_delays: tuple[timedelta, ...] = (30s, 2m, 10m)   # exactly max_attempts-1 delays
    def delay_for_retry(self, retry_ordinal: int) -> timedelta   # 1 <= ordinal <= max_attempts-1
```
`__post_init__` validates `len(retry_delays) == max_attempts - 1` and non-negative delays.
`default_retry_policy()` returns the versioned default (`default.v1`); delay values are
provisional but confined to the policy and swappable without touching the engine.

## `RetryReason` (closed domain vocabulary)
`TERMINAL_DISPOSITION` · `NOT_APPLICABLE_NO_ATTEMPTS` · `NON_RETRYABLE_FAILURE` ·
`RETRYABLE_TRANSIENT` · `EXHAUSTED`. A closed enum — never free-form strings. Any
human-readable text is derived from the enum and stays content-free.

## `RetryDecision` (immutable, relative, content-free)
```python
@dataclass(frozen=True)
class RetryDecision:
    eligible: bool
    completed_attempts: int
    retry_ordinal: int | None         # None unless eligible
    next_attempt_number: int | None   # None unless eligible; == completed_attempts + 1
    max_attempts: int
    retry_delay: timedelta | None      # relative; None unless eligible
    reason: RetryReason
    policy_id: str
```
`to_dict()` serializes `retry_delay` as `retry_delay_seconds` and `reason` by value; no
timestamp, no recipient/title/body/contact/payload/error text.

## Decision model (evaluated in order)
```
1. terminal disposition (delivered/failed/suppressed/disabled/dead) → not eligible, TERMINAL_DISPOSITION
2. pending, completed_attempts == 0                                 → not eligible, NOT_APPLICABLE_NO_ATTEMPTS
3. pending, latest attempt retry_recommended != true               → not eligible, NON_RETRYABLE_FAILURE
4. pending, completed_attempts >= policy.max_attempts              → not eligible, EXHAUSTED
5. pending, transient latest & completed_attempts < max_attempts   → eligible, RETRYABLE_TRANSIENT
      retry_ordinal = completed_attempts; next_attempt_number = completed_attempts + 1;
      retry_delay   = policy.delay_for_retry(retry_ordinal)
```
Retryability derives from the **normalized immutable `retry_recommended`** field on the latest
attempt (F5.5) — never inferred from provider outcome text. In Model A a *hard* failure already
transitions the notification to terminal `failed` at dispatch, so a still-`pending`
notification's latest attempt is, by construction, a transient one; the `NON_RETRYABLE_FAILURE`
branch is a defensive guard.

## Interaction with F5.5 / F5.6
Read-only over F5.5's immutable `notification_delivery_attempts` + `notifications.status`; it
never calls `dispatch_notification`, writes attempts, or alters F5.5 normalization. It provides
the eligibility gate that F5.6's coarse cycle-local exclusion approximates today: a future
scheduler (owning wall-clock) or F5.6 can consult `evaluate_retry(...)` to decide whether a
`pending` notification is due for another attempt. F5.6 remains the only path to F5.5.

## Transaction boundaries
Read-only: each `evaluate_retry` runs a short read over the attempt history + notification
(caller `conn` or its own connection). No write transaction, no lock, no batch-rollback
coupling. **No migration** — all inputs already exist on the F5.5 attempt table (`attempt_seq`,
`execution_result`, `failure_class`, `retry_recommended`, `execution_completed_at`) and
`notifications.status`. Single head remains `f55d1s2p3t4c`.

## Metrics
Per notification: the `RetryDecision`. Aggregate: `summarize(decisions)` → content-free counts
`{inspected, retry_eligible, exhausted, not_retryable}`. No per-notification identifiers in the
summary.

## Architecture compliance
- **ADR-017 / Pure Ledger / Model A / immutable execution history / single source of truth:**
  read-only; every input is the immutable attempt history + disposition; writes nothing; adds
  no lifecycle state.
- **No mutable retry counter/summary on notifications:** `completed_attempts` is always computed
  from the attempt rows.
- **Intent/disposition separation:** reads disposition, decides timing; creates no intents,
  evaluates no eligibility/consent, dispatches nothing.

## Out of scope (later)
Scheduler/activation that fires the retry, dispatch, provider communication, backoff engines
beyond a fixed policy table, dead-letter transition writes, failover, batching, priority queues,
distributed workers/leasing, and any notification API/admin surface.

## References
ADR-013, ADR-017; `docs/NOTIFICATION_DISPATCH.md` (F5.5), `docs/NOTIFICATION_WORKER.md` (F5.6),
`docs/NOTIFICATIONS.md` (F5.1); `app/services/notification_dispatch.py`.
