# ADR-024 — Scheduling as an authoritative scheduling-metadata domain

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Scheduling); Compliance Architecture (meeting/scheduling
audit history is regulated recordkeeping); Business Operations Owner (Michael Shelton — calendar &
meeting requirements). Authorized compliance reviewer: Not yet designated.

## Context
The platform had calendar **integration/plumbing** but no scheduling **domain**. Existing pieces: a
one-way Microsoft 365 Graph calendar sync (`app/jobs/microsoft_calendar_sync.py`,
`app/services/calendar.get_person_calendar_events`, routes under `/microsoft365`) that lands
Outlook events onto the shared Activity Timeline as read-only `calendar_event` rows plus an
unmatched-attendee review queue (`microsoft_unmatched_calendar_attendees`); read-only "meeting"
views over those timeline rows in the Advisor Workspace (`get_meeting_brief` /
`record_meeting_outcome`, which persist no meeting entity) and Annual Review; workflow SLA `due_at`
(relative deadlines, not calendar appointments); and the notification ledger + outbox as delivery
transport. **No table modeled a meeting, appointment, room, resource, availability, booking,
attendee, recurrence, or reminder as an owned entity.** The M365 `/calendar` UI is gated by the
legacy `communication.read` capability. There was no owned, editable scheduling record and no
`scheduling.*` capability surface.

## Decision
Scheduling becomes its **own authoritative domain** that owns **scheduling metadata only** and is
**never a source of truth for business records**.
- **Owns:** `meetings` (the unified Meeting/Appointment/Calendar Event, distinguished by
  `meeting_type`), `meeting_templates` (reusable meeting templates), `scheduling_resources`
  (rooms/equipment/virtual/staff), `meeting_attendees`, `meeting_resource_bookings`,
  `meeting_reminders`, `meeting_followups`, and `scheduling_events` (an **append-only** audit
  ledger, trigger-blocked BEFORE UPDATE OR DELETE).
- **References, never owns:** people/households/organizations are anchors (`ON DELETE SET NULL`;
  the organization anchor is the canonical `relationship_entities.id`). Cross-domain links are
  nullable references — `opportunity_id`, `annual_review_session_id`, `conversation_id`
  (Communications), `workflow_instance_id` (Workflow), `agenda_document_id` (Documents),
  `meeting_followups.advisor_work_item_id` (Advisor Work), and `microsoft_event_id`/`virtual_url`
  (Microsoft 365 / Teams). Business domains reference meetings; Scheduling owns none of them.
- **Reuses transport — no calendar provider is implemented.** Availability is **deterministic
  metadata**: it composes busy windows from scheduling meetings, resource bookings, and the
  EXISTING Microsoft 365 calendar sync (`get_person_calendar_events`) — **no scheduling
  optimization, no AI recommendation**, just interval-overlap free/busy and conflict detection.
  Reminders record intent in the EXISTING notification ledger (`record_notification`) and link the
  `notification_uid`; no dispatch/calendar-provider send is implemented here.
- **Lifecycle** is a deterministic state machine: draft → scheduled → confirmed → checked_in →
  completed, with cancelled / no_show / rescheduled. **Timeline** receives approved lifecycle
  events only (`scheduling_meeting_scheduled` / `_completed` / `_cancelled` / `_rescheduled`) via
  `add_timeline_event` — **not** every status/metadata update. **Analytics** consumes an
  `upcoming_meetings` statistic; Scheduling never depends on Analytics. **Microsoft 365** and
  **Teams** are reused through references (event id / join URL), never duplicated.
- **Security:** the `scheduling.view/manage/templates/audit*/admin*` capability family (`*` =
  sensitive) gates a new `/scheduling` surface (in-route; the prefix matches no middleware RULE, and
  a bare `/calendar` prefix is deliberately avoided because it is gated by `communication.read`).
  Record scope is **always** enforced in-service (person/household/organization anchor, or
  `record.read_all`; internal/firm meetings with no anchor are visible to `scheduling.view`).

