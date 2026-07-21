# ADR-015 — No fabricated data, history, calculations, or relationships

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Compliance Architecture; Business Operations Owner (Michael Shelton);
Domain Owner (each domain).

## Context
Advisor-facing surfaces are tempting places to "fill in" gaps — infer ownership from an employer
name, show a plausible tax figure, estimate a contribution limit, size an insurance need, or
present recomputed recommendations as historical events. In a regulated advisory context, fabricated
data, history, calculations, or relationships are misleading and dangerous.

## Decision
The platform **must not** fabricate. Specifically it **must not**:
- invent ownership, or infer ownership from free text (employer/occupation/notes/recommendation
  text/tax-document presence);
- fabricate timestamps or actors;
- fabricate tax values, compensation, contribution limits, valuations, or insurance needs;
- fabricate compliance approval, or reconstruct history and present it as recorded history;
- use **zero** as a substitute for **missing**, or **missing** as a substitute for **restricted**.

Where a value is unavailable, surfaces **must** use an approved honest marker:
**"Not available"**, **"Not currently modeled"**, **"Additional data required"**, **"Restricted"**,
**"Unknown"**, or **"Conflicting source data"**.

## Alternatives considered
1. **Show best-effort estimates/inferences** to make screens feel complete. Rejected: misleads
   advisors and clients; unacceptable for regulated advice.
2. **Silently omit unavailable fields.** Rejected: hides gaps and can read as "zero/none";
   explicit honest markers are required.

## Reasons for the decision
Honesty about what is and isn't known is a core compliance and trust requirement; explicit markers
also make missing-vs-restricted distinguishable (ADR-005) and prevent fabricated history in the
timeline (ADR-009).

## Consequences
### Positive consequences
- Advisors can trust every displayed value; gaps are explicit and actionable.
- No fabricated history, calculations, or relationships enter the system.

### Negative consequences and tradeoffs
- Screens show many "Not available / Not tracked" markers where upstream data is absent (by
  design, e.g. owner compensation, tax content, policy purpose).

## Enforcement
- Recommendations excluded from the timeline (no durable timestamp): `activity_timeline` adapters.
- "Not available" placeholders + "not tracked" lists: `app/services/business_owner.py`
  (`_owner_compensation_placeholder`, `_tax_profile.untracked`, `_insurance_section` purpose
  "unconfirmed").
- Ownership only from structured edges: `business_owner.is_business_owner`.
- Missing-vs-restricted: ADR-005 enforcement; `not_currently_modeled` in the manifest.
- Tests: `tests/test_business_owner.py`, `tests/test_activity_timeline.py`,
  `tests/test_intelligence_refactor_regression.py`.

## Exceptions
None currently approved.

## Revisit conditions
When an authoritative source for a currently-unavailable datum is added, its marker is replaced by
real data — never by an estimate in the interim.

## References
- `app/services/business_owner.py`, `app/services/activity_timeline/service.py`
- `docs/PLATFORM_ARCHITECTURE.md` §23 (Current limitations), §25 (Prohibited patterns)
- `tests/test_business_owner.py`, `tests/test_activity_timeline.py`
