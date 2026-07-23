# Read Surface Adoption (Phase D.37)

D.36 built the Projection Engine and 12 disposable, event-derived read models (`rm_*`). D.37 **adopts**
those projections into user-facing read surfaces so reads can be served from precomputed read models —
**without changing any business behavior**. This document is the adoption inventory + the rules every
adopted read follows.

See also: [`ADR-042`](adr/ADR-042-read-surface-adoption.md), [`PROJECTION_USAGE_GUIDE.md`](PROJECTION_USAGE_GUIDE.md),
[`READ_OPTIMIZATION.md`](READ_OPTIMIZATION.md), [`READ_MODEL_ARCHITECTURE.md`](READ_MODEL_ARCHITECTURE.md).

## What stays true (invariants)

- **The write side remains authoritative.** D.37 changes READS ONLY. The domain services + their
  tables/ledgers stay the sole mutation layer and system of record. No adopted read mutates anything.
- **The outbox remains the sole event bus and event log.** No second bus, no second log, no event
  sourcing, no CQRS write model, no shadow business logic, no duplicate mutation service, no synchronous
  producer dependency.
- **Projections stay disposable.** Adoption never makes a business record depend on a projection; a
  read model can still be dropped and rebuilt from events at any time.
- **RBAC / runtime / policy are never bypassed.** A projection is served ONLY on the firm-wide
  (`record.read_all`) path; a record-scoped principal always gets the authoritative, scoped read. Every
  surface keeps its capability, runtime, and policy checks.
- **Behavior is unchanged by default.** Projections are dark-launched (unbuilt); adopted reads fall
  back to the authoritative read until an operator enables (`PROJECTIONS_ENABLED`) and rebuilds.

## How an adopted read works

Every adopted read consults `app/services/projections/adoption.py` before touching the authoritative
table:

```
adoption.count(projection_id, principal, firm_level=..., status_col=..., status_in=..., ...)
  → int   when the projection is usable (healthy + fresh) and this is a firm-wide read
  → None  otherwise  → the caller runs its unchanged authoritative query
```

`should_use(projection_id, principal, firm_level=...)` returns `True` only when **all** hold:

1. the projection state is `healthy`, and
2. its lag ≤ `FRESHNESS_LAG_THRESHOLD` (100 events behind head), and
3. for a record-scoped read, the principal has `record.read_all` (firm-wide).

Any failure (unbuilt, lagging, stale, scoped principal, missing table, exception) → fall back. The
helper never raises.

## Adoption inventory (12 surfaces)

| Read surface | Projection | Adopted read (`app/services/...`) | Authoritative read (fallback) | Joins avoided |
|---|---|---|---|---|
| People Summary | `people.summary` | `analytics.client_count` | `COUNT(people)` | 0 |
| Household Summary | `household.summary` | `analytics.household_count` | `COUNT(households)` | 0 |
| Opportunity Pipeline | `opportunity.pipeline` | `analytics.projection_open_opportunity_count` | `COUNT(opportunities WHERE status='open')` | 0 |
| Operational Task Lists | `operations.tasks` | `analytics.open_operational_task_count` | `COUNT(operational_tasks WHERE open)` | 0 |
| Project Dashboard | `operations.projects` | `analytics.active_project_count` | `COUNT(projects WHERE active)` | 0 |
| Compliance Queue | `compliance.queue` | `analytics.projection_open_compliance_count` | `COUNT(compliance_reviews WHERE open)` | 0 |
| Tax Dashboard | `tax.pipeline` | `analytics.projection_tax_return_count` | `COUNT(tax_engagement_returns)` (4-join dashboard) | 4 |
| Insurance Dashboard | `insurance.pipeline` | `analytics.projection_insurance_case_count` | `COUNT(insurance_cases)` (+N+1) | 1 |
| Benefits Dashboard | `benefits.enrollment` | `analytics.projection_benefits_enrollment_count` | `COUNT(benefit_enrollments)` | 1 |
| Document Dashboard | `document.status` | `analytics.document_count` | `COUNT(documents WHERE status)` | 0 |
| Exception Dashboard | `exception.dashboard` | `analytics.projection_open_exception_count` | `COUNT(exceptions WHERE open)` (+Python agg) | 0 |
| Activity Timeline | `activity.feed` | `activity_timeline.recent_activity_feed` | 3-source fan-in + Python merge | 3 |

Adoption sites live in exactly two modules (`ADOPTION_MODULES`): `app/services/analytics/sources.py`
and `app/services/activity_timeline/service.py`. Governance scans these.

**Scope:** only firm-level COUNT / feed reads are adopted in D.37 (the safe, display-value-free reads).
Record-scoped reads and join-heavy *display* reads keep their authoritative query — projections are
references-only and carry no names/titles or scope anchor.

## Remaining authoritative reads

- All record-scoped reads (any principal without `record.read_all`) — always authoritative.
- All join-heavy display reads (dashboards that render names/titles/detail rows).
- Every read when projections are unbuilt / lagging / stale — i.e. the default.

## Governance

`governance.validate_adoption()` (surfaced at `GET /projections/adoption`) detects:

- **projection available but unused** — an active projection with no adoption target.
- **endpoint reading authoritative** — an adoption target with no adoption site in the scanned modules.
- **mixed authoritative/projection reads** — an `rm_*` table queried directly in an adoption module
  (bypassing the helper).
- **projection bypass** — an `adoption.count` / `adoption.recent_feed` call with no fallback guard.
- **duplicate query implementations** — two targets sharing a read function.
- **projection stale beyond threshold** — an adopted, built projection lagging past `LAG_THRESHOLD`.

## Diagnostics

`adoption.adoption_diagnostics()` reports: usage (projection-backed reads vs fallbacks + read %),
per-target freshness (health + lag + usable), the adopted surface count, joins avoided per target, and
the total joins avoided. Analytics metrics expose `projection_backed_read_count`,
`projection_fallback_count`, `projection_adoption_pct`, `adopted_read_surface_count`, plus the six
projection-backed domain counts.

## Enabling projection-backed reads (operator)

1. Enable the incremental tick (`PROJECTIONS_ENABLED=true`) or run an on-demand rebuild.
2. Rebuild the projections: `POST /projections/rebuild {projection_id}` for each (deterministic).
3. Confirm health/lag: `GET /projections/health` and `GET /projections/adoption`.
4. Firm-wide reads now serve from projections; scoped reads stay authoritative. Behavior is otherwise
   unchanged. To roll back, reset the projections — reads fall back automatically.
