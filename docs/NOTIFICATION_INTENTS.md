# Client360 — Event-Driven Notification Intent Creation (F5.4 / Epic 5)

The canonical **event-to-notification-intent** layer, governed by
[ADR-017](architecture/adr/ADR-017-notifications-and-communications-architecture.md). F5.4
consumes *approved* business/workflow events (post-commit, via the F1.3 transactional
outbox) and deterministically records **notification intents** in the F5.1 ledger. It
**stops at intent creation** — no dispatch, no provider delivery, no retries, no
delivery-attempt rows, no notification audit/evidence, no routes, no capabilities.

`app/services/notification_intents.py` · no migration (reuses F5.1)

## Core architectural rule
Business/workflow events remain authoritative for *what happened*; a notification intent
only records that *a communication may need to occur*. Creating or suppressing an intent
**never** completes a task, satisfies an obligation, changes workflow/domain/evidence
state, alters the originating event, infers business completion, or makes the notification
ledger authoritative for the underlying event. F5.4 is a **derived communication-intent
layer only**.

## Existing-system reconciliation
| Source | Classification | F5.4 use |
|---|---|---|
| F4.3 workflow events over the F1.3 outbox (`app/platform/workflow_events.py`, `outbox.py`) | **Authoritative** event source | **Approved** — consumed |
| F1.3 transactional outbox (`subscribe` / `dispatch_pending` / `outbox_processed_events`) | **Authoritative** delivery + idempotency mechanism | Reused (post-commit, at-least-once) |
| F5.1 ledger (`notifications`) | **Authoritative** for intent/outcome | Intents written here |
| F5.2 provider registry / F5.3 decision layer | **Authoritative** for channel state / eligibility | F5.3 invoked; F5.2 read via F5.3 |
| Portal `notify()` → `portal_notifications`, `benefits_notifications`, `exception_sla`, employer-portal | **Legacy / advisory** portal-scoped paths | **Unchanged, not consumed** by F5.4 |

F5.4 does **not** silently subscribe to every event: only the explicitly approved mappings
below are consumed.

