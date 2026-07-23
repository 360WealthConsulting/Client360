# Client 360 Workspace Adapters (Phase D.40)

How to add or change a Client 360 section **safely** — as a composition, never as new domain logic.
See [`CLIENT360_WORKSPACE.md`](CLIENT360_WORKSPACE.md), [`ADR-045`](adr/ADR-045-client360-workspace.md).

## What a section builder is

A section builder (`app/services/client360/sections.py`) composes ONE section by reusing ONE
authoritative domain read. It:

- **only reads** — never mutates, never recomputes a domain calculation, never reads an `rm_*`
  projection table directly, never defines a table (no shadow client record);
- receives a shared `ctx` (`entity_type`, `person_id`, `household_id`, `portfolio`, `subject`,
  `members`, `scope_ids`, `snapshot`, `last_contact`, `next_activity`) — record scope is **already
  verified once** by `service.get_workspace` before any builder runs;
- returns a plain dict (references, not duplicated business payloads);
- is registered in `registry.SECTIONS` with a **capability** (the tab is hidden without it) and fails
  closed (an exception is isolated per section → `{"error": ...}`).

## Adding a section

```python
# sections.py
def foo(principal, ctx):
    """Compose the Foo section from the authoritative Foo read."""
    from app.services.foo import client_foo_summary
    pid = ctx.get("person_id")
    return {"summary": client_foo_summary(pid) if pid else {}}
```

```python
# registry.py
SectionDef("foo", "Foo", "foo.read", sections.foo),   # capability gates the tab
```

Then add a rendering branch in `app/templates/client360/workspace.html` and a test in
`tests/test_client360_workspace.py`. Governance must stay clean.

## Rules (governance-enforced)

- **No `rm_*` reads**, **no `.insert()/.update()/.delete()`**, **no `publish_safe`/`write_audit_event`**,
  and **no `Table(...)`** in the composition modules — governance flags
  `direct_projection_table_read`, `direct_mutation`, `outbox_or_audit_write_in_composition`,
  `shadow_client_record_table`.
- **Reuse, do not recompute** — call the authoritative domain read; never re-implement its math (e.g.
  reuse `aggregate_portfolio` via `get_person_portfolio`, never re-sum accounts; never compute a net
  worth the platform does not model).
- **References, not payloads** — return ids/refs and small display values, not copied business state;
  never expose a sensitive field the principal cannot already see.
- **Deep link, never mutate** — a section links into the owning surface; edits are quick actions.
- **Capability-gate the tab** — set the `SectionDef.capability` so the section is omitted (not
  shown-then-403) without it.

## Scope-enforcement cheat sheet

Record scope is verified ONCE at the workspace boundary. Domain reads that **self-check** scope are
safe to call with just a `person_id` (`opportunities_for_person`, `get_client_signals`, `person_reviews`,
`open_session_for`, `person_work`, `client_timeline`, `household_timeline`). Reads that **do not
self-check** (`client_engagement_summary`, `client_policy_summary`, `client_benefits_summary`,
`open_exceptions_for_client`, `documents_for_entity`, `recent_events`) rely on that single boundary
check — do not add a section that calls them from an unscoped entry point.

## Unmodelled concepts

If the spec asks for a concept the platform does not model (banking, retirement accounts, outside
assets, liabilities, net worth, client status/tier/risk), surface it as **"not tracked"** — do not
invent a domain or a calculation. Adding a real domain is a separate, ADR-reviewed change.
