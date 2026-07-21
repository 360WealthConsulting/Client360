# Phase D.2 — Client 360 Summary

Second production slice of `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md` (§2), building
directly on the Phase D.1 Daily Dashboard. Implemented on `release/0.13.0`.

## What was implemented
A read-only **Client 360 summary** at the top of the person profile Overview
(`/people/{id}?tab=overview`): a **factual per-domain relationship snapshot** shown
side by side and **never summed** into a composite figure (the units are not
comparable). Cards: Client AUM · Cash (%) · Insurance (policy count + coverage) ·
Tax engagements (active) · Open exceptions (attention) · Open tasks (agenda). Reuses
the existing design system (`wc.summary_card`, `stat-grid`); no new CSS/JS. The
existing Overview content (Client Details, meetings, activity, tasks, documents,
recommendations) is unchanged.

## Authoritative services reused (composition, not duplication)
| Value | Source |
|---|---|
| Client / Household AUM, Cash | reused from the already-computed `get_person_portfolio` in the Overview context (no recompute) |
| Insurance policies + coverage | `insurance.client_policy_summary(person_id, household_id)` *(new read)* |
| Active tax engagements | `tax_domain.client_engagement_summary(person_id, household_id)` *(new read)* |
| Open exceptions | `exception_engine.open_count_for_client(person_id, household_id)` *(new read)* |
| Open tasks | reused from the Overview's `open_tasks` count (no recompute) |
| Composition | `advisor_workspace.get_client_snapshot(person_id, household_id, *, portfolio, open_task_count)` |

The three new methods are **read-only** and added to their **owning** services (not
queried from the route). They are strictly person/household-keyed.

## Authorization model
`/people/{id}` is already record-scoped by the middleware `RECORD_PATH` — an
inaccessible person's profile is denied before the handler runs. The snapshot reads
are keyed by `person_id`/`household_id`, so they can only ever reflect the client
whose (already-authorized) profile is being viewed; a test proves no cross-client
leak. No new route, capability, or scope mechanism was introduced.

## Tests
`tests/test_client_360_summary.py` — per-domain composition; **never summed** (asserts
no composite key); **person-keyed, no cross-client leak**; empty/zeroed snapshot; and
the Overview renders the Client 360 section. No route-count change (no new route).

## Exclusions (still honored; deferred to later slices)
Advisor Intelligence / AI recommendations (the existing `advisor_recommendations` on
the Overview is untouched and not expanded); composite "relationship value"; Roth /
tax-planning / insurance-gap / suitability / retirement / estate / business-owner /
cross-selling; historical financial-change / portfolio snapshots; Meeting Workspace;
follow-up generation; new scheduler jobs, tables, migrations, or engines.

## Factual notes
- **Insurance "in force":** the snapshot counts **all** of a client's policies and sums
  face amount (factual). It does **not** filter to an "in force" status set, because
  which statuses count as in-force is a business decision — deferred as a later
  refinement rather than guessed here.
- **Upcoming reviews / benefits:** benefits (person-linked via `benefit_employments`)
  is intentionally not yet in the snapshot; a benefits card is a small future add.
