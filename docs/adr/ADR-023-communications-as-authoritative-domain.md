# ADR-023 — Communications as an authoritative communication-metadata domain

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Communications); Compliance Architecture (communication audit
history is regulated recordkeeping); Business Operations Owner (Michael Shelton — client-engagement
requirements). Authorized compliance reviewer: Not yet designated (communication retention/records
rules require compliance sign-off before any regulated change).

## Context
The platform had communication **transport/plumbing** but no communication **domain**. Existing
pieces: a canonical staff notification ledger (`notifications` + the `notification_intents /
_preferences / _dispatch / _providers / _worker` pipeline, ADR-017/F5.x), a transactional outbox
(`outbox_events` + `app/platform/outbox.py`), client-portal secure messaging (`portal_threads /
portal_messages / portal_message_receipts / portal_message_attachments / portal_notifications`),
and Microsoft 365 read/sync (Outlook mail ingestion, calendar, OneDrive/SharePoint documents). The
`communication.read`/`communication.write` capabilities existed but only gated the M365 UI via
middleware — they backed **no** communication tables. There was **no** staff-facing model of a
conversation/message across channels, **no** delivery-status lifecycle, and **no** reusable
message-template system. Business domains (opportunity, campaign, annual review, workflow, etc.)
had nowhere to reference a firm's outbound/inbound communications.

## Decision
Communications becomes its **own authoritative domain** that owns **communication metadata only**
and is **never a source of truth for business records**.
- **Owns:** `communication_conversations` (the Communication/Conversation container),
  `communication_threads`, `communication_messages`, `communication_recipients`,
  `communication_deliveries` (the delivery-lifecycle ledger), `communication_attachments`
  (document/attachment references), `communication_templates` (reusable, deterministic message
  templates), and `communication_events` (an **append-only** audit ledger, trigger-blocked
  BEFORE UPDATE OR DELETE).
- **References, never owns:** people/households/organizations are anchors (`ON DELETE SET NULL`;
  the organization anchor is the canonical `relationship_entities.id` — the same id
  `organization_in_scope` uses). Documents are referenced by `communication_attachments`
  (never duplicated). Workflow / Compliance / Opportunities / Campaigns / Referral Sources /
  Annual Reviews / Business Owner Plans reference communications, not the reverse.
- **Reuses transport — no proprietary transport is implemented.** Delivery is **metadata only**:
  marking a message `sent` records **intent** in the EXISTING notification ledger
  (`record_notification`, mirroring its intent-only contract) and links the `notification_uid`
  back onto the message. No mail server / SMS gateway / Microsoft Graph send is implemented; the
  supported channels (email, sms, portal_message, teams, internal_notification, phone_log, letter,
  secure_message) and delivery statuses (queued→scheduled→sending→sent→delivered→failed→cancelled
  →read→expired) are controlled vocabularies, not transports.
- **Deterministic.** Template rendering is a pure `{{placeholder}}` substitution over a supplied
  context (unknown placeholders render blank — never invents content). No AI, no probabilistic
  routing, no natural-language generation.
- **Timeline:** approved communication lifecycle events only (`conversation_opened`,
  `communication_sent`, `communication_delivered`, `communication_read`) flow to the shared
  Activity Timeline via `add_timeline_event` (client-anchored, best-effort) — **not** every status
  transition. **Analytics:** consumes an `active_conversations` statistic; Communications never
  depends on Analytics.
- **Security:** the `communications.view/send/manage_templates/audit*/admin*` capability family
  (`*` = sensitive) gates a new `/communications` surface (in-route; the prefix matches no
  middleware RULE). Record scope is **always** enforced in-service (person/household/organization
  anchor, or `record.read_all`; firm-wide conversations with no anchor are visible to
  `communications.view`). The legacy `communication.read/write` M365 capabilities are unchanged.

## Alternatives considered
1. **Extend client-portal messaging (`portal_*`) into the enterprise model.** Rejected: those
   tables are client-portal-scoped (household-anchored, portal-account senders) and coupled to the
   portal session model; overloading them would entangle the staff communications domain with
   portal auth and break the portal's narrower contract.
2. **Make Communications a transport (implement Graph send / SMTP / SMS).** Rejected: the phase
   explicitly forbids proprietary transport; the notification ledger + outbox + M365 already own
   dispatch. Communications records metadata and delegates transport.
3. **Back the existing `communication.read/write` capabilities instead of a new family.** Rejected:
   those capabilities are wired to the M365 middleware prefix rule; repurposing them would silently
   change M365 authorization. A new `communications.*` family is additive and unambiguous.
4. **Reuse the notification ledger as the message store.** Rejected: the ledger is a
   non-authoritative per-recipient delivery record with no conversation/thread/template model; it
   is the transport seam, not the domain.

## Reasons for the decision
The firm needs one authoritative, auditable model of *who was communicated with, about what, on
which channel, and with what delivery/read status* — that other domains can reference — without
re-implementing transport. An owned metadata domain that reuses existing dispatch delivers this
while preserving every ownership boundary and the D.5 golden.

## Consequences
### Positive consequences
- A single authoritative communication-metadata domain with a deterministic delivery lifecycle,
  reusable templates, threaded conversations, and an append-only audit ledger.
- Zero new transport infrastructure; the notification ledger, outbox, and M365 integrations are
  reused, not duplicated.
- Cross-domain reference point (business domains link to communications; Communications owns no
  business entity) with record scope enforced everywhere.

### Negative consequences and tradeoffs
- Two "communication" capability families now coexist: legacy `communication.read/write` (M365 UI)
  and `communications.*` (this domain) — a documented coexistence, mirroring the `work.*` /
  `workflow.*` split from ADR-022.
- Delivery is metadata only: statuses are advanced by staff action / future adapters, not by a live
  provider callback. Read status reflects recorded intent, not a wire receipt.
- A conversation with audit events cannot be hard-deleted (the events ledger is append-only,
  RESTRICT-anchored); teardown detaches anchors and leaves conversations as leftovers.

## Enforcement
- `app/database/communication_tables.py::define_communication_tables` (registered in
  `app/database/schema.py`; reflected in `app/db.py`). Migration `p6a7b8c9d0e1` (8 tables +
  append-only trigger on `communication_events` + 5 `communications.*` capabilities + 12 starter
  templates). Services `app/services/communications/{service,templates,delivery}.py`; routes
  `app/routes/communications.py` (in-route `communications.*` gating; `/communications` matches no
  middleware RULE). The D.5 golden, the notification ledger/outbox, M365 integrations, and the
  legacy `communication.read/write` capabilities are untouched. Tests:
  `tests/test_communications.py`; manifest / platform-architecture / route-count guards updated.

## Exceptions
None currently approved.

## Revisit conditions
Implementing a real outbound transport (Graph send / SMTP / SMS gateway), a provider
delivery-receipt webhook, inbound message ingestion into conversations, or client-portal/staff
conversation unification would each warrant a new or superseding ADR (and, for regulated retention
changes, compliance sign-off).

## References
- `app/services/communications/`, `app/routes/communications.py`,
  `app/database/communication_tables.py`, migration
  `migrations/versions/p6a7b8c9d0e1_communications_platform.py`
- Reused transport: `app/services/notifications.py`, `app/platform/outbox.py`, Microsoft 365
  integrations (`app/services/microsoft_*`, `app/routes/microsoft365*`)
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_communications.py`; relates to ADR-002, ADR-009, ADR-013, ADR-016, ADR-017, ADR-021,
  ADR-022
