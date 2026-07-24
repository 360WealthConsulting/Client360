# Communication Architecture (Phase D.44)

The Unified Communications & Client Engagement layer is a **governed composition** over the platform's
authoritative communication subsystems. It provides one interaction/engagement history across every channel
WITHOUT creating a second messaging, notification, timeline, document, scheduling, audit, or event system.
See [`ADR-049`](adr/ADR-049-unified-communications.md).

## Where it lives
`app/services/communications/engagement/` — deliberately under the existing communications domain to
reinforce that it is not a new domain. Routes: `app/routes/engagement.py` (staff) + portal engagement
routes in `app/routes/portal.py`.

## Authoritative source map (composition, never ownership)
| Interaction | Authoritative owner | How the layer reads it |
| --- | --- | --- |
| Secure messages | Portal (`app/portal/service.py`) | timeline `secure_message` (advisor) / `client_threads` (portal) |
| Staff communications | Communications (D.18) | timeline `conversation_opened` / `communication_logged` |
| Email (inbound) | Microsoft Graph ingestion | timeline `email_received` |
| Appointments / meetings | Scheduling | timeline `calendar_event` (+ portal dashboard meetings) |
| Documents | Document platform | timeline `document_uploaded` |
| Document requests | Portal | timeline `document_requested` / `client_document_requests` |
| Signature requests | Signature provider | timeline `signature_requested` / `signature_completed` |
| Client requests | Exception engine | client action items |
| Workflow milestones | Workflow automation | timeline `workflow_step_completed` |
| Notes (publishable) | Notes | timeline `activity_note_added` |
| Notifications | Notification ledger / portal | `client_notifications` (portal) |

Every mutation stays with the authoritative owner. The layer only reads.

## Two composition spines
- **Advisor / staff spine** (`adapters/timeline.py`) delegates to the authoritative composed projection
  `activity_timeline.client_timeline` / `household_timeline` — already record-scoped, deduplicated by
  `event_id`, ordered, redacted, and paginated — and **classifies** each row onto a registered interaction
  type via the registry, dropping non-communication activity. This is why it is not a second timeline.
- **Client (portal) spine** (`adapters/portal.py`) reuses the D.43 portal grant-scoped reads
  (`client_threads` / `client_notifications` / `client_document_requests` + dashboard meetings), because an
  external portal principal is not a staff principal and cannot use the staff timeline.

## Modules
`registry.py` (interaction catalog + classifier), `model.py` (normalized reference-only `Interaction`),
`service.py` (compose / filter / search / summarize), `adapters/` (read-only, fail-closed normalizers),
`gate.py` (runtime gates), `stats.py` (in-process counters), `metrics.py` (low-cardinality analytics),
`diagnostics.py` (internal-only), `governance.py` (invariant checker).

## Deep-link philosophy
The engagement surfaces never mutate. Every interaction row carries a deep link back to the authoritative
surface where the user acts (open the thread, review the inbox, open the document, sign, reschedule). The
registry declares each type's `supported_actions` and `deep_link`.

## Why this is not a second messaging or timeline system
- It defines **no table**, writes **no rows**, publishes **no events**, and writes **no audit** — governance
  asserts this against every module.
- The advisor timeline is the authoritative `activity_timeline` projection, classified — not a new store.
- Content is never copied: `Interaction.preview` is a short derived snippet; bodies stay in the source.
- Every interaction remains owned by, and is read live from, its authoritative subsystem.

## Integration
Client 360 + Household 360 gain a **Communications** section (composed summary + recent interactions);
Advisor AI Assist grounds on the composed summary (counts only); the client portal gains a **recent
activity** surface. See [`ENGAGEMENT_TIMELINE.md`](ENGAGEMENT_TIMELINE.md),
[`COMMUNICATION_REGISTRY.md`](COMMUNICATION_REGISTRY.md), [`COMMUNICATION_GOVERNANCE.md`](COMMUNICATION_GOVERNANCE.md).

## References
`app/services/communications/engagement/*`, `app/routes/engagement.py`, `docs/platform_architecture_manifest.yaml`,
`tests/test_unified_communications.py`, ADR-049.
