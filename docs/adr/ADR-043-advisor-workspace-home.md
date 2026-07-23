# ADR-043 — Advisor Workspace Home: a personalized, projection-backed advisor home composed over existing services; view-state only, RBAC preserved

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Advisor Workspace); Reliability / Operations; Security /
Authorization (RBAC ownership); Business Operations Owner (Michael Shelton). Authorized compliance
reviewer: Not yet designated.

## Context
Phases D.34–D.37 built the enterprise read infrastructure: the domain-event model, the Projection
Engine (12 disposable read models), and read-surface adoption (projection-backed reads with graceful
fallback). D.38 is the first phase that turns that investment into the daily advisor experience. An
Advisor Workspace already existed (Phases D.1–D.12): a record-scoped daily dashboard at `/workspace`
(`advisor_workspace.get_daily_dashboard`) with needs-attention / meetings / reviews / tasks /
exceptions / activity panels and a propose-only Advisor Intelligence framework. What it lacked was a
quick-glance, **personalizable** home — a widget grid the advisor can reorder / hide / pin, saved
layout presets, a TODAY summary, a deterministic PRIORITIES view — and a clean, AI-consumable summary
layer. It also did not yet consume the D.36/D.37 projections.

## Decision
Phase D.38 **extends** the existing `/workspace` surface (it does not fork a new one) with a new
composition + personalization layer, `app/services/workspace/`:

- **A widget registry (12 widgets)** — Today's Calendar, Active Clients, Workflow Exceptions,
  Operational Tasks, Recent Activity, Revenue Pipeline, Compliance Queue, Tax / Insurance / Benefits
  pipelines, Document Review, Team Workload. Each widget declares the capability that gates it, its
  section, and a deep link into the owning surface (no dead-end tiles).
- **Projection-backed compute** — count widgets read the D.37 projection-backed analytics sources,
  which serve from a projection when healthy + fresh on the firm-wide path and fall back to the
  authoritative, record-scoped read otherwise. List widgets call the existing record-scoped read
  services. **No widget mutates anything; a widget that raises is isolated so it never breaks the home.**
