# ADR-041 — Enterprise Read Models & Projection Engine: disposable, event-derived read models; the write side remains authoritative

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Read Models / Projections); Reliability / Operations (governance);
Security / Authorization (RBAC ownership); Business Operations Owner (Michael Shelton). Authorized
compliance reviewer: Not yet designated.

## Context
Phases D.34/D.35 completed a governed, references-only domain-event model over the transactional outbox,
and the major business domains now publish domain facts. Meanwhile a read-surface audit found that
**no read-model / materialized-view / projection tables exist** — the timeline, analytics, reporting,
dashboards, and per-domain pipeline reads all recompute on every read (heavy joins, N+1 loops, and
in-Python aggregation: exception dashboard, tax/insurance pipelines, opportunity pipeline, activity
timeline, per-client tiles). D.36 begins consuming the events to build fast, query-optimized read
models, without changing any business behavior.

## Decision
Phase D.36 introduces an **Enterprise Read Models & Projection Engine** (`app/services/projections/`):
a generalized framework that consumes the D.34/D.35 domain events from the outbox and projects them into
disposable read-model tables. **The write side always remains authoritative.**

- **Why write models remain authoritative.** The domain services + their tables/ledgers are the sole
  mutation layer and the sole system of record. Projections are strictly downstream, read-only
  consumers. D.36 introduces **no CQRS write model, no alternate business logic, no shadow domain
  service, no duplicate authoritative state, no new broker, no second event bus, no second event log,
  no event sourcing, and no synchronous cross-service dependency.** The outbox remains the sole event
  bus and the sole event log.
- **Why read models are disposable.** A read model holds no authoritative state and no business logic —
  only event-derived references/statuses/timestamps. It may be `DELETE`d and rebuilt entirely from the
  events at any time. Nothing depends on a read model until a read surface explicitly adopts one, and
  even then the authoritative record is unaffected.
- **Why replay rebuilds projections.** The outbox is the authoritative, ordered event log; applying its
  events to a projection is a pure, deterministic function. So a full rebuild (truncate → replay every
  event) reconstructs the exact read model, and a validation (rebuild twice, compare) proves
  determinism. Recovery from a bad deploy, a schema change, or corruption is: reset + replay.
- **Why projections never contain business rules.** Business decisions live in the runtime/policy
  engines and the domain services; a projection only copies event data into a query-optimized shape. A
  projection that embedded a rule, or read an authoritative table, would become a shadow source of truth
  and could diverge from the write side. Governance forbids it (a projection may touch only its
  read-model table + the outbox).
- **Framework.** Each projection declares id, owner, subscribed event types, schema version, rebuild
  strategy, dependencies, and status; the engine tracks health, last processed event, lag, and
  rebuild/replay history. It supports full rebuild, incremental processing, reset, replay, validation,
  statistics, diagnostics, health, and a dependency graph. Twelve read models ship (People/Household
  summary, Opportunity/Operational-task/Project/Compliance/Tax/Insurance/Benefits pipelines, Document
  Status, Exception Dashboard, Activity Feed).
- **Runtime is dark-launched + failure-isolated.** The incremental tick is gated OFF by default (read
  models are always rebuildable on demand); projection failures are isolated per event and never affect
  a business transaction. Governance/diagnostics/analytics + the `/projections` surface reuse the
  existing `observability.*` capabilities (no RBAC changes).

## Alternatives considered
1. **CQRS with a separate write model.** Rejected: the domain services are already the authoritative
   write side; D.36 is read-only projection, not a write-side rewrite.
2. **Switch existing read surfaces onto projections now.** Deferred: D.36 is additive — it builds the
   projections; adopting them in the timeline/analytics/reporting surfaces is a separate, later step
   (and a governance check guards a projection from reading authoritative tables once adopted).
3. **Store the read models by re-querying authoritative tables (a cache).** Rejected: that couples read
   models to the write side and risks drift; building purely from the ordered event log keeps them
   deterministic and disposable.
4. **A second event store / event sourcing.** Rejected: the outbox is the single event log; projections
   read it, they do not fork it.

## Reasons for the decision
Read models must be fast, disposable, and deterministically rebuildable, without a CQRS write model, a
second event log, event sourcing, shadow state, or any change to authoritative behavior. Building purely
from the ordered outbox log — with projections that hold no business rules and never read authoritative
tables — delivers query-optimized reads while keeping the write side the sole source of truth, and
preserves ADR-013/039/040.

## Consequences
### Positive consequences
- 12 disposable read models consume 100% of the domain-event contracts (the Activity Feed consumes
  every event); each is rebuildable + deterministically replayable. Heavy read-time aggregations gain a
  precomputed target. Analytics/observability expose projection health/lag/rebuilds; governance keeps
  the model honest (no owner/subscriber gaps, no drift, no lag, no authoritative reads).

### Negative consequences and tradeoffs
- Read models built from references-only events carry ids/statuses/timestamps, not display values
  (names/titles) — resolving those stays a presentation-layer concern (or a future enrichment).
- The incremental tick is dark-launched; until enabled (or an on-demand rebuild), read models are
  unbuilt. Nothing depends on them yet, so this is safe.
- Twelve read-model tables add schema surface (all disposable, all `rm_`-prefixed).

## Enforcement
- `app/services/projections/{engine,definitions,registry,governance,diagnostics,common}.py`; the
  pure-data seed `app/database/projection_seed.py`; schema `app/database/projection_tables.py` (12 read
  tables + 2 registry tables), registered in `schema.py`; `db.py` exposes the registries; migration
  `migrations/versions/zd3e4f5a6b7c_read_model_projections.py`. Routes `app/routes/projections.py`
  (`/projections`, reusing `observability.*`). Scheduler tick `app/jobs/scheduler.py` +
  `app/config.py::projections_enabled` (gated off). Analytics metrics (`sources.py`/`metrics.py`).
  Projection modules registered in `source_producer_modules`. The domain services, their tables/ledgers,
  the outbox, the event model, the runtime/policy/orchestration engines, RBAC, and infrastructure config
  are untouched. Tests: `tests/test_projections.py`; manifest / platform-architecture / route-count /
  ADR-count guards updated.

## Exceptions
The incremental tick is dark-launched (gated with `PROJECTIONS_ENABLED`). Existing read surfaces are not
switched onto projections in this phase (additive). `administrator`/`record.read_all` scope bypass
remains as defined by ADR-004.

## Revisit conditions
Adopting a projection in a read surface (the surface must then stop querying the authoritative table for
that data), adding a projection that requires enrichment from an authoritative table, or introducing a
durable async projection worker would each warrant a new or superseding ADR.

## References
- `app/services/projections/*`, `app/routes/projections.py`, `app/database/projection_tables.py`,
  `app/database/projection_seed.py`, migration `migrations/versions/zd3e4f5a6b7c_read_model_projections.py`,
  `docs/READ_MODEL_ARCHITECTURE.md`, `docs/PROJECTION_ENGINE.md`, `docs/PROJECTION_GOVERNANCE.md`,
  `docs/PROJECTION_REBUILD_GUIDE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_projections.py`; relates to ADR-013, ADR-039, ADR-040
