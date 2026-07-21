# Phase D.10 — Client and Household Activity Timeline

## Objective
A read-oriented, advisor-facing chronological timeline answering *"What has happened with
this client or household?"* by **unifying events already produced by existing Client360
domains** into one view — while each domain remains the authoritative source. The timeline
is a **projection**, not a new system of record.

## Architecture
```
Existing authoritative records
  ├── timeline_events        (tasks/notes/comms/imports/source-links/matching/filings…)
  ├── advisor_work_items / advisor_work_events   (D.9)
  └── compliance_reviews / compliance_decisions  (D.7)
        │  (read only, per-domain adapters)
        ▼
Activity Timeline projection  (app/services/activity_timeline/)
        ▼
Client Timeline / Household Timeline  (/people/{id}/timeline, /households/{id}/timeline)
```
**Dependency direction (enforced by test):** source domains never import the timeline; the
timeline reads source data and never mutates it; templates never construct domain events;
routes contain no event-classification policy. No workflow engine, no event-sourcing
framework, no domain-event bus.

## Projection model
`app/services/activity_timeline/`: `models.py` (`TimelineEvent`), `service.py`, and explicit
per-domain adapters (`adapters/domain_events.py`, `advisor_work.py`, `compliance.py`). Each
adapter reads its authoritative source, maps to `TimelineEvent`, applies redaction, provides
stable ids and deterministic timestamps and source links, and returns a bounded list. The
service merges, orders, resolves actor names once (batched), filters, and paginates. No giant
SQL union in the route; no reflection-based adapter.

## Event contract
`TimelineEvent` (presentation only — never replaces a domain model, never persisted):
`event_id, event_type, occurred_at, title, summary, person_id, household_id,
actor_principal_id, actor_display_name, source_domain, source_record_type, source_record_id,
source_url, severity, status, metadata, redacted, sort_key`. Deterministic and testable;
`to_dict()` never exposes `recommendation_snapshot` or `evidence_snapshot`.

## Included source domains
- **Domain events** (`timeline_events`) — the existing advisor-facing cross-domain stream
  (people/sources/imports/matching/communications/tasks/notes/filings). Shown fully.
- **Advisor Work** (`advisor_work_events` → `advisor_work_items`) — created/assigned/started/
  waiting/completed/cancelled/archived. One event per action (no duplicate current-status +
  transition).
- **Compliance** (`compliance_reviews` submitted; `compliance_decisions` recorded) — only
  durably-timestamped facts.

## Excluded source domains (and why)
- **Advisor Intelligence recommendations** — recomputed at render time with **no durable
  timestamp**; including them would fabricate history and emit new "events" on every recompute.
  Documented exclusion; no timestamps fabricated.
- **Reviewer Authority events** — firm-governance, not client-scoped (per the spec); a firm-level
  timeline is out of scope for D.10.
- **Compliance assignment / status changes** — not separately timestamped (only current status +
  the decisions ledger); not fabricated. Approval-blocked records no decision row → no event.
- **Person/household `updated_at`-derived events** — not projected (no fake history from
  current-state timestamps). Real person/household/source/match activity already flows through
  `timeline_events`.

## Stable event IDs
`domain:timeline_event:{id}`, `advisor_work:event:{event_id}`, `compliance:decision:{id}`,
`compliance:review:{id}:submitted`. Deterministic (never random at render time); the service
de-duplicates by `event_id`.

## Ordering & pagination
Deterministic **`(occurred_at desc, sort_key desc)`** where `sort_key` is the stable event id
(never timestamps alone). Each adapter fetches ≤ **500** most-recent rows (`PER_SOURCE_CAP`);
page size is bounded to ≤ **100** (`MAX_PAGE_SIZE`). Expected per-client event volume is small;
queries are bounded and indexed (`timeline_events.person_id/household_id`,
`advisor_work_items.person_id`, `compliance_reviews.person_id`).

## Authorization & scope
Base gate: **`timeline.read`** capability (granted to administrator/advisor/compliance/operations
— roles that already hold `client.read`) **plus** person/household record scope (the
`^/(people|households)/(\d+)` middleware RECORD_PATH covers these paths; the service also
re-checks `record_in_scope`). `timeline.read` is **not** a bypass around `advisor_work.read` or
`compliance.review.read`.

## Redaction (server-side, not template-only)
- Advisor-work event **notes** are redacted unless the principal holds `advisor_work.read`.
- Compliance decision **comments/exceptions** are redacted unless the principal holds
  `compliance.review.read`.
- A redacted event still shows its existence/title/status; its summary reads **"Additional
  details are restricted."** and its source link is withheld. The service never reveals the
  content the principal may not view.

## Client timeline
`GET /people/{person_id}/timeline` — reverse-chronological, with source-domain filter, date
range, and title/summary/actor search, pagination, authorized source links, an empty state, and
explicit "restricted" markers. Read-only: no mutation, no inline lifecycle actions, no bulk
actions. Client 360 gains an **"Activity timeline →"** link (no heavy preview; the existing
"Recent activity" already shows `timeline_events`).

## Household timeline
`GET /households/{household_id}/timeline` — household-level events plus events for current
members, each labeled with its person/household context. A single source row referencing both
person and household is one event (natural dedup). **Membership is taken from stored
person/household links only** — no historical membership windows are invented (conservative
rule; documented). The household detail page gains a "Activity timeline →" link.

## Relationship to the audit log
The timeline is **advisor-facing, curated, client/household-scoped, operationally meaningful,
and redacted** — it is **not** the administrative audit log (`/admin/audit`), which is a broader
security record under separate capability. The timeline is not a shortcut to it, and does not
replace it.

## Performance safeguards
Bounded per-source fetch (≤500) and page size (≤100); indexed person/household filters;
**batched actor-name resolution** (one `users` query — no N+1); no Advisor Intelligence
recomputation for history; no unbounded JSON processing; the Client 360 page gets a link, not a
second heavy query.

## Migration decision
**No timeline table, no timeline persistence, no new index, no backfill** — a pure read
projection. **One tiny migration** (`h2t3i4m5l6n7`, down `g1w2o3r4k5m6`) seeds only the
`timeline.read` capability into the four client-record-access roles. Upgrade/downgrade verified.
No fabricated history and no prospective history capture (the timeline records nothing).

## Testing strategy
`tests/test_activity_timeline.py` (12): projection contract + deterministic ids/ordering, adapter
mapping (domain/work/compliance), redaction + detail-exposure with source caps, person scope-first,
household member inclusion, filters/search/date-range/pagination, bounded page size, missing-actor
tolerance, route auth + render + no-mutation-controls + no-raw-JSON, and dependency direction. The
D.5 golden and all D.5–D.9 suites re-run green (AI/Rule-Catalog/compliance/advisor-work unchanged).

## Limitations & remaining technical debt
- Actor is resolved to a display name where a user id is available; some `timeline_events` carry no
  actor (rendered without one).
- Compliance transitions other than submit/decision are not projected (not durably timestamped).
- Advisor Intelligence recommendations are excluded until/if a durable occurrence timestamp exists.
- Household historical-membership windows are not modeled (conservative current-membership rule).
- The append-only trigger idiom would still benefit from the deferred D.8A migration helper.
