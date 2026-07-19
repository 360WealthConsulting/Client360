# Client360 — Ready Notification Claim (F5.9 / Epic 5)

The worker-support layer that returns the next notification currently **ready** for the F5.6
worker (`PendingNotificationClaim`) or `None`, governed by
[ADR-017](architecture/adr/ADR-017-notifications-and-communications-architecture.md). It closes
the F5.8 operational-safety gap: with `claim_next_ready` as F5.6's default claim, the worker
only receives due work, so a recurring activation cannot re-attempt a transiently-failed
(still-`pending`) notification before its retry delay has elapsed.

`app/services/notification_ready.py` · claim contract in `app/services/notification_claims.py` · no migration

## Neutral claim contract (acyclic dependencies)
`PendingNotificationClaim` now lives in **`app/services/notification_claims.py`** — a neutral
module owned by neither F5.6 nor F5.9 (stdlib-only). Both import the DTO from there. F5.6
re-exports it (`from app.services.notification_worker import PendingNotificationClaim` still
resolves to the *same* class object), so existing callers/tests are unaffected. **F5.9 does not
import the F5.6 worker module**, so F5.6 can use a **normal top-level import** of
`claim_next_ready` — no lazy import, no cycle.

### Dependency direction
```
notification_claims (stdlib only)
   ▲                        ▲
F5.6 notification_worker    F5.9 notification_ready
   │  └── imports claim_next_ready ──►  F5.9
   │                                     ├── F5.7 notification_retry (evaluate_retry)
   │                                     ├── F5.5 notification_dispatch (delivery_attempts)
   └── (dispatch, registry, ledger)      └── F5.1 notifications (ledger)
```
Acyclic; F5.9 depends on F5.7/F5.5/F5.1/claims, never on F5.6.

## Three collaborators (never one large function)
- **`CandidateRepository`** — candidate retrieval + latest-attempt-timestamp retrieval over one
  short-lived read connection; deterministic order; bounded; attempted-ID exclusion; read-only.
- **`ReadinessEvaluator`** — evaluates one candidate: consults **F5.7** for the retry decision,
  distinguishes initial-dispatch from retry, applies the inclusive timing rule, fails closed on
  invalid/missing timing. No writes.
- **`claim_next_ready`** — orchestrates the two (`_select_ready` performs the bounded scan),
  returns the first ready `PendingNotificationClaim` or `None`, emits content-free diagnostics
  once per call. One claim per call.

## Public interface
```python
def claim_next_ready(attempted_ids=frozenset(), *, now=None,
                     scan_limit=100, observe=None) -> PendingNotificationClaim | None
```
No `conn`/transaction/repository plumbing is exposed. `scan_limit` is validated (positive int;
`0`, negative, `bool`, and non-int are rejected with `ValueError`). Call-compatible with F5.6's
`claim(attempted)`.

## Readiness rules
- **Zero attempts** (F5.7 `NOT_APPLICABLE_NO_ATTEMPTS`) → **ready now** (initial dispatch; no
  timing math).
- **Retryable transient** (F5.7 `RETRYABLE_TRANSIENT`) → **ready iff** `latest
  execution_completed_at + retry_delay <= now` (**inclusive**). Not yet due → skip. Missing/naive
  timestamp → **fail closed** (not ready).
- **Ineligible** (`TERMINAL_DISPOSITION` / `EXHAUSTED` / `NON_RETRYABLE_FAILURE`) → skip; **no
  mutation** of the notification.
- **Evaluation error** (F5.7 raises / attempt-read fails) → **fail closed**, skip, content-free log.

## Head-of-line prevention & scan bound
The scan continues past not-ready candidates to the first ready one — an early not-due row never
makes the queue look idle. At most `scan_limit` candidates are inspected per call. No ready
candidate within the bound → **`None`** (single return type); diagnostics carry
`scan_bound_reached = True` when the full bound was consumed, `False` when the repository was
exhausted first. No batching; one claim per call.

## Clock & timestamp semantics
F5.9 owns the wall clock: `now` is injected (aware UTC) for deterministic tests, or resolved
**once at the boundary** (`datetime.now(UTC)`) and threaded through — never called inside the
per-candidate loop. A **naive `now` is rejected**. Stored `execution_completed_at` is aware UTC;
a naive/None value is invalid → fail closed. Boundary equality (`due == now`) is **ready**.

## Attempted-ID semantics
F5.6's cycle-local `attempted` set is passed as `exclude`. Candidates F5.9 *skips* are not
added to `attempted` (only claimed ids are), so a later cycle re-evaluates them once due; within
a cycle the not-ready prefix may be re-scanned on later calls, bounded by `scan_limit`
(single-instance-acceptable).

## Diagnostics
Content-free `ClaimDiagnostics` via an injected `observe` callback (default no-op; F5.6 passes
none), emitted once per call: `candidates_inspected`, `zero_attempt_ready`, `retry_ready`,
`retry_not_due`, `retry_ineligible`, `missing_attempt_timestamp`, `evaluation_errors`,
`scan_bound_reached`, `claim_returned`, `no_ready_claim`. Never recipient/destination/title/
body/payload/provider-text/notification-bearing exception messages. `PendingNotificationClaim`
is unchanged.

## Failure handling
Candidate-specific failures (F5.7 raise, malformed/naive timestamp, missing attempt row) fail
closed and scanning continues (content-free logs). Repository-level candidate-page retrieval
failure **propagates** through F5.6's existing claim path (F5.8 represents it as an
activation-level failure). No infrastructure error is silently swallowed.

## F5.6 integration
F5.6 is modified **only** to: source `PendingNotificationClaim` from the neutral module (with
compatibility re-export) and use `claim_next_ready` as its default claim (normal import). The
worker loop, one-claim-at-a-time behavior, attempted-ID exclusion, cooperative stop, per-item
isolation, dispatch delegation to F5.5, cycle metrics, and injected-custom-claim behavior are
all preserved.

## Read-only & concurrency
Read-only: no status/attempt/counter writes, no lease/lock, no transaction spanning dispatch, no
lifecycle states. Single-instance (no locks/leases/`SKIP LOCKED`/queues); concurrent workers
could claim the same ready notification → possible double dispatch (the F5.5/F5.6 at-least-once
caveat). Multi-worker support is a later explicit feature. **No migration** (reuses
`notifications.status` + `delivery_attempts.execution_completed_at`/`retry_recommended`); single
head `f55d1s2p3t4c`.

## References
ADR-013, ADR-017; `docs/NOTIFICATION_WORKER.md` (F5.6), `docs/NOTIFICATION_RETRY.md` (F5.7),
`docs/NOTIFICATION_DISPATCH.md` (F5.5), `docs/NOTIFICATION_ACTIVATION.md` (F5.8).
