# ADR-075 — Outbound email reply: send under the consented `Mail.Send`, reconcile identity afterwards

## Status
Accepted — supersedes the "outbound send through Graph" and "storing full message bodies" revisit
conditions of ADR-074.

## Date
2026-09-06

## Decision owners
Platform Architecture; Domain Owner (Communications); Business Operations Owner (Michael Shelton).

## Context
ADR-074 made inbound Microsoft 365 mail a canonical `communication_*` record. Staff can now read a
client's email in 360Plus and must leave to answer it. Replying in Outlook drops the answer out of
the client record entirely: the firm's own words to a client are the half of the correspondence
360Plus does not hold.

The obstacle is a consent decision already made. `GRAPH_DELEGATED_SCOPES` is read-only plus
`Mail.Send`:

```
User.Read, Mail.Read, Mail.Send, Calendars.Read, Files.Read.All, Sites.Read.All
```

The safer way to send a threaded reply is draft-first — `POST /me/messages/{id}/createReply` to get a
draft with a real provider message id, then `POST /me/messages/{draft}/send`. **`createReply` and
every other draft-creating call require `Mail.ReadWrite`.** The first attempt at this work (Batch 4c,
stopped) established what adding that scope costs: `acquire_token_silent` requests the full scope
list, so a widened list fails against every cached token. All ~30 connected mailboxes would fail
silently — and because the inbound sync requests the same list, **inbound ingestion would stop for
every mailbox** until each user individually reconnected. A send feature is not worth breaking the
ingestion that ADR-074 just delivered.

`POST /me/messages/{id}/reply` sends a threaded reply under `Mail.Send` alone. It answers
`202 Accepted` **with no body**: no provider message id, nothing to record. Microsoft does not
clearly document custom `internetMessageHeaders` on this endpoint, so a correlation header cannot be
relied on either.

So the real question this ADR answers is not "how do we send" but **"what stands in for the provider
identity we are not given, and how do we avoid sending a client the same email twice?"**

## Decision
**Send with the provider-native direct reply under the existing `Mail.Send`. Do not change the
consented scope set. Replace pre-send provider identity with a durable local send intent, and obtain
the real identity afterwards by reconciling the Sent Items copy.**

1. **Transport.** `POST /me/messages/{source_graph_message_id}/reply` with `{"comment": body}`,
   using the `graph_id` ADR-074 already stores in `communication_message_sources.source_metadata`.
   Threading is Microsoft's, not ours. No `createReply`, no drafts, no `Mail.ReadWrite`, no consent
   change, no mailbox reconnection.

2. **Send intent, committed before the side effect.** A UUID minted when the reply form is rendered
   is written — and committed — as a `communication_message_sources` row with
   `source_system = 'client360_send_intent'` **before** Graph is called. That table already carries
   `UNIQUE (source_system, source_external_id)`, so the key is race-safe against concurrent submits
   **without a migration**. A repeat of a key never reaches Graph.

3. **Status lifecycle, in the existing vocabulary.** `queued` → `sending` → `sent` | `failed` on
   `communication_messages.status`, mirrored as `communication_deliveries` rows.
   `sent` means **Graph accepted it for transport** and nothing more; `delivered` and `read` are
   never written by this path, because nothing observed either. "Pending reconciliation" needs no
   new state: it is `sent` with no `microsoft_graph` source row.

4. **The uncertain window is a refusal, not a retry.** Between Graph's acceptance and our recording
   of it, a crash leaves the message in `sending`. **Only `failed` — written solely when Graph
   explicitly refused — may be sent again.** `sending` is uncertain and is resolved by
   reconciliation. A duplicate email to a client cannot be withdrawn; a missing one is visible and
   can be re-sent by hand.

5. **Reconciliation is deterministic.** When the sent copy comes back on a later Sent Items poll,
   `email_ingest.normalize_email` attaches the real `internetMessageId` to the existing outbound
   message instead of creating a second one. Matching uses the provider conversation, the mailbox
   that sent it, and an outbound message still awaiting identity — **never subject text and never a
   custom header**. Afterwards the ordinary uniqueness path deduplicates every later poll.

6. **Full outbound body is retained.** `communication_messages.body` is unbounded `TEXT`, so this
   costs no schema change and no new retention subsystem. A 500-character preview is derived into
   `message_metadata` for lists.

7. **Authorization.** `communications.send`, plus record scope over the conversation's anchor, plus
   `account_for_principal` — which matches the signed-in user's own address and never falls back to
   another account. All three are re-checked on POST. Failure is closed and does not say which check
   failed.

8. **Surface.** One reply action on an inbound email: body and Send. **No recipient input exists** —
   the address comes from the stored conversation and Graph's reply semantics — so no request can
   redirect an outbound email. No Reply All, no composer, no attachments, no bulk send.

