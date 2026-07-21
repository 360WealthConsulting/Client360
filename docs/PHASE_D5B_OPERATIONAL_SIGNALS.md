# Phase D.5B — Deterministic Operational Signals

Sixth production slice of `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md` (§4, §7). Activates
the Phase D.5A Advisor Intelligence framework with a small set of **factual,
deterministic, operational** signals. Operational awareness only — **no regulated
recommendations, no AI, no probabilistic scoring, no policy interpretation.**
Implemented on `release/0.13.0`.

## Implemented signal types
Four producers, each composing an **existing authoritative, record-scoped read**
(no domain-table access, no status/eligibility recomputation):

| Signal (registry key) | Authoritative source read | Priority | Route |
|---|---|---|---|
| **Client review overdue** (`client_review_overdue`) | `portfolio.accounts_due_for_review(person_ids, stale_days=365, today)` | High if never reviewed, else Medium | `/people/{id}` |
| **Open client exception** (`open_client_exception`) | `exception_engine.open_exceptions_for_people(person_ids)` | From source severity: `blocker→Critical, high→High, medium→Medium, low→Low` | `/people/{id}` (else `/exceptions`) |
| **Overdue open task** (`overdue_open_task`) | `tasks.open_tasks_for_people(person_ids)` + factual `due_date < today` | High if >30 days overdue, else Medium | `/people/{id}?tab=tasks` |
| **Upcoming client meeting** (`upcoming_client_meeting`) | `timeline.recent_events(person_ids, event_types=("calendar_event",), start, end)` | Medium | `/workspace/meetings/{id}?event={ev}` |

No other signal types were added. All explicitly excluded regulated types (Roth,
tax-planning, harvesting, allocation, coverage gaps, replacement/1035/rollover,
suitability, retirement-readiness, Social Security, estate, beneficiary,
cross-selling, business-owner, plan-design, licensing/compliance conclusions, risk/
probability scores, predictive/AI/ML) are **not** implemented.

## Authoritative services reused
- `portfolio.accounts_due_for_review` — existing, already `person_ids`-scoped.
- `timeline.recent_events` — existing; the exact read the Daily Dashboard uses, with
  the same firm timezone (`advisor_workspace.FIRM_TZ`) and day-window logic.
- **Two smallest-possible read additions to the owning services** (an exact
  book-scoped list was missing; per-person/principal reads existed):
  - `exception_engine.open_exceptions_for_people(person_ids, *, limit)` — open
    exceptions across a set of person ids; returns id, domain, category, severity,
    status, opened_at, sla_due_at, title, person_id, household_id.
  - `tasks.open_tasks_for_people(person_ids, *, limit)` — non-closed tasks across a
    set of person ids; returns id, title, due_date, status, person_id, household_id.
  Both mirror the existing scope contract (`None`=read_all, empty=`[]`) and reuse the
  existing status vocabulary; neither recomputes status.

The Advisor Intelligence layer itself queries **no domain tables directly**.

## Deterministic IDs
Each signal id is `f"{signal_type}:{source_record_type}:{source_record_id}"`
(e.g. `client_review_overdue:account:42`, `open_client_exception:exception:7`). The
id is globally unique per underlying record, so the **same fact never produces a
duplicate signal**; `_collect` also de-duplicates by id. Ordering is deterministic:
`(priority.rank desc, id asc)` — no scoring, no time-dependent tiebreak.

## Priority mapping (from evidence only)
- **Critical** — only when the authoritative exception source labels it its most
  severe (`blocker`).
- **High** — never-reviewed account; blocker/high exception; task >30 days overdue.
- **Medium** — ordinary overdue review/task; upcoming meeting requiring preparation.
- **Low / Informational** — lower-severity exceptions from the source.

Materiality thresholds (`_MATERIAL_REVIEW_STALE_DAYS=365`, `_MATERIAL_TASK_OVERDUE_DAYS=30`)
are deterministic comparisons over authoritative evidence, **not scores**. Severity is
never invented beyond the source.

## Explainability and evidence
Every emitted signal carries a factual title, a factual summary (e.g. "Account review
is overdue (never reviewed).", "Exception remains open.", "Task is overdue by N
day(s).", "Meeting is scheduled within the preparation window."), a `why`, the
authoritative `source_service`, a `SourceRecord` reference, evidence fields sufficient
to reproduce the signal, deterministic `confidence=1.0`, `PolicyGate.NONE`, a
protected destination route, and `status`. Summaries use factual language only — no
"opportunity", "recommend", "suitable", "should …", or scoring/probability language.

## Authorization model
Preserves the D.5A **scope-first** behavior. Scope is resolved **before** any
producer runs and producers read strictly by the resolved `person_ids`:
- `get_client_signals` / `get_household_signals` enforce `record_in_scope` first; an
  inaccessible person/household returns `()` and **never reaches producer logic**
  (proven by tests: the same records produce for a `record.read_all` principal, so the
  empty result is the authorization gate, not absent data).
- `get_dashboard_signals` scopes to the advisor's book (`accessible_person_ids`);
  household scope resolves to member person ids.
- Producers query only by accessible `person_ids`, so evidence fields, counts, routes,
  dates, and summaries can never reference an inaccessible record. Destination routes
  are all independently authorization-protected (`/people/{id}`, `/exceptions`,
  `/workspace/meetings/{id}`).

## UI behavior
The existing Advisor Intelligence dashboard panel (`GET /workspace`) now renders
populated signals in an existing `table.data` — columns Signal (link to the protected
route), Priority (`ui.badge`), Detail (factual summary), Source, Gate (badge only when
not `NONE`). Reuses existing badges/links/empty-state/design tokens; the empty state is
retained when there are no signals. **No** scores, generated content, approve/reject,
dismiss/snooze, workflow, task-creation, or compliance controls are rendered. Client
360 / Meeting Workspace rendering was intentionally **not** added (kept the PR focused;
the same scoped `get_client_signals` output can feed them in a later slice).

## Exclusions honored
No regulated/advisory signal types; no recommendation/advice language; no AI/LLM/ML/
embeddings/vector/predictive/historical analytics; no probabilistic scoring; no policy/
compliance/suitability/licensing conclusions; no cross-selling/business-owner signals.

## No persistence or action layer
Signals are computed **read-only** objects. No signal/history tables, no migrations, no
acknowledge/dismiss/snooze/assignment state, no notifications, no workflow or automatic
task creation, no audit decisions or approval records. No new route (route-count guards
unchanged, 319).

## Compliance boundary
This phase carries only factual operational awareness (`PolicyGate.NONE`). Regulated
signals remain `[Policy-gated]` and are not implemented: they stay inert until the firm
supplies rules/thresholds **and** an accountable compliance owner exists
(`V1_RISK_REGISTER.md` GOV-2, `PRODUCT_DECISIONS.md` PD-4). The `PolicyGate` values
other than `NONE` remain display-only placeholders.

## Remaining technical debt (future phases — not in this slice)
- The set-scoped exception/task reads are person-keyed and therefore **under-inclusive**
  for household-only (null person_id) or organization-anchored exceptions — safe (never
  over-inclusive/leaking), but a household/org-anchored read could be added later.
- Rendering scoped signals in the Client 360 / Meeting workspaces (reusing
  `get_client_signals`).
- Governed signal disposition (acknowledge/evidence ledger) and any `[Policy-gated]`
  signal remain deferred to a governed, compliance-owned phase.
