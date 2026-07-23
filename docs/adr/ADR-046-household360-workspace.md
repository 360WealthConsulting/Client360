# ADR-046 — Household 360 Workspace: Household-Level Composition, Member Rollups, and Relationship Navigation

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Client / Household); Reliability / Operations; Security /
Authorization (RBAC ownership); Business Operations Owner (Michael Shelton). Authorized compliance
reviewer: Not yet designated.

## Context
D.40 (ADR-045) built the Client 360 Workspace at `/client/{id}` and included a light household path.
Advisors needed to open one household and understand who belongs to it, each member's role and status,
the combined operational picture, member-specific information, shared relationships, current work and
deadlines, and where to take authoritative action. The building blocks already existed:
`get_household_portfolio` (the single authoritative aggregation), `household_timeline` (merges members,
dedups by `event_id`, deterministic), the D.39 Unified Work Queue (`compose_queue` with a
`household_id`/`person_id` filter contract), per-member and batch-by-people reads, and
`build_relationship_graph` (one-hop). The mandatory audit surfaced two decisive facts: (1) **household
record-scope does not automatically grant per-member `record_in_scope`** — that primitive is an exact
match and not team-aware — whereas **`accessible_person_ids` DOES expand household→members** (team-aware);
and (2) banking, retirement accounts, outside assets, liabilities, and **net worth** remain unmodelled.

## Decision
Phase D.41 upgrades the existing `/client/household/{id}` route into the full **Household 360 Workspace**
— a read-only COMPOSITION (`app/services/client360/household.py`, extending the D.40 package). **It is
not a second household database and never the source of truth.**

- **Household vs person responsibilities.** The household workspace **summarizes and navigates**
  (household context, member directory, member rollups, snapshot, graph); the person workspace
  (`/client/{person_id}`) remains the member-detail surface. Full member sections are not duplicated in
  the household screen; each member deep-links to `/client/{person_id}`, and the person workspace links
  back to `/client/household/{id}` (reciprocal navigation).
- **Record-scope boundary + member visibility.** `record_in_scope(principal, "household", id)` is
  verified ONCE at the boundary (404 out of scope). Member visibility is then gated by
  `accessible_person_ids` — the existing rule that inherits household→member access — so a
  household-assigned advisor sees its members, while members outside the principal's scope are
  **suppressed (fail closed)**, not shown. `record_in_scope("person", member_id)` is deliberately NOT
  used for the fan-out (it would drop every member for a household-only advisor).
- **Member-level rollup strategy.** Sections fan out over in-scope members using batch-by-people reads
  (`opportunities_for_people`, `reviews_due_for_people`, `open_exceptions_for_people`) where they exist,
  and per-member summaries otherwise, each capability-gated + fail-closed + timed.
- **Financial aggregation constraints.** The household portfolio total **reuses the single authoritative
  `get_household_portfolio`** aggregation (never re-summing member portfolios); per-member AUM +
  `member_contribution = member_aum / household_aum` (zero-guarded) are shown. Insurance face,
  opportunity revenue, benefit values, and tax figures are presented **side by side and NEVER summed**
  into assets.
- **Why "net worth" is not calculated.** Banking, retirement accounts, outside assets, and liabilities
  are not modelled — there is nothing authoritative to subtract. The workspace shows these as **"Not
  tracked"** and never fabricates a net-worth figure or a composite household score.
- **Timeline deduplication.** The household timeline reuses `household_timeline` (already merges members
  and dedups by `event_id`, deterministic order); a defensive composition-layer dedup pass reports a
  dedup count. No timeline rows are ever written from the workspace.
- **Relationship graph composition.** The household graph composes each member's one-hop
  `build_relationship_graph` + household memberships, with node/edge **dedup**, a **depth cap**, and
  **cycle protection** (key-deduped nodes/edges). It is read-only — no new relationship engine, and no
  relationship is created or mutated.
- **Authoritative workflow deep links.** Nine household-aware quick actions deep-link into the
  authoritative create surfaces, prefilled with the household id (and the primary member for
  person-scoped surfaces). **No new mutation layer, no hidden mutation endpoints.**
- **Unified Work reuse.** Household work reuses D.39 `compose_queue(filters={"household_id": hid})` — no
  re-query of task/workflow/exception domains.