## Alternatives considered
1. **Promote the M365 calendar sync / timeline `calendar_event` rows into the domain.** Rejected:
   those are a read-only provider projection (ADR-009 keeps the timeline a projection, not an
   editable store); an owned scheduling record must be independently mutable and auditable.
2. **Extend Advisor Workspace `record_meeting_outcome` into a meeting store.** Rejected: the
   workspace is a composition view + orchestration (persists no entity, ADR-001/ADR-013);
   overloading it would make a composition layer authoritative.
3. **Implement a calendar provider (Graph create-event / booking pages).** Rejected: the phase
   forbids duplicating provider functionality; M365 owns transport. Scheduling records metadata and
   references the provider event.
4. **Fold meetings into Communications or Workflow.** Rejected: a meeting is neither a message nor a
   workflow instance; conflating them breaks single ownership (ADR-002).

## Reasons for the decision
The firm needs one authoritative, auditable model of *who is meeting whom, when, where, about what,
with which resources, and what came out of it* — that other domains can reference — without
re-implementing a calendar provider. An owned metadata domain that reuses M365 sync + the
notification ledger delivers this while preserving every ownership boundary and the D.5 golden.

## Consequences
### Positive consequences
- A single authoritative scheduling-metadata domain with meetings/appointments, templates,
  resources/rooms, attendees, bookings, reminders, follow-ups, deterministic availability, an
  approved lifecycle, and an append-only audit ledger.
- Zero new calendar infrastructure; the M365 sync, notification ledger, timeline, and
  Communications are reused, not duplicated.
- Cross-domain reference point (business domains link to meetings; Scheduling owns none of them)
  with record scope enforced everywhere.

### Negative consequences and tradeoffs
- Availability is metadata only: busy/free reflects owned meetings + resource bookings + the M365
  overlay, not a live provider free/busy query. No optimization or AI suggestion is provided.
- The Advisor Workspace meeting-outcome flow (over timeline `calendar_event` rows) and the new
  Scheduling outcome coexist — a documented coexistence, mirroring `work.*`/`workflow.*` and
  `communication.read`/`communications.*`.
- A meeting with audit events cannot be hard-deleted (the ledger is append-only, RESTRICT-anchored);
  teardown detaches anchors and leaves meetings as leftovers.
- `meeting_followups` overlaps conceptually with Advisor Work; it is deliberately a lightweight
  scheduling-side follow-up note whose authoritative task is Advisor Work when linked.

## Enforcement
- `app/database/scheduling_tables.py::define_scheduling_tables` (registered in
  `app/database/schema.py`; reflected in `app/db.py`). Migration `q7b8c9d0e1f2` (8 tables +
  append-only trigger on `scheduling_events` + 5 `scheduling.*` capabilities + 10 starter meeting
  templates). Services `app/services/scheduling/{service,templates,availability}.py`; routes
  `app/routes/scheduling.py` (in-route `scheduling.*` gating; `/scheduling` matches no middleware
  RULE). The D.5 golden, the M365 calendar sync, the notification ledger/outbox, the timeline
  projection, Advisor Workspace, Communications, and Workflow are untouched. Tests:
  `tests/test_scheduling.py`; manifest / platform-architecture / route-count guards updated.

## Exceptions
None currently approved.

## Revisit conditions
Implementing a real calendar provider (Graph create/update event, bookable public scheduling
pages), a live provider free/busy query, recurrence expansion into materialized occurrences, or a
background reminder dispatcher would each warrant a new or superseding ADR (and, for regulated
recordkeeping changes, compliance sign-off).

## References
- `app/services/scheduling/`, `app/routes/scheduling.py`, `app/database/scheduling_tables.py`,
  migration `migrations/versions/q7b8c9d0e1f2_scheduling_platform.py`
- Reused integration: `app/services/calendar.py`, `app/jobs/microsoft_calendar_sync.py`,
  `app/services/notifications.py`, `app/services/timeline.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_scheduling.py`; relates to ADR-002, ADR-009, ADR-013, ADR-016, ADR-017, ADR-022,
  ADR-023
