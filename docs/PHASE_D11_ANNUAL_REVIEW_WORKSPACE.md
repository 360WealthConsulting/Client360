# Phase D.11 — Annual Review Workspace

## Objective
One advisor-facing workspace that answers *"What do I need to review with this client
today?"* — the primary destination for advisor-led annual client meetings. It **composes**
existing Client360 domains into one read-first operational workspace. It consumes existing
services; it does not replace them, duplicate their logic, execute recommendations, automate
compliance, or add planning logic.

## Architecture
```
Client360 / Meeting Workspace ─┐
Advisor Intelligence           │
Advisor Work (D.9)             │   read-only, per-section
Activity Timeline (D.10)       ├──►  Annual Review Service  ──►  Annual Review Workspace
Compliance (D.6–D.8)           │     (app/services/annual_review.py)   (/annual-review/{person_id})
Portfolio                      │
Client Profile                ─┘
```
**Dependency direction (enforced by test):** existing domains never import Annual Review;
Annual Review consumes existing services only. No duplicated business logic. No workflow
engine, no event bus, no automation.

## Composition (what each section reuses)
| # | Section | Reuse point | Notes |
|---|---------|-------------|-------|
| 1 | Client Snapshot | `advisor_workspace.get_meeting_brief` + `client_summary.get_client_summary` + `calendar.get_person_calendar_events` | client / household / advisor / review date / last meeting / next meeting / status / household summary — read only |
| 2 | Advisor Intelligence | `advisor_intelligence.get_client_signals` (filtered to `recommendation`) | reused, **never regenerated**; priority / governing rule / policy gate / summary + navigation to detail |
| 3 | Outstanding Advisor Work | `advisor_work.person_work` (additive read) | open work / priority / owner / due / status + navigation; no embedded work management |
| 4 | Recent Activity | `activity_timeline.service.client_timeline` (page_size 5) | recent events + "View full timeline →"; no duplicate timeline query |
| 5 | Compliance Summary | `compliance.reviews.person_reviews` (additive read) | pending / blocked / completed counts + reviewer assignments; recreates no compliance logic |
| 6 | Portfolio Overview | the snapshot already inside `get_meeting_brief` | **no second portfolio fetch, no new calculations** |
| 7 | Meeting Preparation | `get_meeting_brief` (agenda = open tasks, preparation notes) | no duplicate meeting logic |
| 8 | Review Checklist | presentation-only, bound to the session's `checklist_state` | completion records advisor activity only |

Two **additive, person-scoped reads** were added to the owning services
(`advisor_work.person_work`, `compliance.reviews.person_reviews`) because neither domain
had a per-person read (their existing reads are book-scoped/paginated). These are additive:
every existing function is untouched, so D.9/D.7 behavior is unchanged (regression suites
prove it). This is the same "smallest read on the owning service" pattern used in D.5B–D.5D,
and is strictly safer than fetching a book page and filtering client-side.

## Review session model
The ONLY new persistence is `annual_review_sessions` — a **mutable** advisor-activity record
(edited in place, so **not** an append-only ledger):

`id, person_id (NOT NULL → people, CASCADE), household_id (→ households, SET NULL),
advisor_id (→ users, SET NULL), started_at, completed_at, status, notes, checklist_state
(jsonb), created_at, updated_at`.

A session records that a review happened and holds its notes + presentation-only checklist.
It **never** changes a source-domain record (recommendations, work, timeline, compliance,
portfolio). Session notes belong to the session only.

## Session lifecycle (no workflow engine — an explicit status set)
`draft → in_progress → completed → archived`. "Start review" creates an `in_progress`
session (records `started_at`); it is **idempotent** — a partial-unique index
`(person_id, advisor_id) WHERE status IN (draft, in_progress)` allows at most one open
session per advisor per client, so a second start returns the existing one. Notes/checklist
are editable only while `draft`/`in_progress`; `completed` records `completed_at` and is
read-only; `archived` is terminal. Transitions are an explicit allowed-source map — no
generic engine.

