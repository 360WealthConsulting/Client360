# Phase D.3 — Meeting Workspace Brief

Third production slice of `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md` (§3), building on
D.1 (Daily Dashboard) and D.2 (Client 360 Summary). Implemented on `release/0.13.0`.

## What was implemented
A read-only, **factual** one-screen **Meeting Workspace brief** at
`GET /workspace/meetings/{person_id}` (optional `?event={id}`). Sections: meeting
context (when launched from a synced meeting) · household context · the **Client 360
snapshot** (labelled *current values*) · benefits facts · meeting agenda (open
tasks) · open exceptions · reviews due · recent activity · recent notes &
communications. Reuses the existing design system (`ui`, `wc.summary_card`,
`table.data`, badges, empty states); no new CSS/JS; printable one-screen layout (no
PDF generation).

**Entry points:** the Daily Dashboard "Meetings today" rows now link to
`/workspace/meetings/{person_id}?event={event_id}`; the Client 360 Overview shows a
"Prepare for meeting →" link. All existing person/household/dashboard/specialist
routes are preserved.

## Authoritative services reused (composition only)
| Section | Source |
|---|---|
| Client 360 snapshot | `advisor_workspace.get_client_snapshot` (D.2 reuse) over `get_person_portfolio` |
| Benefits facts | `benefits_domain.client_benefits_summary(person_id)` *(new, person-keyed)* |
| Open tasks (agenda) | `tasks.tasks_with_assignee(person_id)` |
| Open exceptions | `exception_engine.open_exceptions_for_client(person_id)` *(new)* |
| Reviews due | `portfolio.accounts_due_for_review({person_id})` |
| Recent activity | `timeline.get_person_timeline(person_id)` |
| Notes & communications | `notes.list_person_notes(person_id)` |
| Meeting event | `timeline.get_event(event_id)` *(new; validated)* |
| Composition | `advisor_workspace.get_meeting_brief(person_id, *, event_id)` |

Three small **read-only** methods were added to their **owning** services:
`timeline.get_event`, `exception_engine.open_exceptions_for_client`,
`benefits_domain.client_benefits_summary`. No writes; no meeting/task/follow-up
creation; no notifications; no recommendations.

## Authorization model
- `GET /workspace/meetings/{id}` requires **`client.read`** (middleware `^/workspace`
  rule + route dependency).
- **`/workspace/meetings/{id}` is NOT covered by the middleware `RECORD_PATH`** (only
  `/people|/households/{id}` are), so the route enforces person record-scope
  **explicitly**: `record_in_scope(principal, "person", person_id)` → **404** for an
  inaccessible person. It is deliberately not a firm-wide collection.
- The brief's lists are all **person-keyed** (`household_id` is intentionally *not*
  passed to the person-keyed reads), so it cannot expose other **household members'**
  data. Benefits is keyed by `benefit_employments.person_id`, never by organization,
  so it cannot expose unrelated employees. Household context is name + link only (no
  member roster).

## Event-to-person validation
When `?event` is supplied, the selected event is used **only if** it exists, is a
`calendar_event`, and its `person_id` equals the requested person. Otherwise it is
ignored and a general brief is rendered — an arbitrary or another client's event can
never surface meeting metadata (tested).

## Factual / current-data limitation
The financial section shows **current values only**, explicitly labelled "Current
values … not historical change and not a composite total." There is **no**
"changes since last meeting", no performance/return math, and no composite
"relationship value." Historical portfolio snapshots remain a **blocked dependency**
(`WEALTH_ARCHITECTURE.md §10`), so historical comparison is out until they are
populated.

## Exclusions (deferred to later phases)
Advisor Intelligence / AI / regulated recommendations (Roth, tax-planning,
coverage-gap, suitability, retirement readiness, estate, business-owner,
cross-selling); editable agenda notes; follow-up generation; notifications; PDF
export; new scheduler jobs, tables, migrations, or engines.

## Tests
`tests/test_meeting_workspace.py` (11): capability + not-firm-wide; inaccessible
person → 404; valid calendar-event context; another person's event omitted;
non-calendar event omitted; general brief without event; person-keyed no
cross-client leak; snapshot reuse + "current values" label; no advisor-intelligence
/ historical-change content; Daily-Dashboard link → Meeting Workspace; Overview link
→ Meeting Workspace. Route-count guards 316 → 317.
