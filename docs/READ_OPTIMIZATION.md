# Read Optimization (Phase D.37)

The before/after read-cost analysis behind read-surface adoption. Adoption replaces an authoritative
read with a projection read only when the projection is healthy + fresh + firm-wide; otherwise the
authoritative read runs unchanged. This doc records what each adopted read costs before and after, and
how to measure the improvement.

See also: [`READ_SURFACE_ADOPTION.md`](READ_SURFACE_ADOPTION.md), [`PROJECTION_USAGE_GUIDE.md`](PROJECTION_USAGE_GUIDE.md).

## Method

For each adopted read we compare the authoritative query the surface would run against the projection
query the helper runs. The measurable dimensions:

- **query count** — statements per render (adoption keeps it at 1 per adopted read).
- **join count** — joins the projection read avoids vs the authoritative read.
- **execution shape** — index scan of a smaller `rm_*` table vs scan/aggregation of the authoritative
  table (and, for some surfaces, Python-side aggregation or an N+1 loop).
- **rows scanned** — the `rm_*` table holds one row per subject (post-D.35 events), typically ≤ the
  authoritative table.
- **projection latency** — projection health/lag (the read is only served when lag ≤ 100).

`ADOPTION_INVENTORY` in `app/services/projections/adoption.py` is the machine-readable source of this
table; `adoption.adoption_diagnostics()` reports the live per-target usage + joins avoided.

## Before / after

| Surface | Authoritative read (before) | Projection read (after) | Joins avoided | Notes |
|---|---|---|---|---|
| People Summary | `COUNT(people)` | `COUNT(rm_people_summary)` | 0 | precomputed count |
| Household Summary | `COUNT(households)` | `COUNT(rm_household_summary)` | 0 | precomputed count |
| Opportunity Pipeline | `COUNT(opportunities WHERE status)` | `COUNT(rm_opportunity_pipeline WHERE status)` | 0 | avoids scanning full pipeline |
| Operational Tasks | `COUNT(operational_tasks WHERE status)` | `COUNT(rm_operational_tasks WHERE status)` | 0 | precomputed open-task count |
| Project Dashboard | `COUNT(projects WHERE status)` | `COUNT(rm_projects WHERE status)` | 0 | precomputed active-project count |
| Compliance Queue | `COUNT(compliance_reviews WHERE open)` | `COUNT(rm_compliance_queue WHERE decided_at IS NULL)` | 0 | precomputed queue depth |
| Tax Dashboard | `COUNT(tax_engagement_returns)` (4-join dashboard) | `COUNT(rm_tax_pipeline)` | 4 | authoritative tax dashboard joins 4 tables |
| Insurance Dashboard | `COUNT(insurance_cases)` (+per-case N+1) | `COUNT(rm_insurance_pipeline)` | 1 | authoritative dashboard N+1 over cases |
| Benefits Dashboard | `COUNT(benefit_enrollments)` | `COUNT(rm_benefits_enrollment)` | 1 | avoids enrollment→employment join |
| Document Dashboard | `COUNT(documents WHERE status)` | `COUNT(rm_document_status WHERE status)` | 0 | precomputed document count |
| Exception Dashboard | `COUNT(exceptions WHERE open)` (+Python aging/SLA/trend) | `COUNT(rm_exception_dashboard WHERE status)` | 0 | authoritative dashboard aggregates in Python |
| Activity Timeline | 3-adapter fan-in + Python merge/sort | `SELECT rm_activity_feed ORDER BY id` | 3 | avoids the 3-source activity fan-in |

**Total authoritative joins avoided across the adopted set: 9.**

## Honest bounds

- The biggest wins are the surfaces whose authoritative read is join-heavy or does Python-side work
  (Tax, Insurance, Exception, Activity). For the plain-`COUNT` surfaces both sides are a `COUNT`; the
  projection's win is a smaller, purpose-built table and decoupling the read from the authoritative
  table, not a change in query class.
- Only firm-level COUNT / feed reads are adopted. Record-scoped reads and join-heavy *display* reads
  (rendering names/titles/detail rows) keep their authoritative query — the references-only projections
  carry no display values. Adopting those is a later, separately-reviewed step.
- Improvement is realized only when projections are enabled + rebuilt; by default every adopted read
  falls back to authoritative (no regression, no gain).

## Measuring in practice

- **Query reduction / usage:** `GET /projections/adoption` → `diagnostics.usage`
  (`projection_read_pct`, `reads`, `fallbacks`) and `diagnostics.joins_avoided_total`. Analytics metrics
  `projection_backed_read_count`, `projection_fallback_count`, `projection_adoption_pct`.
- **Freshness / projection latency:** `GET /projections/health` and per-target `lag` in the adoption
  diagnostics (a read is only served when lag ≤ 100).
- **Regression guard:** `tests/test_read_surface_adoption.py` asserts that with projections unbuilt
  every adopted read returns the identical authoritative value (behavior unchanged), and that a
  rebuilt+healthy firm-wide read serves from the projection while a scoped principal stays authoritative.