## Checklist (presentation only)
A fixed 13-item list (client information, beneficiaries, risk tolerance, investment
allocation, retirement goals, insurance, tax planning, estate planning, cash flow, business
planning, pending advisor work, compliance items, meeting follow-up). Checking an item writes
to the session's `checklist_state`; unknown keys are ignored. It modifies no planning/domain
logic.

## Authorization
Capabilities `annual_review.read` (administrator/advisor/operations),
`annual_review.create` and `annual_review.update` (administrator/advisor) — seeded by the
migration. Enforcement is server-side only.

`/annual-review/*` is **outside** the `^/(people|households)` middleware RECORD_PATH, so the
service enforces person **record scope itself** (scope-first: out-of-scope client → `None` →
404). `annual_review.*` is **never a bypass**: each composed section is gated on its OWNING
capability — Advisor Work needs `advisor_work.read`, Recent Activity needs `timeline.read`,
Compliance Summary needs `compliance.review.read`; a principal lacking one gets that section
omitted, never its contents. Session writes re-check person write-scope.

## Navigation & Client360 integration
- `GET /annual-review/{person_id}` — the workspace (all 8 sections) + open-session banner /
  "Start annual review" + recent completed reviews.
- `POST /annual-review/{person_id}/start` — begin/resume a session (idempotent).
- `GET /annual-review/session/{id}` — the workspace bound to a session (editable notes +
  checklist + Complete/Archive when editable).
- `POST /annual-review/session/{id}` — save notes/checklist or transition status.

Client 360 (`people/workspace.html`) gains an **"Annual review →"** link (gated by
`annual_review.read`), beside the existing "Prepare for meeting →" / "Activity timeline →"
links. Client 360 is otherwise unchanged. No dashboards.

## Performance
Reuses existing services; the portfolio is reused from the meeting-brief snapshot (no second
fetch); Advisor Intelligence is read once (not recomputed); the timeline is queried once at
page size 5; owner display names are resolved in a single `users` query (no N+1). All reads
are bounded by one client's volume.

## Migration decision
**One migration** (`i9a1n2r3e4v5`, down `h2t3i4m5l6n7`): creates `annual_review_sessions`
(the sole new persistence — session activity), its indexes, the partial-unique OPEN guard,
and the CHECK constraint; and seeds the three `annual_review.*` capabilities. **No
source-domain table is touched.** Upgrade/downgrade verified (down removes the table and the
caps).

## Testing
`tests/test_annual_review.py` (13): workspace composition (all sections populated),
recommendations reused (equals `get_client_signals` filtered — not regenerated), scope-first
(stranger → `None`), per-section capability gating (no bypass), session lifecycle +
idempotent start, invalid-transition rejection, checklist + note persistence (unknown keys
filtered), session scope-first, route rendering (workspace + session views), start-creates-
session redirect, update-session save via route, Client360 link presence, and dependency
direction (no source domain imports Annual Review). The D.5 golden and all D.5–D.10 suites
re-run green — Advisor Intelligence, Timeline, Advisor Work, Compliance, and Portfolio
behavior are unchanged.

## Exclusions honored
No workflow engine, CRM sync, calendar integration, email/SMS/Slack, notifications, automatic
scheduling, automatic review/recommendation creation or completion, AI/LLMs, document
generation, trade execution, compliance automation, new portfolio calculations, new
recommendation/planning logic, client portal, or mobile features.

## Remaining technical debt
- "Advisor" in the Client Snapshot is the current reviewing advisor (`principal`) — the data
  model has no per-client owning-advisor field.
- Last/next meeting derive from Microsoft 365 calendar events; clients without synced calendars
  show "None".
- The two additive owning-service reads (`person_work`, `person_reviews`) could later be
  folded into a shared per-person query helper if more composition layers need them.
- Completed-session immutability is enforced in the service (status gate), not by a DB trigger
  (sessions are intentionally mutable while open); a future hardening could lock completed rows.
