# ADR-042 — Enterprise Read Surface Adoption: adopt projections into read surfaces incrementally, with graceful fallback; the write side stays authoritative

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Read Models / Projections); Reliability / Operations (governance);
Security / Authorization (RBAC ownership); Business Operations Owner (Michael Shelton). Authorized
compliance reviewer: Not yet designated.

## Context
Phase D.36 (ADR-041) built the Projection Engine and 12 disposable, event-derived read models, but
explicitly deferred *adopting* them: every read surface still recomputed on each read (heavy joins, N+1
loops, in-Python aggregation, and a 3-source activity fan-in). D.37 adopts the projections into
user-facing read surfaces so those reads can be served from precomputed read models — **without changing
any business behavior**. Two constraints shape the design:

1. **Projections are dark-launched and disposable.** By default they are unbuilt (the incremental tick
   is gated OFF), so an adopted read must behave identically to today until an operator enables and
   rebuilds them. Projections also only carry events emitted since D.35 — they are not a complete
   historical record until rebuilt.
2. **Projections are references-only and carry no record-scope anchor.** A read model holds ids /
   statuses / timestamps, not the per-record scope needed to enforce record-level RBAC. Serving a
   record-scoped read from a projection would bypass scope.

## Decision
Phase D.37 introduces **read-surface adoption** (`app/services/projections/adoption.py`): a thin,
read-only helper that an adopted read consults before it queries the authoritative table. **The
authoritative services remain the sole mutation layer, and every adopted read falls back to the
authoritative read whenever the projection is not both usable and safe.**

- **Why writes remain authoritative.** D.37 changes READS ONLY. No adopted surface mutates a projection
  or an authoritative table; the domain services + their tables/ledgers stay the sole system of record.
  D.37 introduces **no CQRS write model, no second event bus, no second event log, no event sourcing, no
  shadow business logic, no duplicate mutation service, and no synchronous producer dependency.** The
  outbox remains the sole event bus and event log.
- **Why adoption is incremental.** Twelve read surfaces are adopted (Activity Timeline, Opportunity
  Pipeline, Compliance Queue, Tax / Insurance / Benefits dashboards, Operational Task Lists, Exception
  Dashboard, Project Dashboard, Document Dashboard, Household Summary, People Summary), but only their
  firm-level COUNT / feed reads — the safe, display-value-free reads. Join-heavy *display* reads keep
  their authoritative query; adopting the counts first is the low-risk first step and can be extended
  later behind the same helper.
- **Why fallbacks exist.** `adoption.count(...)` / `adoption.recent_feed(...)` return the projection
  value ONLY when `should_use` holds — the projection is healthy AND its lag is within the freshness
  threshold; otherwise they return `None` and the caller runs the unchanged authoritative read. Because
  projections are unbuilt by default, adopted reads fall back to authoritative by default, so behavior
  is unchanged until an operator opts in (enable + rebuild).
- **Why RBAC is never bypassed.** A projection is served ONLY on the firm-wide (`record.read_all`)
  path. A record-scoped principal always receives the authoritative, scoped read — the references-only
  projection carries no scope anchor, so it is never used to answer a scoped read. Capability, runtime,
  and policy checks on each surface are untouched.
- **Governance + diagnostics.** Adoption governance (`governance.validate_adoption`) detects a
  projection available but unused, an endpoint still reading authoritative, mixed authoritative /
  projection reads (a read-model table queried directly, bypassing the helper), a projection bypass (an
  adoption call without a fallback), duplicate query implementations, and a projection stale beyond
  threshold. Diagnostics (`adoption.adoption_diagnostics`) report projection usage (reads vs
  fallbacks), per-target freshness (lag), endpoint latency proxy (joins avoided), and query reduction,
  surfaced at `GET /projections/adoption` (reusing `observability.audit`).

## Alternatives considered
1. **Switch reads onto projections unconditionally (no fallback).** Rejected: projections are
   dark-launched and incomplete until rebuilt; an unconditional switch would change behavior and could
   serve empty/partial data.
