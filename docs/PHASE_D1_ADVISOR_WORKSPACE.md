# Phase D.1 — Advisor Workspace Foundation & Daily Dashboard

First production slice of the approved `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md`.
Implemented on `release/0.13.0`.

## What was implemented
- **Advisor Workspace navigation group** — a new top sidebar group **Workspace → Today** (`/workspace`), gated on `client.read` (book-scoped, so advisors without `record.read_all` get it). All existing specialist consoles (Overview, Clients, Wealth, Work, Tax, Benefits, Insurance, Oversight, …) are preserved and unchanged.
- **Read-only Daily Dashboard** at `GET /workspace` with six panels: *My clients needing attention · Meetings today · Upcoming reviews · Tasks · Exceptions · Recent client activity*, plus a count strip. Reuses the existing design system (`ui.page_head`, `section-title`, `table.data`/`.num`, `.card`, `ui.badge`, `ui.empty`) — no new CSS/JS.
- **Thin orchestration service** `app/services/advisor_workspace.py::get_daily_dashboard(principal, *, now=None)` — composition only; no writes; no new domain/task/workflow/exception/notification/calendar/timeline logic.

## Authoritative services reused (composition, not duplication)
| Panel | Source |
|---|---|
| Clients needing attention | grouped open exceptions from `work_management.work_items(principal)` (record-scoped) |
| Tasks | `work_management.work_items(principal)` (task items) |
| Exceptions | `work_management.work_items(principal)` (exception items; tax/benefits/insurance) |
| Meetings today | `timeline.recent_events(...)` — `event_type='calendar_event'`, firm-tz day window |
| Recent client activity | `timeline.recent_events(...)` |
| Upcoming reviews | `portfolio.accounts_due_for_review(...)` — from `accounts.last_review_date` |
| Record scope | `security.authorization.accessible_person_ids(conn, principal)` |

Two small **authoritative** read methods were added to their owning services (not queried from the route): `timeline.recent_events(person_ids, *, limit, event_types, start, end)` and `portfolio.accounts_due_for_review(person_ids, *, stale_days, limit, today)`. Both are read-only and scope on `person_ids` (`None` = `record.read_all`, empty = no access → `[]`).

## Authorization model
- Route requires **`client.read`** (middleware RULE `^/workspace → client.read`, placed **before** `^/work` so `/workspace` is not mis-matched as `work.read`; also route-level `require_capability`).
- **Not** in `FIRM_WIDE_COLLECTION` — the workspace is book-scoped, not a firm-wide listing, so it does **not** require `record.read_all`. Every panel is scoped to the advisor's accessible clients; a `record.read_all` admin sees all.
- Per-panel **capability degradation**: a panel whose capability the principal lacks (e.g. `exception.read`, `task.read`) is returned empty rather than raising.
- Deep links target existing authorization-protected routes (`/people/{id}`, `/households/{id}`, `/tasks`, `/exceptions`), enforced at their destination.

## Tests
`tests/test_advisor_workspace.py` — capability + not-firm-wide; nav gating; authorized render; population from services; **record-scope exclusion of inaccessible clients from every panel**; meetings-today date filtering; empty states; deep links; **no policy-gated Advisor Intelligence content rendered**. Route-count guards updated 315 → 316.

## Exclusions (deferred to later Phase D slices)
Advisor Intelligence and all AI/recommendation content (Roth, tax planning, insurance-gap, suitability, retirement readiness, estate, business-owner, cross-selling); composite "relationship value"; historical financial-change / portfolio snapshots; Meeting Workspace; Client 360 Workspace; follow-up generation; new scheduler jobs; new tables/migrations; new workflow/notification/exception engines.

## Factual note on the architecture doc
`ADVISOR_WORKSPACE_ARCHITECTURE.md §1` lists "Upcoming reviews" as sourced from *workflow review instances + `accounts.last_review_date`*. No review-workflow template exists yet, so Phase D.1 sources this panel **only** from `accounts.last_review_date` (accounts due/overdue for review). The workflow-instance source remains a later-phase enhancement; no doc change is required beyond this note.
