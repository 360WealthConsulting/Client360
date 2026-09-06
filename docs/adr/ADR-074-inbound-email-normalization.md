# ADR-074 — Inbound email normalization: canonical communication records beside the timeline, never a second history

## Status
Accepted — supersedes the "inbound message ingestion into conversations" revisit condition of ADR-023.

## Date
2026-09-06

## Decision owners
Platform Architecture; Domain Owner (Communications); Business Operations Owner (Michael Shelton).

## Context
Inbound Microsoft 365 mail has been ingested on a schedule since the Microsoft integration shipped
(`app/jobs/microsoft_mail_sync.py`, every 15 minutes, one pass per connected mailbox). What it
produces is a `timeline_events` row for a sender it recognises and a `microsoft_unmatched_messages`
row for one it does not. Neither is a communication record: an email has no conversation, no message
row, no recipients, and cannot be counted, filtered or threaded alongside the firm's other
correspondence.

Two identifiers that make an email addressable were being read and discarded. The read-only preview
route (`microsoft365_mail.MESSAGE_SELECT`) has selected `conversationId` and `internetMessageId`
against the live API since it shipped; the sync's `$select` omitted both.

The D.44 audit also surfaced two defects in the existing path:

1. **The Graph `id` is not a stable message identity, and it was being used as one.** It is
   mailbox-scoped and changes when a message moves between folders, so the timeline key
   `outlook-message-{graph id}` records the same email twice when it is filed, and once per mailbox
   when two mailboxes are connected.
2. **`/me/messages` is not folder-scoped**, so Sent Items arrive in the same pass. An outbound
   message's `from` is the staff member, never matches a client, and accumulated in the unmatched
   review queue as though it were an unrecognised inbound sender.

ADR-023 named "inbound message ingestion into conversations" as warranting a new or superseding ADR.
This is that ADR.

## Decision
Inbound email is **normalized into the existing `communication_*` tables beside the existing timeline
write**, and the timeline write does not change.

1. **One email, one timeline row.** Normalization emits no timeline event of its own. The D.44
   registry classifies a timeline row by `(source, event_type)` and falls back to `event_type` alone,
   so a `conversation_opened` event for an email that already has `email_received` would make it
   appear twice in every engagement timeline. Normalization therefore writes its rows directly rather
   than through `communications.service.create_conversation`, which publishes such an event.
2. **Identity is the RFC 5322 `internetMessageId`**, which is stable across folders, mailboxes and
   tenants — never the Graph `id`, which is kept in source metadata for traceability. Items with no
   Message-ID fall back to the most specific composite available
   (`{tenant}:{mailbox}:{graph id}`).
3. **Provider identity lives in `communication_message_sources`** (migration `emailnorm01`),
   deliberately mirroring `document_sources` (ADR-072, whose source-system list already names
   "Email"): one canonical record, many source references. `UNIQUE (source_system,
   source_external_id)` is the idempotency key, enforced by the database rather than by a
   read-then-write that would race between workers. The same email seen in two connected mailboxes is
   ONE message with two sightings.
4. **Threading is composite.** `conversationId` is scoped to a mailbox, so the conversation key is
   `(tenant_id, mailbox_user_id, conversationId)`, held in `conversation_metadata`. `conversationId`
   never becomes an internal primary identity.
5. **Anchoring never guesses.** One matched person anchors the person and their household; several
   people in one household anchor the household; several across different households is ambiguous and
   anchors nothing. An email with no anchor writes no conversation and stays in the existing review
   queue — normalization cannot manufacture an orphan conversation.
6. **Timeline and queue contracts are preserved exactly.** A timeline event is written when, and only
   when, the SENDER is a recognised client, as before. Recipient matching widens what can be
   *normalized*, never what appears on a client's timeline. The one behavioural change is that a
   message the mailbox owner sent no longer enters the unmatched queue — defect 2 above.
7. **`sender_type` admits `external`.** The vocabulary was `('user','system')`; an ingested email has
   a sender who is neither, and recording that as `system` would be false in the data.
