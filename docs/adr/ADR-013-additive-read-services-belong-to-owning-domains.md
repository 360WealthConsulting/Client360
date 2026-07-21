# ADR-013 — Additive reads belong to owning domains

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (each owning domain).

## Context
Composition layers frequently need a person- or business-scoped view that an owning domain does not
yet expose (e.g. "this person's open work", "this business's tax engagements"). The tempting
shortcut is to query the owning domain's tables directly inside the composition workspace, or to
fetch a broad list and filter locally — both of which fork query semantics and authorization.

## Decision
Person-scoped and business-scoped reads needed by composition layers **must** be added as
**additive read functions on the owning domain's service**, not implemented as local filters or
raw table queries inside composition workspaces.
- The owning domain **controls** query semantics and authorization.
- Consumers **must not** reproduce the owning domain's policy.
- Additive reads **must** leave existing functions **behaviorally unchanged**, **must** avoid
  mutation (ADR-014), and **must** be bounded (no unbounded result sets).

Implemented examples: `advisor_work.person_work`, `compliance/reviews.person_reviews`,
`tax_domain.business_engagements`, `insurance.business_policies`,
`organization_service.list_person_business_ownership` / `list_household_business_ownership`.

## Alternatives considered
1. **Query owning-domain tables directly from the workspace.** Rejected: couples the composition
   layer to another domain's schema and bypasses its authorization semantics.
2. **Fetch a broad list and filter in the workspace.** Rejected: wrong scope, potential
   correctness gaps (e.g. paging), and duplicated policy — the D.12 audit chose owning-service
   reads instead.

## Reasons for the decision
Keeping reads on the owning service preserves single ownership (ADR-002), coherent authorization,
and stable behavior of existing functions, while giving composition exactly the scoped view it
needs.

## Consequences
### Positive consequences
- Coherent authorization/scope; no schema coupling; existing behavior unchanged (regression green).
- Reusable by any future consumer.

### Negative consequences and tradeoffs
- Owning services grow a few small read functions per new composition need.
- Requires discipline to place reads on the owner rather than inline.

## Enforcement
- Reads live on owning services: `app/services/advisor_work.py`,
  `app/services/compliance/reviews.py`, `app/services/tax_domain.py`,
  `app/services/insurance.py`, `app/services/organization_service.py`.
- Behavioral stability verified by owning-domain regression suites (D.7/D.9 etc.) staying green
  after each additive read.

## Exceptions
None currently approved.

## Revisit conditions
If several composition layers need the same shape, consider a shared per-person query helper on the
owning service — still on the owner, never in the consumer.

## References
- `app/services/{advisor_work,tax_domain,insurance,organization_service}.py`,
  `app/services/compliance/reviews.py`
- `docs/PLATFORM_ARCHITECTURE.md` §3 (principles), §5
- `tests/test_business_owner.py`, `docs/PHASE_D11*`, `docs/PHASE_D12*`
