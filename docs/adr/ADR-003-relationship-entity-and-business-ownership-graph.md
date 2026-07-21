# ADR-003 — Relationship-entity and business-ownership graph

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Organizations / Relationships); Business Operations Owner
(Michael Shelton).

## Context
Business owners rarely map one-to-one to a single business. A person may own several businesses;
a business may have several owners; households contain multiple owners and businesses; ownership
may be direct, indirect, current, former, or held through a household. A flat `business_id` on
`people`, or free-text employer/occupation fields, cannot represent this and would invite
inferring ownership from names or tax-document presence.

## Decision
Business identity and ownership **must** be modeled as a relationship graph:
- **`relationship_entities`** (a business is `entity_type='business'`) with a 1:1
  **`organization_profiles`** (legal name, EIN, `entity_form`, status).
- **`relationships`** edges (`owns`, category `ownership`) with a 1:1 **`relationship_ownership`**
  detail (`ownership_percentage`, `voting_percentage`, `is_direct`, `evidence_source`).

The platform **must not** use a `business_id` column on `people`, free-text employer/occupation,
tax-document presence, recommendation text, or duplicated business tables inside workspaces as
ownership evidence. Consequently:
- one person **may** own multiple businesses; one business **may** have multiple owners; a
  household **may** own multiple businesses;
- direct/indirect/current/former/household ownership **must** remain representable;
- ownership **must** be validated through structured relationships;
- ownership percentages **must not** be silently normalized, and incomplete totals **must**
  remain visible;
- a name match **is not** ownership proof.

## Alternatives considered
1. **`people.business_id` (or `business_name` text)**. Rejected: cannot express multiple
   owners/businesses, indirect/former ownership, or households; invites name-based inference.
2. **A dedicated `businesses` table separate from the relationship graph**. Rejected: the graph
   already models entities and typed relationships (organizations, carriers, households); a second
   table would duplicate identity and split ownership.

## Reasons for the decision
The relationship graph already existed (Benefits/Organizations foundation) and natively expresses
many-to-many, typed, dated ownership with provenance — exactly what business-owner planning needs
without fabricating a flat model.

## Consequences
### Positive consequences
- Faithful many-to-many ownership with percentages, direction, dates, and evidence.
- Business Owner Planning validates ownership structurally, never from free text.

### Negative consequences and tradeoffs
- `ownership_type` is free-text and `as_of_date` is not always populated (documented limitation).
- The unique `(from,to,type)` edge prevents representing same-owner "conflicting" duplicate edges;
  cross-source conflict rows are not expressible today.

## Enforcement
- Schema: migration `r8c69f7e6d5c` (relationship graph) + `organization_profiles`; reads in
  `app/services/organization_service.py` (`list_owners`, `list_person_business_ownership`).
- Business-owner status derives only from active ownership edges (`app/services/business_owner.py`,
  `is_business_owner`); tested in `tests/test_business_owner.py`
  (`test_inactive_ownership_not_business_owner`, ownership totals/missing-percentage).

## Exceptions
None currently approved. Free-text `ownership_type` and unpopulated `as_of_date` are limitations,
not sanctioned inference paths.

## Revisit conditions
If cross-source ownership conflicts must be represented, or an enumerated `ownership_type` is
required, revisit the edge-uniqueness constraint and detail schema via a new ADR.

## References
- `app/services/organization_service.py`, `app/services/business_owner.py`
- migration `r8c69f7e6d5c_employee_benefits_foundation.py`
- `docs/PLATFORM_ARCHITECTURE.md` §8 (Identity and relationship model)
- `tests/test_business_owner.py`
