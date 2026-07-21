# ADR-001 — Composition layers

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (per consumed domain); Business Operations Owner
(Michael Shelton, for workflow/operational requirements).

## Context
Advisors need consolidated, cross-domain views (a client's 360 picture, a meeting brief, a
chronological activity view, an annual review, a business-owner planning picture). The naive
approach — letting each new screen own its own copy of client/portfolio/tax/benefits/insurance
data and logic — would fork business rules, drift out of sync with the authoritative domains,
and multiply the surfaces where sensitive data and scope must be re-enforced.

## Decision
Cross-domain advisor surfaces **must** be built as **composition layers** that **consume**
authoritative domains read-first.
- A composition layer **must not** be a general source of truth.
- A composition layer **may** persist **only** records it explicitly owns.
- A composition layer **must not** duplicate source-domain business logic.
- A composition layer **must not** mutate source-domain records during rendering.
- Source domains **must not** import composition layers.
- The capabilities of every consumed domain **remain mandatory** — a composition layer never
  bypasses them.

Client 360 and the Meeting Workspace are **read-only** composition surfaces (they persist
nothing; meeting outcomes route to owning services). Owned persistence is limited to:
Annual Review → `annual_review_sessions`; Business Owner Planning → `business_planning_profiles`;
Activity Timeline → a read **projection** plus approved durable domain timeline events (never
duplicated source rows).

## Alternatives considered
1. **Screen-owned data/logic per view** — each workspace queries and re-derives domain data
   directly. Rejected: duplicates business rules, drifts from source, and re-scatters scope and
   redaction enforcement.
2. **A denormalized read-model / materialized "advisor view" store** synced from domains.
   Rejected: introduces a sync pipeline, staleness, and a second source of truth — disproportion
   for the current scale, and it would obscure ownership.

## Reasons for the decision
Composition keeps exactly one authoritative owner per datum (ADR-002), lets each domain keep its
own authorization/redaction, and makes new advisor surfaces cheap and consistent. It matches the
observed reality that these surfaces are *views*, not systems of record.

## Consequences
### Positive consequences
- One owner per datum; no logic duplication; consistent scope/redaction.
- New workspaces compose existing services quickly and safely.
- Source domains evolve without breaking consumers (consumers depend downward only).

### Negative consequences and tradeoffs
- Composition layers issue several reads across domains (bounded, but more calls than a single
  denormalized store).
- A genuinely new fact (e.g. succession) still needs owned persistence, decided case by case.

## Enforcement
- Code structure: `app/services/advisor_workspace.py`, `app/services/activity_timeline/service.py`,
  `app/services/annual_review.py`, `app/services/business_owner.py`.
- Dependency-direction tests: `tests/test_activity_timeline.py`, `tests/test_annual_review.py`,
  `tests/test_business_owner.py`, and `tests/test_platform_architecture.py`
  (`test_source_producers_do_not_import_composition_layers`).
- Manifest: `docs/platform_architecture_manifest.yaml` (`composition_layers`,
  `composition_service_modules`).

## Exceptions
The intentional composition-consumes-composition edge `business_owner → annual_review` (a higher
layer reads the latest review) is downward and allowed. No other exception is approved.

## Revisit conditions
If read fan-out becomes a measured performance problem at scale, or a genuine denormalized
read-model is justified, revisit with a superseding ADR that defines sync and ownership.

## References
- `docs/PLATFORM_ARCHITECTURE.md` §7 (Composition layers), §6 (Dependency architecture)
- `app/services/{advisor_workspace,annual_review,business_owner}.py`,
  `app/services/activity_timeline/service.py`
- `tests/test_platform_architecture.py`, `docs/PHASE_D11_ANNUAL_REVIEW_WORKSPACE.md`,
  `docs/PHASE_D12_BUSINESS_OWNER_PLANNING_WORKSPACE.md`
