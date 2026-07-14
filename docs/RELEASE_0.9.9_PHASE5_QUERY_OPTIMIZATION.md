# Release 0.9.9 Phase 5 — Query Optimization & N+1 Elimination

Four confirmed N+1 / full-scan hot paths were eliminated while preserving exact
output and authorization semantics (RC8/RC9 H15–H19; `PRODUCTION_ARCHITECTURE.md`
§23). No caching was introduced. No database schema change (relies on the Phase 4
indexes). Each optimized path has a regression test in
`tests/test_phase5_query_optimization.py`.

Query counts below were measured with a SQLAlchemy `before_cursor_execute`
counter on realistic fixtures.

## WP5.1 — `work_items()` authorization pushed into SQL

**Before:** loaded **all** tasks and **all** workflow steps, then filtered by the
caller's assignment set in Python — O(all rows).
**After:** the authorization scope (assigned task/person/household/step/workflow
ids) is pushed into the SQL `WHERE` (`OR` of `IN` predicates); non-`record.read_all`
callers read only their book — O(caller's book). `record.read_all` is unchanged.

**Correctness:** the scoped rows are exactly those the prior Python filter kept.
Tests prove (a) unrelated tasks never leak into a scoped result and (b) an advisor
without an assignment sees nothing (negative scope).

## WP5.2 — tax `staff_dashboard()` bulk intake details

**Before:** looped over every authorized return calling `intake_detail()`
(~7 queries each) → N×7.
**After:** a single `_bulk_intake_details(return_ids)` issues a fixed set of
`WHERE … IN (return_ids)` queries and assembles per-return details identical to
`intake_detail()`.

| Returns | Before (N+1) | After (bulk) |
|---|---|---|
| 4 | 28 queries | **7 queries** (constant in N) |

**Correctness:** `_bulk_intake_details([rid])[rid] == intake_detail(rid)` (exact
structural equality) and query-count is independent of N.

## WP5.3 — portal dashboard scope threading + dedicated narrow endpoints

**Before:** `dashboard()` recomputed `portal_scope()` up to ~4× (via
`client_tasks`, `portal_intakes`, `portal_returns`); and every narrow endpoint
(`/documents`, `/requests`, `/tasks`, `/notifications`, `/messages`) computed the
**entire** dashboard just to return one key.
**After:** one `portal_scope()` is threaded through the sub-calls, and each narrow
endpoint has a dedicated single-purpose function (`client_documents`,
`client_document_requests`, `client_notifications`, `client_threads`,
`client_tasks`). `dashboard()` composes those same functions, so its output is
unchanged.

| Endpoint | Before (full dashboard) | After (dedicated) |
|---|---|---|
| `/api/v1/portal/notifications` | 21 queries | **1 query** |

**Correctness:** each narrow function returns the same value as the corresponding
`dashboard()` key; portal isolation tests still pass.

## WP5.4 — `search_portfolios()` concentration filter

**Before:** with a `concentration` filter, called `get_person_portfolio()` for
**every** result row (~7 queries each, including an unused household portfolio).
**After:** `_largest_position_percents(person_ids)` bulk-loads accounts and
holdings in a bounded number of queries and computes the ratio with the **same**
`aggregate_portfolio` math, so the number is identical. An optional `limit`
(default off) bounds the returned rows.

| People | Before (N+1) | After (bulk) |
|---|---|---|
| 4 | 28 queries | **2 queries** |

**Correctness:** `_largest_position_percents([pid])[pid] ==
get_person_portfolio(pid)["largest_position_percent"]` and query-count is
independent of N.

## WP5.5 — pagination on `/activities` and `/tasks`

Both staff dashboards selected **all** rows. They now accept `limit` (default 100,
clamped 1–500) and `offset` (default 0, clamped ≥0) applied in SQL. Templates
render whatever page is passed; there was no total-count logic to update. With the
default limit and small datasets the response is unchanged.

## Scope notes

- N+1 elimination only; no query that was already efficient was rewritten
  (`production_dashboard` was already bulk and was used as the model for WP5.1).
- The portal per-client `return_detail`/`intake_detail` loops are bounded by the
  client's own (small) book; WP5.3 removes the duplicate `portal_scope()` work and
  the whole-dashboard waste on narrow endpoints.
- No change to authorization, audit, workflow, or security behavior; the full
  suite (including the portal-isolation and authorization tests) passes unchanged.