8. **No body, no attachments, no deliveries.** The same preview the timeline already stores is
   persisted and surfaced in no new UI; `communication_deliveries` is an outbound lifecycle ledger and
   inbound mail has no delivery intent of ours to record.

## Alternatives considered
1. **Write inbound email through `communications.service.create_conversation`.** Rejected: it
   publishes a `conversation_opened` timeline event, which would double-count every email in the
   engagement timeline.
2. **Use the Graph `id` as identity** (the status quo). Rejected: mailbox-scoped and unstable across
   folder moves; it is the cause of the existing duplicate-timeline defect.
3. **Use `conversationId` as the conversation primary key.** Rejected: mailbox-scoped, so the same
   thread carries different values in different mailboxes and would fragment or collide.
4. **Add a unique index to `communication_messages` instead of a sources table.** Rejected: it would
   bind a channel-neutral table to one provider's identifier and could not express the same message
   seen in two mailboxes. `document_sources` already solved this shape.
5. **Store the full message body now.** Deferred, not rejected: Communications carries a REGULATORY
   retention class, so full inbound correspondence — quoted history, signatures, whatever a client
   pastes — is a compliance decision with a retention consequence and deserves its own decision.
6. **Ingest attachments now.** Deferred: the sync receives only `hasAttachments`; metadata needs a
   second Graph call and bytes a third. `msgatt01` already accommodates them when they land.
7. **Converge portal messages into `communication_messages`.** Rejected, per ADR-049: that would be a
   second store for one message. The engagement layer already composes portal and email through the
   timeline projection.

## Reasons for the decision
Normalizing beside the timeline rather than through it keeps the authoritative projection unchanged
and the user-visible history identical, while giving email the canonical record it needs to be
counted, filtered and threaded. Reusing the `document_sources` shape means provider identity is
modelled the way this platform already models it, and gives multi-mailbox de-duplication without a
second decision. Fixing identity to the Message-ID repairs an existing duplication defect as a side
effect rather than as separate work.

## Consequences

### Positive
- Email becomes a first-class communication record without a second history or a new store.
- Idempotency is a database constraint, not a convention, and survives re-runs, retries, folder moves
  and duplicate mailbox copies.
- The Graph-id duplication defect and the Sent-Items queue pollution are both closed.
- Attachments and outbound send can be added later without another identity migration.

### Negative and tradeoffs
- Conversation lookup scans email conversations and matches on `conversation_metadata` rather than an
  indexed column; acceptable at current volume, and a generated column or index is a later,
  additive change if it stops being so.
- A message manually matched from the review queue has no Message-ID or `conversationId` stored (the
  queue predates this work), so it is reconciled by Graph id and becomes its own conversation.
- Recipient-matched email is normalized but has no timeline event, so it does not appear in the
  engagement timeline until the sender is also known. Deliberate: widening the timeline is a
  user-visible change and is out of scope here.

## Enforcement
`tests/test_email_normalization.py` — identity and idempotency (re-run, folder move, second mailbox),
atomicity with the timeline write, the one-timeline-row rule, composite threading, matching
(sender / recipient / household / ambiguous / unmatched), direction inference, anchor stability, and
that no body or storage identifier is persisted. Migration head and manifest guarded by
`tests/test_deployment_readiness.py` and `docs/platform_architecture_manifest.yaml`.

## Exceptions
None.

## Revisit conditions
Storing full message bodies (needs a retention decision), ingesting email attachments, outbound send
through Graph, a delta/cursor-based sync, shared-mailbox ingestion, or any convergence of portal
messages with `communication_messages` would each warrant a new or superseding ADR.

## References
- `app/services/communications/email_ingest.py`, `app/jobs/microsoft_mail_sync.py`,
  `app/routes/microsoft365_inbox_review.py`
- `migrations/versions/emailnorm01_communication_message_sources.py`,
  `app/database/communication_tables.py`
- Supersedes part of ADR-023; relates to ADR-049 (composition, not a second store), ADR-072
  (canonical record + many source references), ADR-009 (timeline as projection)