## Alternatives considered
1. **A parallel household package.** Rejected: the household path extends the D.40 `client360` package
   (shared registry/serialization/capability-gating/timing/failure-isolation) — no unnecessary fork.
2. **A new top-level household route.** Rejected: the existing `/client/household/{id}` becomes the
   authoritative workspace (bookmark-compatible); no competing route.
3. **Gate members with `record_in_scope("person")`.** Rejected: it does not inherit household access and
   would suppress all members for a household-only advisor — `accessible_person_ids` is the correct,
   existing rule.
4. **Compute a household net worth / re-sum member portfolios.** Rejected: unmodelled domains + a second
   aggregation would be shadow business logic; reuse `get_household_portfolio`, show "Not tracked".
5. **A `rm_household360` projection.** Rejected: authoritative composition is affordable; a household
   projection would duplicate data and risk a shadow record.

## Reasons for the decision
The household must become a complete operational surface without weakening any invariant: no second
household engine, no shadow record, no duplicate person record, no duplicate portfolio aggregation, no
fabricated net worth, no inferred filing/dependency relationships, no direct mutation, no new event bus,
no duplicate projection. Composing the authoritative reads behind a single household scope boundary,
gating members with the existing household-inheriting rule, reusing the single portfolio aggregation and
D.39 for work, and deep-linking every edit achieves that and preserves ADR-004/013/041/042/043/044/045.

## Consequences
### Positive consequences
- One authoritative Household 360 workspace: household context, a first-class member directory (deep-
  linking to each `/client/{id}`), member-level rollups, an authoritative financial rollup with per-
  member contribution, a deduped household timeline, a cycle-protected relationship graph, a compact
  snapshot (+ AI-ready JSON), diagnostics + governance. Reciprocal person↔household navigation. Behavior
  of every domain unchanged (read-only).

### Negative consequences and tradeoffs
- Member visibility depends on the `accessible_person_ids` membership definition (`people.household_id`);
  a roster member whose primary household differs may be suppressed for a household-only advisor
  (correct fail-closed behavior; `record.read_all` sees all). Household work uses the `household_id`
  filter, so member-only work items without a household anchor surface on the member's own workspace
  (documented). Financial breadth is bounded by what the platform models; unmodelled concepts show "Not
  tracked".

## Enforcement
- `app/services/client360/household.py` (context, member directory, section rollups, snapshot, quick
  actions, graph) + `diagnostics.household_diagnostics` + `governance.validate_household360`;
  `service.get_workspace` delegates the household path. Routes in `app/routes/client360.py`
  (`GET /client/household/{id}`, `/snapshot`, `/diagnostics`); template
  `app/templates/client360/household.html`. **No migration, no new table, no new projection, no new
  capability** (reuses `client.read` + per-domain read caps + `observability.audit`). Migration head
  unchanged. The authoritative domain services, their tables/ledgers, the outbox, the projection model,
  the runtime/policy engines, and RBAC are untouched. Tests: `tests/test_household360_workspace.py`;
  platform-architecture / route-count / ADR-count guards updated.

## Exceptions
The page reuses `client.read`; each section respects its domain read capability; diagnostics reuse
`observability.audit`; the snapshot JSON enforces the same security as the HTML page. Household record
scope is enforced once at the boundary (404); member visibility follows `accessible_person_ids`;
`administrator`/`record.read_all` bypass is unchanged (ADR-004). Unmodelled concepts show "Not tracked".

## Revisit conditions
Adding a modelled banking/retirement/liability/net-worth domain (then the financial rollup composes it),
adding a member-anchored work filter to D.39, adding a household-level relationship read, or letting the
workspace mutate directly (it must always deep-link) would each warrant a new or superseding ADR.

## References
- `app/services/client360/household.py`, `app/services/client360/{diagnostics,governance,service}.py`,
  `app/routes/client360.py`, `app/templates/client360/household.html`
- `docs/HOUSEHOLD360_WORKSPACE.md`, `docs/HOUSEHOLD360_WORKSPACE_ADAPTERS.md`,
  `docs/HOUSEHOLD360_WORKSPACE_ACTIONS.md`, `docs/HOUSEHOLD360_WORKSPACE_GOVERNANCE.md`
- `docs/CLIENT360_WORKSPACE.md`, `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_household360_workspace.py`; relates to ADR-004, ADR-013, ADR-041, ADR-042, ADR-043, ADR-044, ADR-045
