"""Advisor Workspace orchestrator (Phase D.38).

``get_workspace`` composes the personalized advisor home: a greeting, the TODAY summary row, the
deterministic PRIORITIES panel, and the personalized WIDGET GRID (the advisor's order, with hidden
widgets removed and pinned favorites floated to the top). It reuses the existing record-scoped daily
dashboard for the detail panels + priorities, computes each widget through the projection-backed
sources (graceful fallback), and applies the advisor's saved preferences. Read-only, capability-aware,
never bypasses RBAC — a widget the principal cannot open is never assembled.
"""
from __future__ import annotations

from datetime import datetime

from app.services.advisor_workspace import get_daily_dashboard

from . import digest, preferences
from .registry import WIDGETS, WidgetDef
from .widgets import FIRM_TZ, compute_widget


def _greeting(now) -> str:
    h = now.hour
    if h < 12:
        return "Good Morning"
    if h < 17:
        return "Good Afternoon"
    return "Good Evening"


def _widget_view(key: str, principal, *, now, filters, pinned: bool) -> dict:
    w: WidgetDef = WIDGETS[key]
    return {
        "key": w.key, "title": w.title, "section": w.section, "kind": w.kind,
        "detail_href": w.detail_href, "projection_backed": w.projection_backed,
        "description": w.description, "pinned": pinned,
        "data": compute_widget(key, principal, now=now, filters=filters.get(key)),
    }


def get_workspace(principal, *, now=None) -> dict:
    """Compose the personalized advisor home for ``principal``. Never mutates."""
    now = now or datetime.now(FIRM_TZ)
    prefs = preferences.get_preferences(principal.user_id)

    # Eligible = the widgets whose capability the principal holds, in the advisor's order.
    eligible = [k for k in prefs["order"] if k in WIDGETS and principal.can(WIDGETS[k].capability)]
    pinned = [k for k in prefs["pinned"] if k in eligible]
    hidden = set(prefs["hidden"])
    # A widget is visible unless hidden — but pinning overrides hiding.
    visible = [k for k in eligible if k not in hidden or k in pinned]
    ordered = [k for k in pinned if k in visible] + [k for k in visible if k not in pinned]

    widgets = [_widget_view(k, principal, now=now, filters=prefs["filters"], pinned=(k in pinned))
               for k in ordered]
    # Eligible-but-hidden widgets (for the customize panel's "hidden" list).
    hidden_meta = [{"key": WIDGETS[k].key, "title": WIDGETS[k].title, "section": WIDGETS[k].section}
                   for k in eligible if k in hidden and k not in pinned]

    dashboard = get_daily_dashboard(principal)

    # Operational Intelligence panel (D.46) — a read-only composition over the authoritative recommendation
    # sources. Guarded so a failure/gate-off never breaks the advisor home.
    try:
        from app.services.recommendations import workspace_recommendations
        operational_intelligence = workspace_recommendations(principal)
    except Exception:
        operational_intelligence = {"enabled": False, "recommendations": [], "workload": {}}

    return {
        "greeting": _greeting(now),
        "display_name": getattr(principal, "display_name", None) or "there",
        "date": now.date(),
        "now": now,
        "today": digest.today_counts(principal, now=now),
        "priorities": digest.priorities(dashboard),
        "widgets": widgets,
        "hidden_widgets": hidden_meta,
        "presets": preferences.list_presets(principal.user_id),
        "active_preset_id": prefs.get("active_preset_id"),
        "can_personalize": principal.can("workspace.personalize"),
        "daily": dashboard,
        "operational_intelligence": operational_intelligence,
    }
