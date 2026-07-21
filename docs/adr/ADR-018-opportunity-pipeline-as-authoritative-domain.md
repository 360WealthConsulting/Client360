# ADR-018 — Opportunity & Pipeline as an authoritative domain

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Opportunity / Business Development); Business Operations
Owner (Michael Shelton — pipeline/business-development requirements). Compliance Architecture
consulted (pipeline surfaces compliance status display-only; no approval implied).

## Context
Client360 excelled at servicing existing clients but had **no business-development capability**
— no authoritative sales pipeline. The audit confirmed no Opportunity domain existed (grep hits
were incidental: the insurance case pipeline, an organization `prospect` status, referral
relationship-type labels). Business-development data (prospects, referrals, COIs, cross-sell,
forecasts) needed a first-class home that does not duplicate canonical People/Organizations,
does not become AI, and does not break the D.5 golden regression.

## Decision
Introduce a **new, first-class authoritative Opportunity & Pipeline domain**. It **owns** the
firm's sales pipeline (`opportunity_pipelines`, `opportunity_stages`, `opportunities`,
`opportunity_participants`, `opportunity_events`, `opportunity_activities`,
`opportunity_work_links`).
- Business-development data **must not** be stored inside Advisor Work, Annual Review, Business
  Owner Planning, Activity Timeline, Organizations, or People.
- An opportunity **references** canonical People / Households / Organizations by validated,
  nullable FK; it **must never** create them or infer ownership. Targets are optional (a raw
  prospect may have none).
- Pipelines and stages are **configurable**; business logic keys off
  `opportunity_stages.category` (open/won/lost/dormant/cancelled), **never** a hard-coded stage
  name.
- **Pipeline Intelligence** (aging, stalled, missing next action, proposal overdue, high-value
  dormant, missing discovery, referral concentration, advisor imbalance, closing forecast,
  capacity warnings) **must** be deterministic (not AI) and is a **dedicated service owned by
  the Opportunity domain** — it is **NOT** registered into the Advisor Intelligence
  `_PRODUCERS` seam, so the D.5 golden regression and `advisor_intelligence.py` remain
  untouched (byte-for-byte). Most pipeline signals are book-level and do not fit the per-client
  `get_client_signals` seam anyway.
- **Advisor Work may reference** an opportunity through the Opportunity-owned
  `opportunity_work_links` table; Advisor Work **must not** own an opportunity and its schema is
  not modified.
- **Activity Timeline** receives **approved** opportunity events only (created / qualified /
  proposal / won / lost / advisor reassigned) via the shared `add_timeline_event` writer — never
  a field edit, and **no second event table** (ADR-009).
- **Compliance** is display-only on the pipeline; no compliance approval is implied (ADR-008).
- **Annual Review** and **Business Owner Planning** gain **read-only** opportunity visibility via
  additive scoped reads (ADR-013), gated on `opportunity.view`; they never own pipeline data.
- **Microsoft 365** is **referenced, not duplicated**: an opportunity activity may point at an
  existing `timeline_events` row (source `microsoft`). No calendar/mail integration is added.

## Alternatives considered
1. **Register pipeline signals into the D.5 Advisor Intelligence seam.** Rejected: `test_registry_
   matches_golden` pins the global registry list unconditionally, so any new rule breaks the
   byte-for-byte D.5 golden invariant; and most pipeline signals are book-level, not per-client.
2. **Store opportunities as a lead flag/table on People or Organizations.** Rejected: violates
   single ownership (ADR-002) and cannot represent prospects that are not canonical People, or
   many-to-many targets.
3. **Add an `opportunity_id` column to `advisor_work_items`.** Rejected: Advisor Work is
   recommendation-shaped; an Opportunity-owned link table keeps Advisor Work unmodified and
   preserves ADR-007.

## Reasons for the decision
A dedicated source domain gives business development a proper home with its own capabilities,
scope, and reporting, while every ADR is preserved: single ownership, additive reads for
consumers, deterministic (non-AI) intelligence, no render-time mutation, no fabricated data, the
timeline as a projection, and the D.5 golden untouched.

## Consequences
### Positive consequences
- A first-class, authoritative sales pipeline with configurable stages, reporting, and
  deterministic pipeline intelligence.
- Consumers (Annual Review, Business Owner Planning) surface opportunities without owning them.
- Advisor Intelligence / D.5 golden completely unaffected.

### Negative consequences and tradeoffs
- Opportunity intelligence is not shown inside the per-client `get_client_signals` panel (it
  lives on the pipeline surface and in the composition sections); a future ADR could add a
  narrow per-client opportunity producer with a controlled golden registry update.
- `opportunity_events` is a CASCADE-deletable log (not append-only) so `opportunity.delete`
  works; security-relevant deletion is captured by the separate audit log.
- Advisor Work items still carry no direct business/opportunity anchor beyond the link table.

## Enforcement
- Domain: `app/services/opportunity/{service,reporting,intelligence}.py`,
  `app/routes/opportunity.py`, `app/database/opportunity_tables.py`, migration
  `k1o2p3p4t5y6_opportunity_pipeline.py` (7 tables + default pipeline/stages + 7 capabilities).
- Capabilities `opportunity.view/edit/delete/assign/close/report/forecast`; forecast is
  sensitive and server-side.
- D.5 golden untouched: `tests/test_intelligence_refactor_regression.py` stays green with no
  fixture change. Pipeline Intelligence is a separate module (not in `_PRODUCERS`).
- Consumer reuse via additive reads: `opportunities_for_person` / `_organization` / `_people`.
- Tests: `tests/test_opportunity.py`; manifest/platform-architecture updated and test-checked.

## Exceptions
None currently approved. The Annual Review / Business Owner opportunity sections are gated on
`opportunity.view` and omitted otherwise (restricted ≠ missing, ADR-005).

## Revisit conditions
Adding per-client opportunity signals to the D.5 seam (with a controlled golden registry update);
introducing a campaign/marketing domain; or giving Advisor Work a first-class opportunity anchor
— each would warrant a new or superseding ADR.

## References
- `app/services/opportunity/`, `app/routes/opportunity.py`, `app/database/opportunity_tables.py`
- migration `migrations/versions/k1o2p3p4t5y6_opportunity_pipeline.py`
- `docs/PLATFORM_ARCHITECTURE.md` (Opportunity domain), `docs/platform_architecture_manifest.yaml`
- `tests/test_opportunity.py`; relates to ADR-001, ADR-002, ADR-006, ADR-007, ADR-009, ADR-013
