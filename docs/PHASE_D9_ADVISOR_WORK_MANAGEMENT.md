# Phase D.9 — Advisor Work Management

Advisor-facing operational work driven by Advisor Intelligence *recommendations*. D.9
**consumes** Advisor Intelligence; it does **not** modify it, execute recommendations,
automate compliance, change recommendation generation/IDs, or create a generalized
workflow engine. Work items are operational; recommendations remain advisory.
Implemented on `release/0.13.0`.

## Namespacing (no collision with the existing Work system)
The platform already has a Work Management system (`/work*` routes, `work.read`/
`work.write` capabilities, "My Work"/"Team Work", tasks/exceptions/workflow steps). D.9
is a **separate, namespaced layer** and touches none of it: route prefix `/advisor-work`
(the `^/work` middleware rule does not match `/advisor-work`), capabilities
`advisor_work.*`, tables `advisor_work_*`, nav item "Advisor Work".

## Architecture & dependency direction
```
Advisor Intelligence (advisor_intelligence.py)   — unchanged
        │ get_client_signals()  (one-way read, to snapshot a recommendation)
        ▼
Recommendation Registry / Rule metadata
        ▼
Advisor Work Service (services/advisor_work.py)
        ▼
Advisor Work Workspace (routes/advisor_work.py + /advisor-work UI)
```
Advisor Intelligence **never imports** Work Management (test-guarded); Work Management
consumes recommendations only. There is no workflow engine — the lifecycle is a small,
explicit set of transitions.

## Work model (`advisor_work_items`)
`id, recommendation_id, recommendation_type, governing_rule, rule_version, policy_gate,
priority, recommendation_snapshot (jsonb), person_id, household_id, owner_principal_id,
created_by, status, due_date, completed_at, completed_by, completion_notes, archived_at,
created_at, updated_at`. The **recommendation snapshot preserves** title, description
(summary), evidence, governing rule, rule version, priority, and policy gate (the full
`Signal.to_dict()`); it is an immutable copy — never a live reference. Deterministic
recommendation IDs from Advisor Intelligence are preserved and never altered.

## Status lifecycle (explicit transitions — no generic engine)
`new → assigned` (assign) / `in_progress` (start) / `cancelled`; `assigned → in_progress
/ waiting / completed / cancelled`; `in_progress ↔ waiting`, `→ completed / cancelled`;
`completed | cancelled → archived`. Every transition passes an `expected_status` and
locks the row (`SELECT … FOR UPDATE`); a stale submission fails loudly (`StaleWorkError`,
HTTP 409) rather than overwriting later changes. Status never changes by rendering a page.

## Creation & duplicate prevention
Creation is **explicit** (never automatic) and **idempotent**: an existing OPEN item for
the same (recommendation_id, person_id, governing_rule) is returned unchanged — a DB
**partial unique index** (`uq_open_advisor_work`, over statuses new/assigned/in_progress/
waiting) is the backstop. New work is allowed after completion, cancellation, or archive.
Only governed **recommendations** (`category == 'recommendation'`) are eligible; creation
enforces person record-scope first.

## Ownership
Assignment sets `owner_principal_id` (a `new` item becomes `assigned`); the item tracks
`created_by`, `owner_principal_id`, and `completed_by`. **No automated routing, no
workload balancing.**

## Recommendation integration (golden-safe)
The Client 360 workspace and Meeting Workspace pass an optional `work_index`
(recommendation_id → open work item) to the shared Advisor Intelligence renderer. Each
recommendation row then shows **"Create work"** (a POST to `/advisor-work`) or **"Work
exists →"** (a link to the item). The integration is strictly additive: when `work_index`
is absent (the dashboard and the D.5E golden), the rendered Advisor-Intelligence HTML is
**byte-for-byte unchanged** (verified by the golden regression). **Recommendations are
never suppressed** because work exists — they continue to appear until their underlying
facts change.

## Completion semantics
Completing work records `completed_at`, `completed_by`, and `completion_notes` and appends
a `completed` event. It **does NOT** suppress recommendations, mark them resolved, modify
their evidence, or alter their IDs — proven by a test asserting the serialized
recommendation set is identical before and after completion.

## Append-only history (`advisor_work_events`)
Every material change (creation, assignment, status transitions, completion, cancellation,
archive) appends an immutable event (`event_type, prior_status, new_status,
actor_principal_id, occurred_at, note`); a trigger blocks UPDATE/DELETE. Prior events are
never modified or deleted.

## Authorization
Four distinct capabilities (granted to the advisor, operations, and administrator roles):
`advisor_work.read` (view queue/detail), `advisor_work.create` (create from a
recommendation), `advisor_work.assign` (set owner), `advisor_work.update` (status /
completion). All endpoints are gated server-side via `require_capability`. `/advisor-work`
uses route-level gating (no middleware `.read` rule — the `.read→.write` inference would
demand a nonexistent `advisor_work.write`); the queue is book-scoped in the service (not
firm-wide). The existing `work.read` capability is a different system and is not reused.

## Concurrency
`expected_status` + `SELECT … FOR UPDATE` on every write reject stale updates; the partial
unique index prevents duplicate open work under races; assignment is a single locked write
(no lost assignments); the event ledger is append-only.

## UI
`/advisor-work` (WORK nav "Advisor Work"): a **queue** (search; filter by status/priority/
owner/recommendation type/governing rule/policy gate; sort; pagination) and a **detail**
view (recommendation snapshot + link, ownership, lifecycle, append-only history, and
explicit action forms — assign/start/wait/complete/cancel/archive — with confirmation). No
bulk actions, no deletion, no editing of history.

## Migration
`g1w2o3r4k5m6` (down `f8a9u1t2h3r4`) — creates `advisor_work_items` (+ partial unique
index + status CHECK) and the append-only `advisor_work_events` table, and seeds the four
capabilities into advisor/operations/administrator. Downgrade drops both tables, the
trigger, and the caps. Upgrade/downgrade verified. **No data backfilled.**

## Exclusions honored
No automated task generation, reminders, notifications, email/SMS/Slack, recurring work,
client communications, calendar integration, workflow engine, compliance approval, AI/LLM/
ML, recommendation suppression or execution, trade execution, document generation, or CRM
sync.

## Remaining technical debt
- Recommendation integration surfaces on the single-client workspaces (Client 360, Meeting)
  where person context is known; a dashboard "Create work" affordance (multi-client) is
  future work.
- Owner is stored as a principal id (rendered as `principal N`); a display-name join is a
  future readability nicety.
- The append-only trigger idiom would benefit from the deferred D.8A migration helper.
