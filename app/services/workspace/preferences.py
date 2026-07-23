"""Advisor Workspace personalization store (Phase D.38).

Per-advisor workspace VIEW STATE — widget order, hidden widgets, pinned favorites, remembered
filters, and named saved presets. This is personal UI settings, not business data: reads/writes are
self-service and always scoped to the acting user's own ``user_id``. No authoritative business logic,
no ledger, no cross-user access. The workspace page is gated by ``client.read``; mutations here are
gated by ``workspace.personalize`` at the route.
"""
from __future__ import annotations

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import engine, workspace_preferences, workspace_presets

from .registry import DEFAULT_ORDER, WIDGETS

_DEFAULTS = {"widget_order": None, "hidden_widgets": [], "pinned_widgets": [], "filters": {},
             "active_preset_id": None}


def _effective_order(stored):
    """Merge a stored order with the registry: keep stored keys that still exist, then append any
    new registry widgets at the end (so a newly added widget shows up without a migration)."""
    known = set(DEFAULT_ORDER)
    order = [k for k in (stored or []) if k in known]
    order += [k for k in DEFAULT_ORDER if k not in order]
    return order


def get_preferences(user_id) -> dict:
    """The user's live preferences (defaults if they have never personalized)."""
    with engine.connect() as c:
        row = c.execute(select(workspace_preferences)
                        .where(workspace_preferences.c.user_id == user_id)).mappings().first()
    if row is None:
        prefs = dict(_DEFAULTS)
    else:
        prefs = {k: row[k] for k in _DEFAULTS}
    prefs["order"] = _effective_order(prefs.get("widget_order"))
    prefs["hidden"] = list(prefs.get("hidden_widgets") or [])
    prefs["pinned"] = list(prefs.get("pinned_widgets") or [])
    prefs["filters"] = dict(prefs.get("filters") or {})
    return prefs


def _write(user_id, values):
    """Upsert the single preferences row for a user."""
    values = {**values, "updated_at": func.now()}
    with engine.begin() as c:
        existing = c.execute(select(workspace_preferences.c.id)
                             .where(workspace_preferences.c.user_id == user_id)).scalar()
        if existing is None:
            c.execute(insert(workspace_preferences).values(user_id=user_id, **values))
        else:
            c.execute(update(workspace_preferences)
                      .where(workspace_preferences.c.user_id == user_id).values(**values))


def move_widget(user_id, key, direction):
    if key not in WIDGETS:
        return
    prefs = get_preferences(user_id)
    order = prefs["order"]
    i = order.index(key)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(order):
        order[i], order[j] = order[j], order[i]
        _write(user_id, {"widget_order": order})


def hide_widget(user_id, key):
    if key not in WIDGETS:
        return
    prefs = get_preferences(user_id)
    if key not in prefs["hidden"]:
        _write(user_id, {"hidden_widgets": [*prefs["hidden"], key]})


def show_widget(user_id, key):
    prefs = get_preferences(user_id)
    if key in prefs["hidden"]:
        _write(user_id, {"hidden_widgets": [k for k in prefs["hidden"] if k != key]})


def pin_widget(user_id, key):
    if key not in WIDGETS:
        return
    prefs = get_preferences(user_id)
    if key not in prefs["pinned"]:
        _write(user_id, {"pinned_widgets": [*prefs["pinned"], key]})


def unpin_widget(user_id, key):
    prefs = get_preferences(user_id)
    if key in prefs["pinned"]:
        _write(user_id, {"pinned_widgets": [k for k in prefs["pinned"] if k != key]})


def set_filter(user_id, key, value):
    prefs = get_preferences(user_id)
    filters = dict(prefs["filters"])
    if value in (None, "", {}):
        filters.pop(key, None)
    else:
        filters[key] = value
    _write(user_id, {"filters": filters})


def reset(user_id):
    """Clear the user's personalization back to registry defaults."""
    _write(user_id, {"widget_order": None, "hidden_widgets": [], "pinned_widgets": [],
                     "filters": {}, "active_preset_id": None})


# --- saved presets (named layouts) -------------------------------------------

def list_presets(user_id) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(select(workspace_presets)
                         .where(workspace_presets.c.user_id == user_id)
                         .order_by(workspace_presets.c.is_favorite.desc(),
                                   workspace_presets.c.name.asc())).mappings().all()
    return [dict(r) for r in rows]


def save_preset(user_id, name, *, is_favorite=False):
    """Snapshot the user's current layout as a named preset (upsert on name)."""
    name = (name or "").strip()
    if not name:
        return None
    prefs = get_preferences(user_id)
    layout = {"order": prefs["order"], "hidden": prefs["hidden"],
              "pinned": prefs["pinned"], "filters": prefs["filters"]}
    stmt = pg_insert(workspace_presets).values(
        user_id=user_id, name=name, layout=layout, is_favorite=is_favorite)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_workspace_preset_user_name",
        set_={"layout": layout, "is_favorite": is_favorite,
              "updated_at": func.now()})
    with engine.begin() as c:
        c.execute(stmt)


def apply_preset(user_id, preset_id):
    with engine.connect() as c:
        row = c.execute(select(workspace_presets)
                        .where(workspace_presets.c.id == preset_id,
                               workspace_presets.c.user_id == user_id)).mappings().first()
    if row is None:
        return
    layout = row["layout"] or {}
    _write(user_id, {"widget_order": _effective_order(layout.get("order")),
                     "hidden_widgets": list(layout.get("hidden") or []),
                     "pinned_widgets": list(layout.get("pinned") or []),
                     "filters": dict(layout.get("filters") or {}),
                     "active_preset_id": preset_id})


def delete_preset(user_id, preset_id):
    with engine.begin() as c:
        c.execute(delete(workspace_presets)
                  .where(workspace_presets.c.id == preset_id,
                         workspace_presets.c.user_id == user_id))
