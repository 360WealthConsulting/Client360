# Client Portal Messaging (Phase D.43)

Secure client↔advisor messaging is provided by the existing portal messaging layer
(`app/portal/service.py`, tables `portal_threads` / `portal_thread_participants` / `portal_messages` /
`portal_message_receipts` / `portal_message_attachments`). D.43 adds no second messaging system. See
[`ADR-048`](adr/ADR-048-secure-client-portal.md).

## Threads & messages
- `GET /api/v1/portal/messages` lists the account's threads (scope-guarded).
- `POST /api/v1/portal/messages` creates a thread (`create_thread`) under the `messages` grant permission +
  person/household scope.
- `GET /api/v1/portal/messages/{thread_id}` lists messages; `POST /api/v1/portal/messages/{thread_id}`
  sends a message (`send_message`).
- Messages are **append-only** (a database trigger blocks updates/deletes); receipts and audit events are
  likewise tamper-evident.

## Internal notes never leak
Staff can attach `internal_note=True` messages (`staff_send_message`) that are visible only internally.
`list_messages` filters them out for portal principals — the portal never shows internal notes, advisor
reasoning, or hidden workflow state. This is enforced at the query layer and covered by tests.

## Attachments respect document scope
A message attachment must reference a document the sender is entitled to; attaching an out-of-scope document
raises `PermissionError`. Attachment visibility inherits the thread's scope.

## Delegated actions over messaging
Appointment requests are delivered as governed secure-message threads (see
[`CLIENT_PORTAL_REQUESTS.md`](CLIENT_PORTAL_REQUESTS.md)); the advisor completes the authoritative action.

## Audit & timeline
Portal message activity publishes to the authoritative activity timeline
(`timeline_events`, source `client_portal`) and writes audit events (references only). D.43 adds NO outbox
contracts — the portal uses the append-only audit ledger, not a second event bus.

## Visibility
`messages.thread` / `dashboard.unread_messages` are `conditional` on the `messages` grant permission in the
visibility registry. See [`CLIENT_PORTAL_VISIBILITY_REGISTRY.md`](CLIENT_PORTAL_VISIBILITY_REGISTRY.md).

## References
`app/portal/service.py` (threads/messages), `app/routes/portal.py`, `app/portal/appointments.py`,
`tests/test_client_portal.py`, `tests/test_secure_client_portal.py`, ADR-048.
