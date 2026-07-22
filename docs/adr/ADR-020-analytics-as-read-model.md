# ADR-020 — Enterprise Analytics as a deterministic read-model

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Analytics / Firm Intelligence); Business Operations Owner
(Michael Shelton — executive reporting requirements).

## Context
Client360 had operational systems (clients, pipeline, campaigns, referrals, compliance, tax,
benefits, insurance, portfolio) but no executive insight layer — no KPIs, scorecards, trends,
targets, or firm intelligence. The audit confirmed no Analytics domain existed. Analytics must
provide firm-wide and advisor-book metrics **without** becoming a source of truth, without
duplicating business logic, without fabricating history, and without breaking the D.5 golden.

## Decision
Introduce Analytics as a **deterministic read-model**. It **owns no business data** and is
**never a source of truth**.
- Metrics are computed deterministically by (a) **composing existing principal-scoped domain
  reports** (opportunity/bizdev/insurance/benefits/tax) and (b) running **bounded, scope-filtered
  COUNT/SUM aggregates** via the shared `accessible_person_ids` primitive. Analytics re-implements
  **no business logic** and **never writes** source data. All source reads live in one module
  (`app/services/analytics/sources.py`).
- **Scope:** `accessible_person_ids` is the book-scope primitive — `None` = firm-wide (only
  reached with `record.read_all`), a set = the advisor's book, empty = zero. **Executive/firm-wide
  and revenue metrics require `analytics.executive`** and are withheld server-side otherwise
  (value `None`, `restricted: true`) — restricted ≠ missing (ADR-005).
- Analytics persists **only** analytics-specific data: `analytics_targets` (targets/thresholds),
  `analytics_snapshots` (prospective point-in-time captures), `analytics_dashboards` /
  `analytics_dashboard_widgets` (custom dashboards + visualization metadata). It stores
  **visualization metadata only** — no chart libraries.
- **Trends** come from accumulated snapshots and from timestamped source facts that genuinely have
  history (e.g. opportunity close dates). **No backfill** — there is no historical source to
  backfill from; snapshots accrue going forward (ADR-015). Trends never fabricate history.
- **Firm Intelligence** is deterministic (not AI) and a **dedicated service** — **NOT** registered
  into the D.5 Advisor Intelligence `_PRODUCERS` seam, so the D.5 golden and `advisor_intelligence.py`
  remain untouched.
- Analytics is **read-only** with respect to every consumed domain (Timeline, Advisor Work,
  Opportunity, Campaign, Referral, Compliance, Tax, Benefits, Insurance, Portfolio, People,
  Households, Organizations, Annual Review, Business Owner Planning).

## Alternatives considered
1. **A materialized analytics warehouse (ETL from every domain into fact/dimension tables).**
   Rejected: introduces a sync pipeline, staleness, and a second source of truth — disproportionate
   for the current scale and contrary to "owns no business data."
2. **Compute metrics by re-querying source tables directly, per metric.** Rejected: duplicates
   business logic and scattering of authorization; composing existing scoped reports + a single
   source-reading layer keeps scope/redaction coherent (ADR-002/ADR-013).
3. **Store computed metric values as authoritative.** Rejected: they would drift; metrics are
   computed on read; only prospective snapshots (explicitly point-in-time captures) are stored.

## Reasons for the decision
A read-model that composes existing scoped reports gives executive insight with correct
authorization, no drift, and no fabricated history — while preserving single ownership and the D.5
golden. Targets/snapshots/dashboards are genuinely analytics-owned config/derived data with no
other home.

## Consequences
### Positive consequences
- Deterministic firm-wide + book-scoped KPIs, scorecards, targets/variance, and firm intelligence.
- No business-data ownership, no drift, no fabricated history; D.5 untouched.
- New dashboards/metrics compose the registry cheaply.

### Negative consequences and tradeoffs
- Trends require snapshots to accrue (empty until captured); metrics without source timestamps have
  no back-history (by design).
- Metrics recompute per request (bounded); a future materialization could be added if needed.
- Some domain metrics (tax/insurance) return `None` when the principal cannot see that domain —
  intentionally (restricted ≠ missing).

## Enforcement
- `app/services/analytics/{sources,metrics,trends,targets,dashboards,intelligence,service}.py`;
  route `app/routes/analytics.py` (in-route capability enforcement — `/analytics` matches no
  middleware rule). Migration `m3d4e5f6a7b8` (4 tables + 5 capabilities); declared schema
  `app/database/analytics_tables.py` (registered).
- Executive gating in `metrics.compute_metric` / dashboard composition. One additive read
  (`portfolio.book_aum`).
- D.5 golden untouched (`advisor_intelligence.py` never imports analytics). Tests:
  `tests/test_analytics.py`; manifest/platform-architecture/route guards updated.

## Exceptions
None currently approved.

## Revisit conditions
A materialized analytics warehouse (if request volume demands it), scheduled automated snapshot
capture, or an external BI export integration would each warrant a new or superseding ADR.

## References
- `app/services/analytics/`, `app/routes/analytics.py`, `app/database/analytics_tables.py`
- migration `migrations/versions/m3d4e5f6a7b8_analytics_kpi_warehouse.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_analytics.py`; relates to ADR-001, ADR-002, ADR-005, ADR-013, ADR-015, ADR-018, ADR-019
