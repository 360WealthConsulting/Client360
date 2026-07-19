# Client360 — Canonical Notification Ledger & Model (F5.1 / Epic 5)

The canonical, platform-level **notification ledger** and persistence model, governed by
[ADR-017](architecture/adr/ADR-017-notifications-and-communications-architecture.md). F5.1
delivers **only** the ledger table and model — no providers, dispatch worker, event
consumers, preferences/consent, or routes (those are F5.2–F5.7). The existing portal and
benefits notification code and `portal_notifications` are **untouched**.

`app/services/notifications.py` · migration `f51n0t1c3d4e`

## Non-authoritative ledger (ADR-017 §8, normative)
The `notifications` ledger records **notification intent and delivery outcomes only**. It is
**never authoritative** for workflow, domain/business, or evidence state — recording,
reading, suppressing, delivering, disabling, failing, retrying, or deleting a notification
never mutates any workflow or domain record. Authoritative state remains in the workflow
engine (ADR-016), the domain services, and the F3.3 evidence store (ADR-015).

## Schema (additive — migration `f51n0t1c3d4e`)
Table `notifications` (created by migration, reflected at runtime; not declared in `schema.py`):

| Column | Meaning |
|---|---|
| `id` | Surrogate key |
| `notification_uid` | Stable external id (uuid, unique) |
| `recipient_type` / `recipient_ref` | Recipient reference (e.g. `user` / `user:3`, `portal_account:7`, `team:2`, `ops` / `benefits-operations`) |
| `channel` | `in_app` / `email` / `sms` / `push` |
| `notification_type` | Purpose (e.g. `workflow.sla.escalated`) |
| `status` | Lifecycle status (CHECK-constrained; see below) |
| `dedupe_key` | Deterministic idempotency key (unique) |
| `source_event_id` | Source F1.4/outbox event id (reference) |
| `source_ref` | Source domain/workflow reference (e.g. `workflow_instance:12`) |
| `provider_ref` | Channel/provider reference (e.g. `in_app`) |
| `attempts` / `last_error` | Retry metadata (forward-compatible with F5.5 dispatch) |
| `title` / `body` | Recipient-facing **content** (see boundary) |
| `notification_metadata` | References only (JSON) |
| `created_at`, `updated_at` | Timestamps |
| `delivered_at`, `failed_at`, `disabled_at`, `suppressed_at`, `dead_at` | Outcome timestamps (where applicable) |
| `read_at` | Read timestamp — **not** a status, **not** a completion signal |
Unique `uq_notifications_uid`, `uq_notifications_dedupe_key`; indexes
`ix_notifications_recipient`, `ix_notifications_status`, `ix_notifications_source_event`;
CHECK `ck_notifications_status`.

The ledger row is intentionally **mutable** for its own lifecycle (status + outcome
timestamps + retry metadata), so — unlike `evidence`/`audit_events` — it has **no**
immutability trigger. Per-attempt append-only delivery history is reserved for **F5.5** (an
additive `notification_delivery_attempts` table) and needs no destructive redesign.

## Lifecycle (ADR-017 §8)
`pending → suppressed | delivered | disabled | failed | dead`. `read` is a `read_at`
timestamp, never a status and never a business-completion signal. The status set and
transition map are exported (`NOTIFICATION_STATUSES`, `TERMINAL_STATUSES`, `LIFECYCLE`) for
F5.5 to consume; F5.1 performs no transitions.

## Model
```python
from app.services.notifications import record_notification, get_notification, notification_dedupe_key
rec = record_notification(notification_type="workflow.sla.escalated", recipient_type="user",
                          recipient_ref="user:3", title="Action required", body="…",
                          source_event_id=evt_id, source_ref="workflow_instance:12")
get_notification(notification_uid=rec.notification_uid)
```
- **Deterministic + idempotent:** `dedupe_key` (default derived from type+recipient+channel+
  source) is unique; the same logical notification returns the existing record.
- **Intent only:** `record_notification` records intent/outcome; it performs **no**
  delivery/dispatch (F5.5) and mutates no workflow/domain state.

## Content / reference boundary (ADR-017 §14)
`title`/`body` may carry recipient-facing **content**, kept **only** inside this ledger.
`notification_metadata`, `source_ref`, and `source_event_id` are **references only**. F5.1
emits **no** events, audit records, or logs — so notification content cannot leak into
events/audit/evidence/logs.

## Reconciliation with `portal_notifications`
The portal ledger (`portal_notifications`, portal-account-scoped) is **retained and
untouched** in F5.1 — no data migrated, deleted, or renamed. The canonical `notifications`
table generalizes it: `portal_account_id → recipient_type=portal_account`/`recipient_ref`;
`entity_type`/`entity_id → source_ref`; `idempotency_key → dedupe_key`. Portal and benefits
send behavior is unchanged; delegation to the canonical service is a later feature.

## Compatibility
Additive, reversible migration (single head `f51n0t1c3d4e`); reflection preserved; no
destructive DDL; no table dropped/renamed; no new capability, route, provider, or dispatch.
Email/SMS/push remain disabled (no provider config added).

## References
ADR-013, ADR-017; `docs/EVIDENCE.md` (F3.3), `docs/OUTBOX.md` (F1.3), `docs/DATABASE.md`;
`app/portal/providers.py`, `app/portal/service.py`, `app/services/benefits_notifications.py`.
