# ADR-045 — Client 360 Workspace: the master client record as a composition surface, not a second client engine

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Client / Household); Reliability / Operations; Security /
Authorization (RBAC ownership); Business Operations Owner (Michael Shelton). Authorized compliance
reviewer: Not yet designated.

## Context
Advisors and operations staff needed one operational screen to open a person or household and see —
and act on — the client's whole picture: summary, financial, tax, insurance, benefits, opportunities,
documents, meetings, compliance, activity timeline, and relationships. The pieces already existed:
`/people/{id}` (the person profile, itself an inline composer of ~15 services), `advisor_workspace`
composition helpers (`get_client_snapshot`, `get_meeting_brief`), `annual_review.compose_workspace`
(the D.11 precedent — scope-first, capability-gated, reuse-not-recompute), `build_relationship_graph`,
and per-domain scoped reads. What was missing was a single, governed `/client/{id}` surface that
composes them with a compact snapshot, a relationship graph, and deep-link quick actions — without
becoming a second client database.

Two hard facts shaped the design: (1) **record-scope enforcement is uneven** across the domain reads —
some self-check, some are person-keyed factual reads that trust the caller; and (2) several financial
concepts the spec lists (banking, retirement accounts, outside assets, liabilities, **net worth**) are
**not modelled anywhere in the platform** — there is no assets−liabilities computation to reuse.

## Decision
Phase D.40 adds the **Client 360 Workspace** at `GET /client/{id}` — a read-only COMPOSITION surface
(`app/services/client360/`). **It is not a second client database and never the source of truth.**

- **Composition, not a new engine.** Twelve section builders each reuse ONE authoritative domain read
  (portfolio, insurance, benefits, tax, opportunity, documents, compliance, exceptions, advisor-work,
  timeline, relationships) — no recomputation, no duplicated business logic, no shadow client record,
  no new table, no new projection. The financial figures reuse the single authoritative
  `aggregate_portfolio` math and are presented **side by side, never summed** (units differ), exactly
  as `get_client_snapshot` already does.
- **Record scope verified once at the boundary.** Because scope enforcement is uneven, the workspace
  calls `record_in_scope(principal, entity_type, id)` ONCE up front (returns `None` → route 404), then
  fans out — so the person-keyed factual reads that do not self-check are always reached in-scope. A
  section the principal lacks capability for is omitted (never shown-then-403); sections fail closed.
- **Every edit deep-links to the authoritative workflow.** Nine quick actions (Schedule Meeting, Upload
  Document, Add Note, Create Task, Start Tax Return, Create Opportunity, Start Insurance Case, Send
  Secure Message, Generate Meeting Prep) are deep links into the authoritative create surface, prefilled
  with the client's id. **The workspace performs no mutation of its own.**
- **Unmodelled concepts are surfaced honestly, never fabricated.** Banking, retirement accounts, outside
  assets, liabilities, net worth, and client status/tier/risk have no domain — they are reported as
  "not tracked" rather than invented. (Adding them would be new domains, out of scope for a composition
  surface.)
- **Read-only snapshot + relationship graph.** A compact executive snapshot (assets / revenue pipeline /
  tax / insurance / compliance / deadlines / open work / last communication) is exposed for the page and
  as AI-ready JSON. The relationship graph reuses `build_relationship_graph` (family / business /
  professional / estate categories = beneficiaries / trustees / businesses / employers / dependents /
  advisors) — read-only.
- **Runtime / Policy / RBAC / record scope / audit / outbox preserved.** The workspace changes none of
  them; it only reads.

## Alternatives considered
1. **Extend `/people/{id}` in place.** Rejected: the person profile is already a large inline composer;
   a dedicated `/client/{id}` surface with capability-gated tabs, a snapshot, diagnostics, and
   governance is cleaner and household-capable, and it does not disturb the existing profile.
2. **A `rm_client360` unified projection.** Rejected: per-client reads are affordable authoritative
   composition; a cross-domain client projection would duplicate data, need scope anchors it lacks, and
   risk becoming a shadow record.
3. **Compute net worth / banking / liabilities in the workspace.** Rejected: those domains do not
   exist; computing them here would be new business logic in a composition layer (a shadow domain). They
   are surfaced as "not tracked".
4. **Let each section self-enforce scope.** Rejected: enforcement is uneven; a single boundary check is
   safer and matches the `compose_workspace` precedent.

## Reasons for the decision
The master client record must let a user do nearly all daily work from one screen without weakening any
invariant: no second client engine, no duplicated business logic, no duplicate projection, no shadow
record, no mutation in the composition layer. Composing the authoritative scoped reads behind a single
boundary scope-check, deep-linking every edit to the owning workflow, and honestly excluding unmodelled
concepts achieves that and preserves ADR-004/013/041/042/043/044.

## Consequences
### Positive consequences
- One governed `/client/{id}` workspace (person or household) with 12 capability-gated sections, a
  compact snapshot (+ AI-ready JSON), a read-only relationship graph, deep-link quick actions,
  composition diagnostics, and governance. Behavior of every domain is unchanged (read-only). Section
  timings are observable; governance proves the surface stays a composition.

### Negative consequences and tradeoffs
- Financial breadth is bounded by what the platform models (portfolio AUM/cash/allocation + insurance
  face + benefit relationships); banking/retirement/outside-assets/liabilities/net-worth show as "not
  tracked". Some domain reads are book-scoped rather than strictly per-person (meetings via the calendar
  timeline); the workspace filters to the client. Household mode composes the household-capable subset.

## Enforcement
- `app/services/client360/` (service, sections, registry, snapshot, diagnostics, governance, common);
  routes in `app/routes/client360.py` (`GET /client/{id}`, `/client/{id}/snapshot`,
  `/client/{id}/diagnostics`, `/client/household/{id}`, `/client/household/{id}/diagnostics`); template
  `app/templates/client360/workspace.html`, `app/static/css/client360.css`. No migration (composition
  only — head unchanged), no new table, no new capability (reuses `client.read` + per-domain read caps +
  `observability.audit` for diagnostics). The authoritative domain services, their tables/ledgers, the
  outbox, the projection model, the runtime/policy engines, and RBAC are untouched. Tests:
  `tests/test_client360_workspace.py`; platform-architecture / route-count / ADR-count guards updated.

## Exceptions
The page is gated by `client.read`; each section tab additionally requires its domain read capability;
diagnostics require `observability.audit`. Record scope is enforced once at the boundary (404 out of
scope); `administrator` / `record.read_all` scope bypass is unchanged (ADR-004). Unmodelled financial
concepts are reported as "not tracked", not computed.

## Revisit conditions
Adding a modelled banking / retirement-account / liability / net-worth domain (then the Financial
section would compose it), adding a per-client projection (only if authoritative composition becomes
materially too expensive), or letting the workspace mutate directly (it must always deep-link) would
each warrant a new or superseding ADR.

## References
- `app/services/client360/*`, `app/routes/client360.py`, `app/templates/client360/workspace.html`,
  `app/static/css/client360.css`
- `docs/CLIENT360_WORKSPACE.md`, `docs/CLIENT360_WORKSPACE_ADAPTERS.md`,
  `docs/CLIENT360_WORKSPACE_ACTIONS.md`, `docs/CLIENT360_WORKSPACE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_client360_workspace.py`; relates to ADR-004, ADR-013, ADR-041, ADR-042, ADR-043, ADR-044
