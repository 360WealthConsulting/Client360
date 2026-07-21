# ADR-011 — Business Owner Planning as business-planning composition

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Business Owner Planning); Business Operations Owner
(Michael Shelton); Compliance Architecture (for surfaced compliance status).

## Context
The firm's tax-centered model for business owners spans many domains (entities, ownership, tax,
retirement, benefits, insurance, succession) plus advisor intelligence, work, compliance, timeline,
and annual review. Advisors needed one place to see it. The risk was building a planning *engine*
that calculates taxes, designs plans, sizes insurance, values businesses, or reinvents
recommendations.

## Decision
Business Owner Planning **must** be a **read-first business-planning composition** anchored to a
**person**:
- It **reaches businesses through validated ownership** (structured relationships), supporting
  zero, one, or multiple businesses; business-owner status derives **only** from an active
  ownership edge.
- It **composes** Tax, Retirement, Benefits, Insurance, Advisor Intelligence, Advisor Work,
  Compliance, Timeline, and Annual Review, each gated on its owning capability.
- It **must not** calculate taxes, design retirement plans, calculate insurance needs, value a
  business, or provide legal conclusions.
- It **must not** create a second recommendation engine (recommendations are reused and grouped
  only by durable `recommendation_type`).
- It **must not** merge with Annual Review (the two remain distinct surfaces; they may link).

Conceptually it exposes a **person-level** workspace (portfolio of businesses, ownership stake,
cross-domain summaries, missing information) and a **business-detail** view (deep single-business
sections). Route specifics live in the phase doc, not here.

## Alternatives considered
1. **A business-owner planning engine** that computes tax/plan/insurance/valuation outputs.
   Rejected: fabricated calculations (ADR-015), regulatory exposure, and out of scope.
2. **Merge Business Owner Planning into Annual Review.** Rejected: different orientation
   (business-planning vs meeting); merging would overload one surface and blur ownership.

## Reasons for the decision
Composition over validated ownership gives the full planning picture honestly — with "Not
available / Not tracked" where upstream data is absent — without a calculation engine or a second
recommender.

## Consequences
### Positive consequences
- One cross-domain business-planning surface; no duplicated logic; no fabricated calculations.
- Ownership is always structurally validated (never name-inferred).

### Negative consequences and tradeoffs
- Many upstream facts are absent (owner comp, tax content, policy purpose) and shown "Not
  available".
- Advisor Work/Compliance carry no business link, so those sections are person-scoped.

## Enforcement
- `app/services/business_owner.py` (`compose_person_workspace`, `compose_business_detail`,
  `business_in_scope`, `_group_recommendations` by `recommendation_type`, "Not available"
  placeholders); pure ownership read (ADR-014); one owned table `business_planning_profiles`
  (ADR-012).
- Tests: `tests/test_business_owner.py` (scope/enumeration, reuse-not-regenerate, no fabrication).

## Exceptions
The intentional `business_owner → annual_review` read (latest session) is downward composition,
allowed (ADR-001).

## Revisit conditions
If upstream domains model owner compensation / policy purpose / tax content, the corresponding
sections upgrade from "Not available" to live data — no ADR change required unless a calculation
capability is proposed (which needs a new ADR).

## References
- `app/services/business_owner.py`; `docs/PLATFORM_ARCHITECTURE.md` §17
- `tests/test_business_owner.py`, `docs/PHASE_D12_BUSINESS_OWNER_PLANNING_WORKSPACE.md`