- **Personalization (view state only)** — two new tables, `workspace_preferences` (one row per user:
  order / hidden / pinned / remembered filters) and `workspace_presets` (named saved layouts). Writes
  are self-service (always the acting user's own `user_id`) and gated by a new non-sensitive
  capability `workspace.personalize`. Reorder / hide / pin / reset / preset controls are POST-form
  (POST-redirect-GET), matching the app's no-JS convention.
- **AI-ready summary models** — five read-only structured dicts (Daily Brief, Client Snapshot, Meeting
  Prep, Opportunity Summary, Compliance Summary) exposed as JSON, composing existing record-scoped
  reads so a future assistant can consume them without changing the dashboard architecture.

Why this shape:
- **Why extend, not fork.** The advisor home already lives at `/workspace`; D.38 adds a widget grid +
  personalization + summaries above the existing detail panels. No parallel home, no duplicated
  composition, honoring the ADVISOR_WORKSPACE extension rules (record-scoped worklists, capability
  gating, propose-never-act).
- **Why view state only.** Preferences/presets store UI layout, not business data — no authoritative
  state, no ledger, nothing downstream depends on them. The authoritative services remain the sole
  mutation layer, the transactional outbox remains the sole event bus, and projections stay disposable.
- **Why RBAC is never bypassed.** A widget the principal cannot open is never assembled (no
  shown-then-403). Count widgets inherit the D.37 rule (a projection is served only on the firm-wide
  `record.read_all` path; a record-scoped principal always gets the authoritative scoped read). Summary
  models enforce person record-scope (404 out of scope). The page stays gated by `client.read`.
- **Why behavior is unchanged by default.** Projections are dark-launched (unbuilt), so every count
  widget falls back to the authoritative read until an operator enables + rebuilds — same numbers as
  before, now behind one indirection.

## Alternatives considered
1. **A brand-new advisor home surface.** Rejected: `/workspace` already exists; a second home would
   duplicate composition and split the advisor experience.
2. **Store widget layout in the generic `configuration_preferences` table.** Rejected: that table is a
   governance construct ("governs, does not own"), gated by `configuration.manage`; a purpose-built,
   self-service table is cleaner and correctly scoped.
3. **Drag-and-drop reordering (client-side JS framework).** Rejected for this phase: the app is
   pure-SSR with progressive-enhancement vanilla JS; POST-form controls preserve the convention and
   work without JavaScript. Drag-and-drop can be layered on later as enhancement.
4. **Query authoritative tables directly for widgets.** Rejected: D.36/D.37 exist precisely so read
   surfaces consume projections with fallback; widgets go through the adoption-backed sources.
5. **Build AI features now.** Deferred: D.38 exposes clean summary models AI can later consume; no AI
   logic is added.

## Reasons for the decision
The advisor experience must become fast and personalizable without weakening any invariant: no new
write path, no second event log, no RBAC bypass, no behavior change by default. Extending `/workspace`
with a projection-backed widget grid, a self-service view-state store, and read-only summary models
delivers the daily home while keeping the write side authoritative, the outbox the sole event bus, and
projections disposable — preserving ADR-004/013/041/042 and the ADVISOR_WORKSPACE extension rules.

## Consequences
### Positive consequences
- A production-ready, personalizable advisor home: greeting, TODAY summary, deterministic PRIORITIES,
  and a 12-widget grid (reorder / hide / pin / saved presets), each widget projection-backed where a
  D.37 source exists and deep-linked into its surface. Five AI-ready summary models are available for a
  future assistant. Personalization is observable per-user and fully capability-gated.

### Negative consequences and tradeoffs
- Two new tables (view state only, disposable per user) + one capability add a little schema/RBAC
  surface. Personalization is POST-form (a click per reorder step), not drag-and-drop, until a later
  enhancement. Widgets show quick-glance counts/short lists; deep detail stays on the owning surface.
- Team Workload and Document Review have no projection yet, so they read authoritatively (scoped);
  Team Workload requires `capacity.read`, so most advisors will not see it (correct RBAC).

## Enforcement
- `app/services/workspace/` (registry, widgets, preferences, digest, summaries, service, common);
  `app/database/workspace_tables.py` (registered in `schema.py`); `app/db.py` exposes the two tables;
  migration `migrations/versions/k2w3s4p5r6f7_advisor_workspace_personalization.py` (seeds
  `workspace.personalize` → advisor/operations/administrator). Routes in `app/routes/workspace.py`
  (`/workspace`, `/workspace/customize`, `/workspace/presets`, `/workspace/reset`,
  `/workspace/summaries/*`). Templates `app/templates/workspace/{dashboard,_widgets}.html`,
  `app/static/css/workspace.css`. The authoritative domain services, their tables/ledgers, the outbox,
  the event/projection model, the runtime/policy/orchestration engines, and RBAC are untouched. Tests:
  `tests/test_advisor_workspace_home.py`; manifest / platform-architecture / route-count / ADR-count
  guards updated.

## Exceptions
The page stays gated by `client.read`; personalization mutations require `workspace.personalize`.
Count widgets are served from a projection only on the firm-wide (`record.read_all`) path; scoped
principals get the authoritative scoped read (ADR-004 scope bypass for `administrator`/`record.read_all`
unchanged). Projection serving requires an operator to enable + rebuild — otherwise widgets fall back
to authoritative. Team Workload / Document Review are authoritative (no projection yet).

## Revisit conditions
Adding drag-and-drop (client-side JS) reordering, adding projections for Team Workload / Document
Review / Calendar, sharing preset layouts across users (would need cross-user access + a sharing
model), or building AI features on the summary models would each warrant a new or superseding ADR.

## References
- `app/services/workspace/*`, `app/routes/workspace.py`, `app/database/workspace_tables.py`,
  migration `migrations/versions/k2w3s4p5r6f7_advisor_workspace_personalization.py`,
  `app/templates/workspace/*`, `app/static/css/workspace.css`
- `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md`, `docs/PLATFORM_ARCHITECTURE.md`,
  `docs/platform_architecture_manifest.yaml`
- `tests/test_advisor_workspace_home.py`; relates to ADR-004, ADR-013, ADR-041, ADR-042