## Approved event mappings (deliberately narrow)
Both events carry an explicit, unambiguous recipient (the assigned approver) **directly in
the event payload**, so the recipient is derivable safely and deterministically without any
lookup or PII, and both already have validated notification meaning ("you have an approval
to act on").

| Field | `workflow.approval.requested` | `workflow.approval.reassigned` |
|---|---|---|
| Mapping id | `workflow.approval.requested.v1` | `workflow.approval.reassigned.v1` |
| Authoritative source | F4.3 workflow approval events | F4.3 workflow approval events |
| Notification purpose | `workflow.approval.requested` | `workflow.approval.reassigned` |
| Recipient derivation | `user:{payload.approver_user_id}` | `user:{payload.to_approver}` |
| Channel eligibility | `in_app` (enabled) | `in_app` (enabled) |
| Idempotency key | `f5.4:{mapping}:{event_id}:{recipient}:{channel}:{purpose}` | same shape |
| Correlation / causation | envelope `correlation_id` / `causation_id` → metadata | same |
| Initial ledger status | per F5.3 decision (see policy) | per F5.3 decision |
| Consent required | no (in-app) | no (in-app) |
| Suppression behavior | suppressed → **suppressed row** (decision preserved) | same |
| Why in scope | explicit recipient in event; already-meaningful | explicit recipient in event; already-meaningful |

If `approver_user_id` / `to_approver` is absent (e.g. a team-only assignment), **no intent
is created** (`not_applicable`).

**Deferred (documented, not silently dropped):** `workflow.approval.decided` (event carries
the decider, not the requester — no deterministic recipient without a lookup), SLA
escalation and lifecycle transition events (no explicit user recipient in the payload).
These are candidates for later Epic 5 features once a safe recipient resolver exists.

## Mapping registry
`NotificationMapping` (frozen, **content-free**): `mapping_id`, `source_event_type`,
`notification_purpose`, `recipient_resolver`, `channel`, `recipient_type`,
`consent_required`, `enabled`, `version`, `template_ref`. One mapping per event type
(`register_mapping` rejects a duplicate). `install_default_mappings()` seeds the approved
set (idempotent). Unknown/unmapped events → explicit `not_applicable` no-op; **no fuzzy
event-name matching**.

## Recipient resolution
A mapping's resolver derives a recipient **reference** (`user:{id}`) from the event payload
only. It performs no DB lookup and copies no contact information. A resolver returning
`None` means the event lacks a safe deterministic recipient → **no intent**.

## F5.3 decision integration (normative policy)
Every intent runs through `evaluate_delivery(...)`; F5.4 never bypasses it and never mutates
preference/consent records.

| F5.3 decision | F5.4 action | Ledger status | Outcome |
|---|---|---|---|
| `allowed` | create pending intent | `pending` | `created` / `already_exists` |
| `suppressed` | create suppressed intent (decision preserved) | `suppressed` | `suppressed` |
| `disabled` | create non-deliverable record (no pending, no attempt) | `disabled` | `disabled` |
| `not_applicable` | **no row** | — | `not_applicable` |

## Intent-creation behavior & content-minimality
The service accepts/derives: source event id, source event type, correlation/causation
references, recipient type/reference, notification purpose, channel, a **template
reference** (`template:{purpose}` — never a rendered body), idempotency key, and the F5.3
decision reference. The F5.1 row is **content-minimal**: `title` holds the template
*reference*, `body` is `None`, and `notification_metadata` holds references only
(mapping id/version, source event type, correlation/causation ids, the F5.3 decision
summary, and reference ids such as `workflow_instance_id`/`approval_id`). No domain
payloads, email addresses, phone numbers, or rendered content are copied.

## Result contract
`IntentResult` (structured, **content-free**): `outcome`
(`created`/`already_exists`/`suppressed`/`disabled`/`not_applicable`/`failed`),
`source_event_id`, `source_event_type`, `mapping_id`, `channel`, `recipient_ref`,
`notification_uid` (when a row exists), `decision_reason_code`, human-safe `description`.
No `title`/`body`. `failed` is reserved for a deterministic, safe defensive case (an
unrecognized decision); infrastructure errors propagate so the outbox retries.

## Idempotency & transaction boundary
- **Deterministic key** `f5.4:{mapping_id}:{source_event_id}:{recipient_ref}:{channel}:{purpose}`;
  the F5.1 **unique `dedupe_key`** is the durable backstop. A re-processed event returns the
  existing intent (`already_exists`) — never a duplicate, across restarts and repeated
  invocation, independent of in-memory state.
- **Second layer:** the outbox `outbox_processed_events (event_id, consumer)` prevents
  re-running the consumer for an already-processed event.
- **Post-commit:** consumers run in `dispatch_pending()` **after** the authoritative
  transaction commits (an outbox event exists iff the transition committed), so an intent is
  never created for a rolled-back event and nothing dispatches inside the originating
  transaction. **Dark-launched:** `register_notification_consumers()` is invoked only from
  the gated outbox block in `app/jobs/scheduler.py`, so no subscriber exists until the
  dispatcher is explicitly enabled (default runtime behavior unchanged).

## Point-in-time decisions (normative)
A notification intent records the eligibility decision made when the authoritative source
event was processed. Later changes to notification preferences, consent state, suppression
state, or provider readiness do not retroactively promote, replace, recreate, or otherwise
modify the intent recorded for that historical event. Reprocessing the same source event
returns the existing idempotent result. New authoritative events are evaluated using the
preference, consent, suppression, and provider state effective when those new events are
processed.

Consequences (normative):
- **Suppressed and disabled intents are terminal for the originating source event.** They
  are recorded once and never transitioned by F5.4.
- **Preference or provider-state changes do not resurrect historical intents.** An opt-in
  after an opt-out, or a channel being enabled after it was disabled, does not create a
  pending intent for, or mutate, an event that was already evaluated.
- **A new communication attempt requires a new authoritative event** or a separately
  authorized future re-notification design. Nothing in F5.4 re-fires a stale event.
- **The idempotency key intentionally excludes the decision outcome and ledger status**
  (`f5.4:{mapping_id}:{source_event_id}:{recipient_ref}:{channel}:{purpose}`). Including the
  decision would let a flapping preference spawn multiple rows for one event; excluding it
  is what guarantees deduplication.
- **This preserves exactly one intent per (mapping, source event, recipient, channel,
  purpose).**
- **F5.4 does not implement re-notification or historical-intent promotion.** Re-evaluation
  or promotion of prior intents is explicitly out of scope (a separately authorized future
  design if ever needed).

Suppressed and disabled ledger rows are durable **communication decisions** — records of
what the notification layer decided about contacting a recipient — never workflow or
business state.

## Migration
**None.** F5.1 already supports source-event references (`source_event_id`, `source_ref`),
purpose (`notification_type`), channel, recipient reference, reference metadata
(`notification_metadata`), and durable idempotency (unique `dedupe_key`). Correlation and
causation are preserved as references inside `notification_metadata`. Head remains
`f53p1r2c3n4t`.

## Security & privacy
No `record.read_all`/capabilities; no routes; no provider credentials; no external channel
enabled; no provider invocation; no rendered content or contact data in results, ledger, or
logs; recipient **references** only; no silent recipient guessing; no intent without an
approved mapping.

## Out of scope (F5.5–F5.7)
Dispatch/retry worker and delivery attempts (F5.5), notification audit/evidence (F5.6),
API/admin surface (F5.7), external provider integration, and broader event mappings.

## References
ADR-013, ADR-016, ADR-017; `docs/NOTIFICATIONS.md` (F5.1),
`docs/NOTIFICATION_PROVIDERS.md` (F5.2), `docs/NOTIFICATION_PREFERENCES.md` (F5.3);
`app/platform/workflow_events.py`, `app/platform/outbox.py`.
