# ADR-007 — Advisor Work is work management, not a workflow engine

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Advisor Work); Business Operations Owner (Michael Shelton).

## Context
Recommendations need to become actionable, trackable work. The risk is scope creep into a generic
workflow/orchestration engine — automatic work creation from any observation, background state
machines, and hidden automation — which would be unpredictable, hard to audit, and outside the
firm's operational model.

## Decision
Advisor Work **owns** work items and their lifecycle, and **must** stay a work-management domain,
not a workflow engine:
- Work items are created **explicitly** — by a user action or by idempotent
  `create_from_recommendation` (**at most one OPEN item per recommendation/person/rule**).
- Items **must** anchor to a person/household.
- Status transitions follow an **explicit allowed-transition map** (new → assigned →
  in_progress/waiting → completed/cancelled/archived); there **must be no** background automation
  or hidden orchestration implied by a transition.
- Advisor Work **must not** silently create work from page observations or missing-information.
- Composition workspaces **must** link to work (and offer explicit "create from recommendation"
  where supported) rather than embedding a work editor or auto-creating items.
- Completion records operational activity only; it **must not** alter the underlying
  recommendation, its evidence, or its id.

## Alternatives considered
1. **A generic workflow engine** with triggers/automation. Rejected: unpredictable, hard to audit,
   and unnecessary for the firm's advisor-work model.
2. **Auto-creating work from missing-information/observations.** Rejected: produces noise and
   unowned items; creation must be explicit and idempotent.

## Reasons for the decision
Explicit, idempotent, person-anchored work with a small transition map is auditable and matches
how advisors actually work — without a hidden engine.

## Consequences
### Positive consequences
- Predictable, auditable work; no runaway automation; no duplicate OPEN items per recommendation.
- Completion never mutates the recommendation.

### Negative consequences and tradeoffs
- Work items carry no business link today (person/household only) — a documented limitation for
  business-owner contexts.
- Some cross-domain "should this become work?" decisions stay manual (by design).

## Enforcement
- `app/services/advisor_work.py`: `_TRANSITIONS` map; `create_from_recommendation` (idempotent);
  append-only `advisor_work_events` (migration `g1w2o3r4k5m6`, mutation-blocking trigger).
- Separate from the legacy `/work` + `work.*` system. Tests: `tests/test_advisor_work.py`.
- Composition reuse (link, not duplicate): `annual_review`, `business_owner` (person-scoped
  `person_work`).

## Exceptions
None currently approved. No automatic work creation exists.

## Revisit conditions
If business-linked work is required, add a business anchor via a new ADR/migration. Any automation
proposal requires a superseding ADR and explicit compliance/operational review.

## References
- `app/services/advisor_work.py`; migration `g1w2o3r4k5m6_advisor_work_management.py`
- `docs/PLATFORM_ARCHITECTURE.md` §13 (Advisor Work architecture)
- `tests/test_advisor_work.py`, `docs/PHASE_D9*`