### Asymmetric body retention, stated plainly
Inbound is stored as a 500-character preview (ADR-074); outbound is stored in full. This is
deliberate. Inbound is third-party content mirrored from a mailbox that remains its system of
record. Outbound is the firm's **own** statement to a client — the thing a complaint, an audit or a
supervisory review actually asks about — and truncating it would make the record weaker than the act
it records.

## Alternatives considered
- **Add `Mail.ReadWrite` and use the draft-first flow.** Rejected: strictly better identity handling,
  but it breaks inbound sync for every connected mailbox until each user reconnects. Revisit if a
  tenant-wide re-consent happens for another reason.
- **`POST /me/sendMail` with a hand-built `In-Reply-To`.** Rejected: reconstructs threading Microsoft
  already does correctly, and mis-threading is visible to the client.
- **A correlation `internetMessageHeaders` value for reconciliation.** Rejected: not clearly
  documented as supported on `/reply`; a matching strategy that silently stops working would leave
  outbound messages permanently unidentified.
- **A new `send_intents` table.** Rejected: an existing table with the right uniqueness constraint
  does the job. A migration for identity we can already express would be scope we were told to avoid.
- **Subject-based reconciliation (`RE: …`).** Rejected: not an identity. Two replies in one thread,
  or a client's own `RE:`, would collide.
- **Treating an uncertain send as retryable.** Rejected: it converts a rare lost update into a
  duplicate client email, trading a recoverable failure for an unrecoverable one.

## Reasons for the decision
- It ships outbound correspondence into the client record **without touching consent**, so inbound
  ingestion cannot be collateral damage.
- The idempotency guarantee is a **database constraint**, not application timing.
- Every state is one the repository's own vocabulary already defines, so no reader has to learn a
  private lifecycle.
- The uncertainty Graph's `202` creates is represented honestly rather than papered over: the record
  says "we do not yet know the provider identity" until it does.

## Consequences

### Positive consequences
- The firm's replies live in the client record, threaded with the inbound email they answer.
- No migration, no schema change, no consent change, no mailbox reconnection.
- One email is one message and one timeline row, preserved through reconciliation.
- A double-submitted Send cannot produce two client emails.

### Negative consequences and tradeoffs
- **An outbound reply has no provider identity until the next Sent Items poll** (≤15 minutes). Until
  then its `message_metadata.provider_identity` reads `pending_reconciliation`.
- **A send interrupted after Graph accepted it cannot be re-sent from the UI.** Correct, but it is a
  dead end a staff user must resolve in Outlook.
- Reconciliation depends on the sent copy being polled. If Sent Items is not synced for a mailbox,
  outbound messages stay pending indefinitely — recorded, but never provider-identified.
- Reconciliation matches the oldest-pending outbound in a conversation; two replies sent within one
  poll interval in the same thread could in principle be paired to each other's copies. Both are
  recorded and both were sent, so the exposure is a swapped `internetMessageId`, not a lost or
  duplicated message.
- Outbound replies write **no timeline event**, so they do not appear in the engagement timeline.
  Consistent with ADR-074's recipient-matched case; widening the timeline is user-visible and out of
  scope.
- No attachments on outbound. Staff must use Outlook for those.

## Enforcement
`tests/test_email_outbound.py` — the consented scope set is pinned and the send path is asserted free
of `createReply` / `Mail.ReadWrite` / draft APIs; authorization (capability, record scope, mailbox
identity) fails before Graph is contacted; the intent is committed and visible on another connection
before the transport runs; a repeated key sends nothing and creates no second message; the lifecycle,
the never-`delivered`/`read` rule, full-body retention, the reply endpoint's exact URL and payload,
and reconciliation (attachment, idempotency, no duplicate message, no timeline event, wrong
conversation, wrong mailbox, already-reconciled, uncertain-send recovery).

## Exceptions
None.

## Revisit conditions
A tenant-wide re-consent for any other reason (revisit the draft-first flow), outbound attachments,
Reply All or a general composer, shared-mailbox or send-on-behalf-of sending, delivery/read receipts
from a source that actually observes them, or bulk/marketing send would each warrant a new or
superseding ADR.

## References
- `app/services/communications/email_send.py`, `app/services/communications/email_ingest.py`,
  `app/routes/communications.py`, `app/templates/communications/reply.html`
- `app/services/microsoft_identity.py` (`GRAPH_DELEGATED_SCOPES`, `account_for_principal`)
- `app/database/communication_tables.py` (`uq_comm_message_source_identity`)
- Supersedes two revisit conditions of ADR-074; relates to ADR-023, ADR-049 (composition, not a
  second store)
