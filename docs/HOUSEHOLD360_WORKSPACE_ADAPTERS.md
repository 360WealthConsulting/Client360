# Household 360 Workspace Adapters (Phase D.41)

How to add or change a Household 360 section **safely** — as a composition, never as new domain logic.
See [`HOUSEHOLD360_WORKSPACE.md`](HOUSEHOLD360_WORKSPACE.md), [`ADR-046`](adr/ADR-046-household360-workspace.md).

## What a household section builder is

A household section builder (`app/services/client360/household.py`) composes ONE section by reusing the
authoritative domain reads, fanning out over **in-scope members** where needed. It:

- **only reads** — never mutates, never recomputes a domain calculation (esp. never re-sums member
  portfolios — reuse `get_household_portfolio`), never reads an `rm_*` projection table, never defines a
  table (no shadow household/person record);
- receives the shared household `ctx` (`household_id`, `household_name`, `portfolio`, `roster`, `members`
  [in-scope], `member_ids`, `suppressed_members`, `primary`) — record scope is **already verified once**
  at the boundary and members are **already filtered** by `accessible_person_ids`;
- returns a plain dict (references, not duplicated business payloads);
- is registered in `HOUSEHOLD_SECTIONS` with a **capability** (the tab is hidden without it) and fails
  closed (an exception is isolated per section → `{"error": ...}`).

## Member fan-out

- Iterate `ctx["member_ids"]` (already scope-filtered). Prefer **batch-by-people** reads where they
  exist — `opportunities_for_people`, `reviews_due_for_people`, `open_exceptions_for_people`,
  `open_tasks_for_people` — over N single calls.
- Per-member summaries (`client_engagement_summary`, `client_policy_summary`, `client_benefits_summary`,
  `get_person_portfolio`) are safe once the member is in `ctx["member_ids"]`.
- Attribute results to members by `person_id` and label provenance (member vs household).

## Adding a household section

```python
# household.py
def _foo(principal, ctx):
    from app.services.foo import foo_for_people   # or per-member
    rows = _safe(lambda: foo_for_people(set(ctx["member_ids"])), [])
    for r in rows:
        r["member_name"] = _name(ctx, r.get("person_id"))
    return {"rows": rows}

_SECTION_BUILDERS["foo"] = _foo
HOUSEHOLD_SECTIONS = (..., ("foo", "foo.read"))   # capability gates the tab
```

Then add a rendering branch in `app/templates/client360/household.html` and a test in
`tests/test_household360_workspace.py`. Governance must stay clean.

## Rules (governance-enforced)

- **No `rm_*` reads**, **no `.insert()/.update()/.delete()`**, **no `publish_safe`/`write_audit_event`**,
  **no `Table(...)`** in `household.py`.
- **Reuse the single portfolio aggregation** — `get_household_portfolio` for the household total; never
  call `aggregate_portfolio` or re-sum member portfolios (flagged `duplicate_portfolio_aggregation`).
- **Never fabricate net worth** — banking/retirement/outside-assets/liabilities are unmodelled; keep
  `not_tracked` markers, never compute a composite.
- **Never infer filing/dependency/joint relationships** from membership (keep the
  `inferred_relationships: False` marker in the tax section).
- **Reuse D.39 for work** (`compose_queue`), never re-query task/workflow/exception domains.
- **Deep link, never mutate** — a section links into the owning surface; edits are quick actions.
- **Fail closed** — a member not in `ctx["member_ids"]` is already suppressed; do not re-add out-of-scope
  members.

## Household-level vs member-level reads

Prefer a household-level authoritative read where one exists (`get_household_portfolio`,
`household_timeline`, `documents_for_entity("household")`, `open_count_for_client(None, hid)`,
`compose_queue(filters={"household_id": hid})`). Otherwise fan out per in-scope member. There is **no**
household-level relationship read — the graph is composed from per-member one-hop graphs (deduped,
depth-capped, cycle-protected).
