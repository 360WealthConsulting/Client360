# Projection Usage Guide (Phase D.37)

How to adopt a projection into a read surface correctly. Every adopted read MUST obey these rules;
`governance.validate_adoption()` enforces them.

See also: [`READ_SURFACE_ADOPTION.md`](READ_SURFACE_ADOPTION.md), [`ADR-042`](adr/ADR-042-read-surface-adoption.md).

## The one rule

> An adopted read consults the projection through `app/services/projections/adoption.py`, uses the
> projection ONLY when the helper returns a value, and otherwise runs its **unchanged authoritative
> read**. It never queries an `rm_*` table directly, never mutates a projection, never reconstructs
> business logic, and never bypasses RBAC / runtime / policy.

## The API

```python
from app.services.projections import adoption

# Firm-level count (no record scope). Serves from the projection when healthy + fresh.
n = adoption.count("operations.projects", principal, firm_level=True,
                   status_col="status", status_in=("active",))
if n is not None:
    return n
# ... else fall through to the authoritative query (unchanged) ...

# A read that IS record-scoped when not firm-wide: pass firm_level=False so a scoped
# principal (no record.read_all) is refused the projection and gets the authoritative read.
pc = adoption.count("people.summary", principal, firm_level=False)
if pc is not None:
    return pc
# ... else authoritative scoped/firm read ...

# The firm activity feed (list, not a count):
rows = adoption.recent_feed(principal, limit=50)   # firm-wide only; None → authoritative timeline
```

### `firm_level` — the RBAC switch

- `firm_level=True` — the read is inherently firm-level (a firm-wide count with no record scope, e.g.
  active projects, open tasks). The projection may be served to any principal.
- `firm_level=False` — the read is record-scoped for non-firm principals. The projection is served
  ONLY when `principal.can("record.read_all")`; a scoped principal is refused (helper returns `None`)
  and MUST get the authoritative, scoped read. This is how record-level RBAC is preserved: a
  references-only projection carries no scope anchor, so it never answers a scoped read.

### Status filters (data filters, not business rules)

`count(...)` accepts `status_col` + one of `status_in`, `status_not_in`, or `null_col`. These mirror the
data filter the authoritative read already applies (e.g. "open" opportunities). They are **data
filters** — copy the exact predicate the authoritative read uses. Do NOT compute a business decision in
the adoption layer; business rules live in the runtime/policy engines and the domain services.

## Do / Don't

**Do**
- Return the projection value only when the helper returns non-`None`.
- Keep the authoritative read exactly as it was — it is the fallback and the source of truth.
- Add each new adoption to `ADOPTION_TARGETS` and keep the site inside an `ADOPTION_MODULES` module.
- Keep every capability / runtime / policy check on the surface unchanged.

**Don't**
- Don't `SELECT ... FROM rm_*` directly in a read surface — always go through the helper (a direct
  `rm_*` reference in an adoption module is flagged as a *mixed read*).
- Don't call `adoption.count` / `recent_feed` without a fallback — a call with no `is not None` guard is
  flagged as a *projection bypass*.
- Don't serve a projection to a scoped principal — always pass `firm_level=False` for scoped reads.
- Don't write to a projection from a read surface, and don't add business logic to the projection.
- Don't map two targets to the same read function (flagged as a *duplicate query implementation*).

## Freshness + fallback

`should_use` gates on **health = healthy** and **lag ≤ 100** (`FRESHNESS_LAG_THRESHOLD`). Anything else
(unbuilt, lagging, stale, failed, or a scoped principal) → `None` → authoritative fallback. Because
projections are dark-launched, this means: **by default every adopted read falls back to authoritative,
so behavior is unchanged.** Once an operator enables + rebuilds, firm-wide reads begin serving from the
projection. Adoption usage (reads vs fallbacks) is visible via `adoption.usage_stats()` /
`GET /projections/adoption` / the analytics metrics.

## Adding a new adoption

1. Add `projection_id → "module.read_fn"` to `ADOPTION_TARGETS`.
2. In the read function (inside an `ADOPTION_MODULES` module), call `adoption.count(...)` /
   `recent_feed(...)` first, return its value when non-`None`, else run the existing authoritative read.
3. Add the query-cost row to `ADOPTION_INVENTORY` (authoritative vs projection, joins avoided).
4. Register any new analytics metric in `metrics.py`.
5. Run `tests/test_read_surface_adoption.py` — governance must stay clean.