2. **Serve projections to record-scoped principals too.** Rejected: references-only rows carry no scope
   anchor, so this would bypass record-level RBAC (ADR-004).
3. **Adopt the join-heavy display reads now.** Deferred: display reads need names/titles the
   references-only projections do not carry; adopting the counts first is the safe, behavior-preserving
   step. Enrichment or display adoption is a later, separately-reviewed change.
4. **A feature flag per surface instead of health/freshness gating.** Rejected: the health + lag gate is
   self-describing and fails safe (unbuilt → fallback) without per-surface configuration drift.

## Reasons for the decision
Adoption must deliver projection-backed reads without changing behavior, without bypassing RBAC /
runtime / policy, and without introducing a write model, a second event log, or shadow logic. Gating on
health + freshness with an automatic authoritative fallback, and serving projections only on the
firm-wide path, achieves exactly that: identical behavior by default, a query-reduced read once an
operator enables projections, and record-level scope preserved in every case. This preserves
ADR-004/013/039/040/041.

## Consequences
### Positive consequences
- 12 read surfaces can be served from precomputed read models once projections are enabled + rebuilt,
  avoiding heavy joins / N+1 loops / the activity fan-in (9 authoritative joins avoided across the
  adopted set). Adoption usage, fallback usage, freshness, and query reduction are observable; new
  analytics metrics expose projection-backed reads, fallbacks, and adoption coverage. Governance keeps
  adoption honest (no bypass, no mixed reads, no stale-serving, no duplicate queries).

### Negative consequences and tradeoffs
- Until projections are enabled + rebuilt, every adopted read falls back to authoritative — no
  performance gain by default (by design; safe).
- Only firm-level COUNT / feed reads are adopted; record-scoped and join-heavy display reads keep their
  authoritative query, so the query-reduction win is bounded in this phase.
- Adoption usage counters are in-process (per worker), so the usage view is per-instance, not
  cluster-global.

## Enforcement
- `app/services/projections/adoption.py` (helper: `should_use`, `count`, `recent_feed`,
  `adoption_diagnostics`, `ADOPTION_TARGETS`, `ADOPTION_MODULES`, `ADOPTION_INVENTORY`);
  `app/services/projections/governance.py::validate_adoption`; adoption sites in
  `app/services/analytics/sources.py` and `app/services/activity_timeline/service.py`; analytics metrics
  in `metrics.py`; route `GET /projections/adoption` in `app/routes/projections.py` (reusing
  `observability.audit`). No migration (D.37 is code-only; migration head stays `zd3e4f5a6b7c`). The
  domain services, their tables/ledgers, the outbox, the event/projection model, the runtime / policy /
  orchestration engines, RBAC, and infrastructure config are untouched. Tests:
  `tests/test_read_surface_adoption.py`; manifest / platform-architecture / route-count / ADR-count
  guards updated.

## Exceptions
Projections are served only on the firm-wide (`record.read_all`) path; `administrator` / `record.read_all`
scope bypass remains as defined by ADR-004. Only firm-level COUNT / feed reads are adopted in this phase;
join-heavy display reads keep their authoritative query. Projection serving requires an operator to
enable (`PROJECTIONS_ENABLED`) and rebuild — otherwise reads fall back to authoritative.

## Revisit conditions
Adopting join-heavy display reads (requiring enrichment/display values in projections), serving
projections to record-scoped principals (requiring a record-scope anchor in the read model), moving
adoption usage counters to a shared store, or enabling projections in production by default would each
warrant a new or superseding ADR.

## References
- `app/services/projections/adoption.py`, `app/services/projections/governance.py`,
  `app/services/analytics/sources.py`, `app/services/analytics/metrics.py`,
  `app/services/activity_timeline/service.py`, `app/routes/projections.py`
- `docs/READ_SURFACE_ADOPTION.md`, `docs/PROJECTION_USAGE_GUIDE.md`, `docs/READ_OPTIMIZATION.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_read_surface_adoption.py`; relates to ADR-004, ADR-013, ADR-039, ADR-040, ADR-041
