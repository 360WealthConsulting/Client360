# ADR-014 — No mutation during incidental rendering

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Security / Authorization; Domain Owner (Organizations / Relationships).

## Context
During D.12 the audit found that the existing `organization_service.list_owned` read path calls
`ensure_person_entity`, which **upserts** a `relationship_entities` row. If a composition workspace
had used it to render a person's businesses, merely viewing the page could **create** a
relationship-entity row — a write triggered by a GET. This is a data-integrity hazard: reads must
not silently create source-domain records.

## Decision
Page rendering (GET) **must not** create, upsert, or otherwise mutate source-domain records.
- Read helpers used by composition **must not** call `ensure_*` / `create` / `upsert` unless that
  write is explicitly documented and required by the operation (which a render is not).
- Composition reads **must** be side-effect free.
- Any exception **requires** an explicit ADR and a test.

The D.12 remedy: a **pure read**, `organization_service.list_person_business_ownership`, which
looks up the person's existing relationship entity **without** creating one, and returns `[]` if
none exists.

## Alternatives considered
1. **Reuse `list_owned` and accept the upsert** (it "usually" rolls back under a read connection).
   Rejected: fragile, implicit, and a latent write on a read path.
2. **Guard the upsert behind a flag.** Rejected: still risks accidental writes; a dedicated pure
   read is simpler and unambiguous.

## Reasons for the decision
A read that can write is a correctness and auditability hazard (phantom entities, surprising
audit/timeline effects). A pure read removes the hazard entirely and makes the rule testable.

## Consequences
### Positive consequences
- Viewing a workspace can never create data; no phantom relationship entities.
- The rule is explicit and testable.

### Negative consequences and tradeoffs
- Two read paths exist (`list_owned` with the upsert, still used by the write flow; and the pure
  read for rendering) — a small, documented duplication.

## Enforcement
- Pure read: `app/services/organization_service.py::list_person_business_ownership` /
  `list_household_business_ownership` (no `ensure_person_entity`); consumed by
  `app/services/business_owner.py::_person_businesses`.
- Test: `tests/test_business_owner.py::test_zero_business_empty_state` asserts no
  `relationship_entities` row is created for a person with no ownership after composing the
  workspace.

## Exceptions
None currently approved. The write-path `list_owned`/`record_ownership` retains its intentional
entity creation (that is a write operation, not a render).

## Revisit conditions
If a render legitimately must create data (rare), it requires a superseding ADR and an explicit
test documenting the side effect.

## References
- `app/services/organization_service.py`, `app/services/business_owner.py`
- `docs/PLATFORM_ARCHITECTURE.md` §3 (principles), §17
- `tests/test_business_owner.py`, `docs/PHASE_D12_BUSINESS_OWNER_PLANNING_WORKSPACE.md`
