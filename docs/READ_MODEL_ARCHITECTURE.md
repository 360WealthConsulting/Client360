# Read Model Architecture (Phase D.36)

The **Read Models & Projection Engine** (`app/services/projections/`) consumes the D.34/D.35 domain
events from the transactional outbox to build fast, query-optimized **read models**. Read models exist
only for querying/dashboards/analytics/timelines/reporting/search/AI. They change **no business
behavior**.

## The core rule: the write side is authoritative; read models are disposable

```
   Domain services (authoritative mutation layer)  ──writes──►  domain tables / ledgers  (system of record)
        │  publish domain FACTS (D.35)
        ▼
   Transactional outbox (outbox_events)  ── the sole event bus + the sole event log ──┐
                                                                                       │  READ (never mutate)
                                                                                       ▼
                                             Projection Engine  ──►  read-model tables (rm_*)  (DISPOSABLE)
                                                                     query surfaces (dashboards/analytics/…)
```

- **Write models remain authoritative.** Projections are strictly downstream, read-only consumers. No
  CQRS write model, no alternate business logic, no shadow domain service, no duplicate authoritative
  state, no new broker, no second event bus, no second event log, no event sourcing, no synchronous
  cross-service dependency.
- **Read models are disposable.** A read model holds only event-derived references/statuses/timestamps
  and no business logic. It may be `DELETE`d and rebuilt entirely from events at any time.
- **Replay rebuilds projections.** Applying the ordered outbox events to a projection is a pure,
  deterministic function; `rebuild` (truncate → replay) reconstructs the exact read model, and
  `validate` (rebuild twice, compare) proves determinism.
- **Projections never contain business rules and never read authoritative tables.** A projection copies
  event data into a query-optimized shape; a business rule or an authoritative read would make it a
  shadow source of truth. Governance forbids it.

## Components

| File | Responsibility |
|---|---|
| `definitions.py` | The declarative projection catalog + `apply(conn, event)` handlers (no business logic; touch only the read table). |
| `engine.py` | The runtime — process (incremental) / rebuild / reset / replay / validate / tick / stats / health / lag / size. Per-event failure isolation. |
| `registry.py` | Discovery / versioning / lifecycle / ownership / dependency graph / coverage. |
| `governance.py` | Read-only validation of the projection registry. |
| `diagnostics.py` | Per-projection health / lag / size / rebuild history + fleet health (read-only). |

## Read models (12)

| Projection | Read table | Consumes |
|---|---|---|
| People Summary | `rm_people_summary` | people.person_created / updated / identity_merged |
| Household Summary | `rm_household_summary` | households.household_created / membership_changed |
| Opportunity Pipeline | `rm_opportunity_pipeline` | opportunity.created / stage_changed / won / lost |
| Operational Tasks | `rm_operational_tasks` | operations.task_created / task_completed |
| Projects | `rm_projects` | operations.project_created / project_status_changed |
| Compliance Queue | `rm_compliance_queue` | compliance.review_opened / approval_granted / approval_denied |
| Tax Pipeline | `rm_tax_pipeline` | tax.engagement_created / return_status_changed / filing_submitted / filing_acknowledged |
| Insurance Pipeline | `rm_insurance_pipeline` | insurance.case_created / application_status_changed |
| Benefits Enrollment | `rm_benefits_enrollment` | benefits.enrollment_created / enrollment_status_changed |
| Document Status | `rm_document_status` | document.registered / status_changed / archived |
| Exception Dashboard | `rm_exception_dashboard` | exception.opened / resolved |
| Activity Feed | `rm_activity_feed` | `*` (every domain event) |

Each read row is derived purely from events (the natural id + statuses + event-sourced timestamps).
Display values (names/titles) are NOT stored — the events are references-only.

## Persistence

- `projection_definitions` — the discoverable registry (owner, subscribed events, schema version,
  rebuild strategy, dependencies, status).
- `projection_state` — the runtime checkpoint + health (last processed outbox event, lag, counters,
  rebuild/replay history, validation).
- 12 `rm_*` read-model tables — disposable, rebuildable.

No second event log is added — the outbox is the event log; projections only read it.

## Runtime

The incremental tick (`engine.tick`) is a gated scheduler job (`PROJECTIONS_ENABLED`, dark-launched
off). Read models are always rebuildable on demand (`/projections/rebuild`); nothing depends on a read
model until a read surface adopts one, so behavior is unchanged by default. See
`docs/PROJECTION_REBUILD_GUIDE.md`.
