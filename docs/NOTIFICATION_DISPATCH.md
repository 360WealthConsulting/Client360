# Client360 — Notification Dispatch & Delivery Attempts (F5.5 / Epic 5)

The canonical **execution** layer, governed by
[ADR-017](architecture/adr/ADR-017-notifications-and-communications-architecture.md). F5.5
consumes eligible **pending** notification intents (created by F5.4) and performs provider
dispatch through the **F5.2** provider registry, recording an **immutable, append-only**
delivery attempt for each try. It is responsible **only** for dispatch execution and
delivery-attempt recording.

`app/services/notification_dispatch.py` · migration `f55d1s2p3t4c`

## Architectural model (pure-ledger, Model A — normative)
- **The notification ledger is the durable communication-disposition record.** Notification
  `status` answers "what became of this communication decision?" (pending, delivered, failed,
  suppressed, disabled, dead). The row changes **only** when that disposition changes.
- **Delivery-attempt history is the authoritative execution history** — one immutable row per
  provider dispatch try; it owns provider outcome, normalized execution result, failure
  classification, retry recommendation, provider message id, execution timestamps, and the
  correlation/causation references.
- **Provider failures are attempt-scoped events**, recorded only in delivery-attempt rows.
- **Transient provider failures never become notification lifecycle state.** A transient
  provider condition leaves the notification **`pending`** and **does not write the
  notification row at all** — no status, no timestamps, and no execution-summary fields
  (`attempts`/`last_error`/`updated_at`). Only an attempt row is appended. The notification
  `status` vocabulary and CHECK are **unchanged from F5.4**.
- **Execution summaries are derived from delivery-attempt history, not stored on the
  notification row.** Attempt count, last error, and execution timing are read from
  `notification_delivery_attempts` (the authoritative, immutable source); the ledger keeps no
  denormalized copy. The pre-provisioned `notifications.attempts`/`last_error` columns are
  **not maintained** by dispatch.

## Core architectural rule
The notification intent remains the authoritative communication decision; F5.5 performs
**execution only**. Dispatch success or failure **never** completes a workflow, satisfies a
regulatory obligation, changes domain/evidence state, mutates the originating business
event, or infers business completion. Execution is strictly downstream of the intent.

## Existing-system reconciliation
| Source | Classification | F5.5 use |
|---|---|---|
| F5.1 ledger `notifications` | **Authoritative** for intent + durable disposition | Read pending; transition only on disposition change |
| F5.2 provider registry | **Authoritative** provider abstraction / honest outcomes | Invoked for delivery; outcomes normalized |
| F5.3 decision layer | **Authoritative** for eligibility | **Not** re-run — eligibility already decided at intent time |
| F5.4 intent creation | **Authoritative** for intents | **Not** invoked — F5.5 never creates intents |
| Outbox dispatcher (F1.3) | execution pattern reference | Same single-instance, post-commit philosophy |
| Portal/benefits/exception-SLA legacy notify paths | **Legacy / advisory** | Unchanged, untouched |

- **Authoritative execution source:** the F5.1 ledger `status` (`pending` is the only
  dispatchable state).
- **Approved dispatch path:** F5.5 dispatch service → F5.2 provider → immutable attempt +
  ledger transition.
- **Provider lifecycle:** owned by F5.2 (enabled/disabled/ready); F5.5 adds no
  provider-specific logic and enables no external channel.
- **Retry ownership:** F5.5 records retry **metadata** only; retry *execution/scheduling* is
  deferred (F5.6+).
- **Delivery-attempt ownership:** F5.5 (this feature) — the new
  `notification_delivery_attempts` table.
- **Deferred (F5.6+):** notification audit/evidence, reporting/compliance surfaces,
  API/admin, scheduled retry execution, escalation.

## Dispatch policy
Dispatch **only** intents whose ledger status is `pending`. Never dispatch `suppressed`,
`disabled`, `delivered`, `failed`, or `dead`. A non-`pending` intent is **rejected without
any provider invocation**. After a **transient** provider failure the notification is left
`pending` (see below), so a later dispatch naturally re-attempts it — retry *timing/scheduling*
is a future feature, outside F5.5.

## Status transitions (Model A — the only ones introduced)
```
pending → delivered | failed
```
There is **no** `provider_unavailable` (or any other transient) notification status. A
transient provider outcome appends a delivery attempt (with `retry_recommended`) and leaves
the notification **`pending`**; only a terminal outcome (`delivered` / `failed`) transitions
it. The migration therefore makes **no** change to the `notifications.status` CHECK.

## Provider integration & normalization
F5.5 reuses the canonical F5.2 registry (`default_registry()`), calls
`provider.deliver_result(recipient, title, body, metadata=references)`, and normalizes the
returned `DeliveryResult` into a canonical execution result — no provider-specific branching.
The **execution_result** is recorded on the immutable delivery attempt; the **notification
status** changes only on a terminal outcome:

| F5.2 `DeliveryResult` | attempt execution_result | notification status | retry_recommended |
|---|---|---|---|
| `delivered` | `delivered` | → `delivered` | false |
| `failed`, `failure_class=provider_unavailable` | `provider_unavailable` | **stays `pending`** | **true** |
| `failed`, `failure_class=provider_error` | `failed` | → `failed` | false |
| `disabled` (`provider_not_configured`) | `failed` | → `failed` | false |

Transient execution outcomes (provider unavailable, timeout, DNS failure, network
interruption, HTTP 429/503, connection reset, temporary auth failure) are all represented
this way — as attempt-scoped `execution_result`/`failure_class` values that leave the
notification `pending`. The intent's `title` is the F5.4 **template reference** (not rendered
content) and `body` is `None`; F5.5 passes them through unchanged and copies no content.

## Delivery-attempt model (immutable, append-only)
`notification_delivery_attempts` — one row per dispatch attempt; an immutability trigger
blocks `UPDATE`/`DELETE` (same pattern as `audit_events`/`evidence`). Columns (references +
content-free outcome only): `attempt_uid` (unique), `notification_id`, `notification_uid`,
`attempt_seq`, `provider`, `channel`, `execution_started_at`, `execution_completed_at`,
`provider_message_id`, `provider_status`, `execution_result`, `retry_recommended`,
`failure_class`, `correlation_ref`, `causation_ref`, `attempt_metadata` (references),
`created_at`. Unique `(notification_id, attempt_seq)` prevents a duplicate attempt row.
**No** notification `title`/`body` or contact data is stored. This table is the **sole
authoritative execution history** — every dispatch attempt (delivered, terminal failure, or
transient failure) records exactly one row here; execution summaries (attempt count, last
error, timing) are **derived from these rows**, never mirrored onto the notification. It is
never authoritative for workflow/domain/business-event/evidence/eligibility state.

## Retry boundary
Retry **eligibility** is determined from delivery-attempt history: F5.5 sets
`retry_recommended` on the attempt (true for a transient/`provider_unavailable` execution
result) and persists that metadata **only** on the attempt row. It **does not** schedule,
execute, or enqueue retries, and it introduces **no** notification-status lifecycle for
retry — a transiently-failed notification simply stays `pending`. Retry timing/scheduling is
a future feature outside F5.5.

## Transaction boundary
`dispatch_notification(...)` appends the attempt and, **only on a terminal outcome**, updates
the notification's disposition — in a single unit of work (accepts a caller `conn`, else opens
one), so they commit or roll back together (test-verified). On a **transient** outcome the
notification row is not written at all (only the attempt is appended). The disposition update
is a **conditional** `UPDATE ... WHERE status='pending'`, so a repeated or racing dispatch
cannot double-transition a terminal intent. Dispatch is designed to run **single-instance**
(like the outbox dispatcher); the append-only `(notification_id, attempt_seq)` unique
constraint is the duplicate-attempt backstop. F5.5 adds **no** scheduler job —
activation/periodic execution is a separate, later concern (mechanism, not activation).

## F5.1 / F5.2 / F5.3 / F5.4 boundaries
Reads F5.1 pending intents and transitions their status **only** to a terminal disposition
(`delivered`/`failed`) — the notification status vocabulary is unchanged from F5.4; invokes
F5.2 providers and normalizes their honest outcomes; **does not** re-run F5.3
eligibility/consent, create F5.4 intents, promote suppressed/disabled intents, resurrect
events, or make the ledger authoritative for business state.

## Security & privacy
No `record.read_all`/capabilities; no routes/admin; no provider credentials; no external
channel enabled; no notification `title`/`body` or contact data in attempts, results, or
logs (`last_error` stores only the content-free normalized description/failure class);
references only.

## Migration
`f55d1s2p3t4c` (chained from `f53p1r2c3n4t`): create the immutable
`notification_delivery_attempts` table (with the append-only immutability trigger) **only**.
It makes **no** change to notification lifecycle semantics — the `notifications.status` CHECK
is untouched. Additive and reversible (the reversibility gate runs on an empty schema); single
Alembic head `f55d1s2p3t4c`.

## Out of scope (F5.6+)
Notification audit/evidence, reporting/compliance evidence, API/admin surfaces, scheduled
retry execution, escalation, and any external provider integration/credentials.

## References
ADR-013, ADR-017; `docs/NOTIFICATIONS.md` (F5.1), `docs/NOTIFICATION_PROVIDERS.md` (F5.2),
`docs/NOTIFICATION_PREFERENCES.md` (F5.3), `docs/NOTIFICATION_INTENTS.md` (F5.4);
`app/services/notification_providers.py`, `app/services/notifications.py`.
