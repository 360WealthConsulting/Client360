# Phase D.4 — Meeting Outcomes & Advisor Workflow

Fourth production slice of `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md` (§3), building on
D.1 (Daily Dashboard), D.2 (Client 360 Summary) and D.3 (Meeting Workspace brief).
Implemented on `release/0.13.0`. This is the **first WRITE surface** in the Advisor
Workspace.

## What was implemented
The Meeting Outcome page at `GET|POST /workspace/meetings/{person_id}/outcome`
(optional `?event={id}`). The page reuses the D.3 read-only context (client identity,
household, the **Client 360 snapshot** labelled *current values*, open work &
exceptions) and adds an editable, **factual** outcome record:

| Field | Recorded as | Authoritative service |
|---|---|---|
| Meeting completed (checkbox) | `meeting_completed` timeline event | `timeline.add_timeline_event` |
| Meeting notes / Decisions made / Additional comments | person notes (`note_type="meeting"`) | `notes.add_person_note` |
| Follow-up actions (× 3) | Work Management tasks | `tasks.create_task` |
| Next review recommendation | launches an existing review workflow | `workflow_automation.launch_workflow` |

Everything is a **factual record of what the advisor entered** — no generated text,
no recommendations, no AI. Blank fields write nothing (empty submit is a no-op).

## Orchestration only — no new engine
`advisor_workspace.record_meeting_outcome(...)` composes the four existing
person-keyed authoritative services. It performs **no direct table writes** and
introduces **no new task/note/timeline/workflow model, no new tables, no migrations,
no scheduler, and no notifications.** `get_meeting_outcome_context` simply returns the
D.3 `get_meeting_brief` composition (no new reads). Return value is a summary dict
`{"timeline": bool, "notes": int, "tasks": int, "workflow": id|None}`.

### Idempotency (no duplicate work)
- Follow-up tasks use a stable `idempotency_key=f"mtg-outcome:{person_id}:{title}"`
  (plus in-request dedup of repeated titles), so a double-submit or refresh never
  creates duplicate tasks — `create_task` also applies its own guard.
- The next-review launch uses `idempotency_key=f"mtg-review:{person_id}:{code}"`.

### Whitelisted review templates
Next review only launches when the submitted code is in
`_REVIEW_TEMPLATES = {"annual_review", "insurance_review"}` — both are **existing**
workflow templates. An arbitrary/unknown template code is ignored (no workflow
launched), so the form cannot be used to start off-list workflows.

## Authorization model
- `GET` requires **`client.read`**; `POST` requires **`client.write`** (middleware
  `^/workspace` rule + route dependency, with the middleware's `.read → .write`
  inference for the non-GET method).
- **`/workspace/meetings/{id}/outcome` is NOT covered by the middleware `RECORD_PATH`**
  (only `/people|/households/{id}` are), so both routes enforce person record-scope
  **explicitly**: `record_in_scope(principal, "person", person_id)` on GET and
  `record_in_scope(..., write=True)` on POST → **404** for an inaccessible person.
  Nothing is written for a person outside the advisor's book (tested).
- Not a firm-wide collection: the workspace is deliberately excluded from
  `FIRM_WIDE_COLLECTION`, so `record.read_all` is never granted here.
- The context is entirely **person-keyed** (household is name + link only), so it
  cannot expose other household members' data.

## Reused UI (no new design system)
Reuses `ui.page_head`, `ui.badge`, `ui.empty`, `wc.summary_card`, and base styles
(`.stat-grid`, `.card`, `.field`, `.input`, `.btn`, `table.data`, `.section-title`,
`.detail-label`, `.subtle`); `.detail-row` styling is pulled in via
`workspace.css` in the `{% block head %}`. No new CSS/JS, no new components.

## Exclusions (unchanged from the governing architecture)
No Advisor Intelligence / AI / regulated recommendations (Roth, tax, coverage-gap,
suitability, retirement readiness, estate, business-owner, cross-selling); no
notifications/reminders/scheduled jobs; no new scheduler, tables, migrations,
engines, note/task/timeline/workflow models; no Household Workspace or Executive
Dashboard.

## Tests
`tests/test_meeting_outcomes.py` (8): outcome writes reuse Timeline + Notes + Tasks +
Workflow; no duplicate tasks on double-submit (idempotent); only whitelisted review
template launches; empty outcome writes nothing; GET form authorized + inaccessible
person → 404; POST denies inaccessible person (nothing written); POST end-to-end →
303 redirect with `saved=1`; form has no AI / policy-gated recommendation content.
Route-count guards 317 → 319 (`test_f4_7`, `test_f4_8`).
