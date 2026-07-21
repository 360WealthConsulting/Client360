# ADR-009 — Activity Timeline is a projection, not an event-sourced platform

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Activity Timeline).

## Context
A chronological, cross-domain "what happened with this client" view is valuable. The temptation is
to make Client360 event-sourced — a single event log that is the source of truth and to which every
state transition writes. That would be a large architectural commitment, would duplicate
source-domain records, and would tempt fabricating events for renders and recomputed summaries.

## Decision
The Activity Timeline **must** be a read **projection**, and Client360 **must not** be event
sourced. Distinctions that **must** be preserved:
- `timeline_events` — durable domain events; source-domain records — the authoritative state;
  domain append-only ledgers (`advisor_work_events`, `compliance_decisions`,
  `reviewer_authority_events`, `exception_events`, `workflow_events`, tax `*_events`);
  `audit_events` — the administrative security log; mutable records (`annual_review_sessions`,
  `business_planning_profiles`).
- Timeline **adapters read source domains**; **source domains must not import the timeline
  projection**.
- Not every state transition creates a timeline event; timeline events are emitted **only** for
  approved durable events. Page renders and recomputed summaries **are not** events.
- **No second timeline-event table** may be created.
- Ordering (`occurred_at desc, stable-id desc`), stable ids, and redaction remain **owned by the
  timeline architecture**.

## Alternatives considered
1. **Full event sourcing** (event log as source of truth). Rejected: heavy, duplicates records,
   and invites fabricated/derived events.
2. **A dedicated per-composition event table** (e.g. a timeline table owned by a workspace).
   Rejected: a second event table fragments ordering/redaction and duplicates source data.

## Reasons for the decision
A projection over existing durable records gives the chronological view cheaply, keeps one owner
per fact, and avoids fabricating history — while `timeline_events` plus domain ledgers already
capture the durable events worth showing.

## Consequences
### Positive consequences
- Chronological view with no new event store; ordering/redaction centralized.
- Recomputed recommendations (no durable timestamp) are correctly excluded — no fabricated history.

### Negative consequences and tradeoffs
- Some older `timeline_events` lack an actor.
- Events that were never durably recorded cannot appear (by design).

## Enforcement
- Single `timeline_events` table (verified: `tests/test_platform_architecture.py`
  `test_activity_timeline_is_a_projection_no_second_event_table`).
- Projection: `app/services/activity_timeline/service.py` + adapters; source producers don't import
  it (`tests/test_activity_timeline.py`, `tests/test_platform_architecture.py`).
- Durable D.12 planning events use the shared `add_timeline_event` writer (no new table):
  `app/services/business_owner.py::_emit_planning_event`.

## Exceptions
None currently approved.

## Revisit conditions
If genuine event sourcing is ever required, it needs a superseding ADR defining the log as source
of truth, projections, and migration — not an incremental second event table.

## References
- `app/services/activity_timeline/service.py`; migration `3ca741f2d686_add_timeline_events.py`
- `docs/PLATFORM_ARCHITECTURE.md` §15 (Activity Timeline and audit architecture)
- `tests/test_activity_timeline.py`, `tests/test_platform_architecture.py`, `docs/EVENTS.md`, `docs/PHASE_D10*`
