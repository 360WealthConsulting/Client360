# Work Queue Adapter Guide (Phase D.39)

How to add a new source to the Unified Work Queue **safely** — as a composition adapter, never as
parallel domain logic. See [`UNIFIED_WORK_QUEUE.md`](UNIFIED_WORK_QUEUE.md), [`ADR-044`](adr/ADR-044-unified-work-queue.md).

## What an adapter is

An adapter (`app/services/work_queue/adapters.py`) reads a **bounded, record-scoped, actionable** set
from ONE authoritative service and maps each row to a `UnifiedWorkItem`. It:

- **only reads** — never mutates, never reads an `rm_*` projection table directly;
- **fails closed** — any error → an empty list (an item never appears because scope was undetermined);
- declares a **base capability** (to query the source) and a **per-item capability** (for suppression);
- declares each item's **allowed_actions** and a **deep link** (no dead-end items).

## Prerequisites for a new source (all required)

A source may be adopted only if the authoritative service already provides:

1. a **bounded list of actionable items** scoped to the principal (record-scope enforced in the service);
2. a **stable id**;
3. **record-scope enforcement** on both the list and any action;
4. at least one **deep link** to open the item's source record.

If any is missing, **exclude** the source (document it) rather than inventing the missing behavior. See
the excluded list in `UNIFIED_WORK_QUEUE.md`.

## Adding an adapter

```python
class FooAdapter(Adapter):
    domain = "foo"                 # a SOURCE_DOMAINS entry
    capability = "foo.read"        # base capability to query the source
    label = "Foo"
    actions = ("open",)            # actions this domain supports in the queue

    def _fetch(self, principal, limit):
        from app.services.foo import list_foo
        return list_foo(principal, page=1, page_size=limit).get("rows", [])   # scoped + bounded

    def _to_items(self, rows, principal, now):
        for r in rows:
            if _is_closed(r):       # only actionable items
                continue
            yield make_item(
                source_domain="foo", source_type="foo_item", source_id=r["id"],
                title=r.get("title"), status=r.get("status"), priority=r.get("priority"),
                capability="foo.read", deep_link=f"/foo/{r['id']}",
                allowed_actions=self.actions, due_at=r.get("due_date"),
                person_id=r.get("person_id"), household_id=r.get("household_id"),
                created_at=r.get("created_at"), now=now,
                source_reference={"entity_type": "foo_item", "entity_id": r["id"]})
```

Then wire it up:
1. add the instance to `ADAPTERS`;
2. add `"foo"` to `SOURCE_DOMAINS` and to `DOMAIN_CAPABILITY`;
3. if the source supports actions, add them to `dispatch.ALLOWED_ACTIONS["foo"]` + map each action to an
   authoritative call in `dispatch._delegate` (never mutate in the adapter/dispatch itself), and give
   each action a capability floor in `dispatch.ACTION_CAPABILITY`;
4. optionally add a built-in tab view in `views.BUILTIN_VIEWS`;
5. run `tests/test_unified_work_queue.py` — governance must stay clean.

## Rules the adapter must obey (governance-enforced)

- **No `rm_*` reads** — use the authoritative list or the D.37 adoption count helpers.
- **No mutation** in `adapters.py` / `service.py` / `summary.py` (no `.insert()/.update()/.delete()` on
  authoritative tables) — governance flags `direct_authoritative_mutation` / `direct_projection_table_read`.
- **Every item has a deep link and a stable `work_item_key`** — governance flags `dead_end_work_item` /
  `work_item_without_source_reference`.
- **Only expose actions the source supports** — `allowed_actions` ⊆ the domain's real action set;
  governance flags `source_without_action_map` and unmapped actions.
- **Preserve the source status** — set `status`; `status_group` is derived for display/filter only.
- **References, not payloads** — put ids/refs in `source_reference`, not copied business state; never
  expose a sensitive field the principal cannot already see.

## Names + performance

Do not resolve display names in the adapter (avoids N+1). The service resolves assignee/person/household
names for the **visible page only**. Keep `_fetch` bounded (use the passed `limit`).
