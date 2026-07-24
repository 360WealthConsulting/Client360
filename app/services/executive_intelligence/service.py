"""Enterprise Reporting & Executive Intelligence engine (Phase D.48).

A READ-ONLY composition over the platform's authoritative operational services + the SINGLE Analytics
Registry. It composes named executive dashboards from registered widgets — it defines NO new metrics, owns
NO persistence, and never mutates. Every dashboard carries its generated timestamp, source inventory,
governing services, explainable widgets, and deep links. Gate- and policy-aware; returns ``None`` when the
dashboard is not registered or the principal lacks its required capability (route → 404/403). Executive
(firm revenue/AUM) widgets inherit the ``analytics.executive`` gate from ``compute_metric`` — a non-executive
sees them ``restricted``, never their value.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import Dashboard
from .widgets import compute_widget


def _authorized(principal, dash) -> bool:
    """The principal must hold at least one of the dashboard's required capabilities. Executive widgets
    additionally self-restrict via compute_metric, so an analytics.view holder still sees a non-restricted
    subset of an executive dashboard."""
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered executive dashboard. None when not registered or unauthorized; disabled envelope
    when gated off."""
    if not gate.enabled() or not gate.gate("executive_dashboard.enabled"):
        return _disabled()
    dash = registry.dashboard(key)
    if dash is None:
        return None
    if not _authorized(principal, dash):
        stats.note("authorization_failures")
        return None
    if not gate.policy_ok("dashboard"):
        return {"enabled": True, "dashboard": None, "denied": "policy"}
    t0 = time.monotonic()
    widgets = []
    if gate.gate("executive_widgets.enabled"):
        for wkey in dash.widgets:
            w = compute_widget(principal, wkey)
            if w is not None:
                widgets.append(w)
    sources = tuple(dict.fromkeys(w.source for w in widgets))
    deep_links = {w.key: w.deep_link for w in widgets if w.deep_link}
    board = Dashboard(key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
                      generated_at=datetime.now(UTC).isoformat(), widgets=tuple(widgets),
                      governing_services=dash.governing_services, source_inventory=sources,
                      deep_links=deep_links, navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The dashboards the principal may open (holds at least one required capability). Metadata only."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.DASHBOARD_REGISTRY:
        if _authorized(principal, d):
            out.append({"key": d.key, "audience": d.audience, "navigation": d.navigation,
                        "widget_count": len(d.widgets), "required_capabilities": list(d.required_capabilities),
                        "governing_services": list(d.governing_services)})
    return {"enabled": True, "dashboards": out}


def executive_summary(principal):
    """The firm executive summary — the composed ``executive`` dashboard + firm-intelligence observations
    (reused from the Analytics firm-intelligence layer). Backs the Advisor Workspace panel + the Client 360 /
    Household 360 sections + AI grounding. Never raises; a non-executive gets a restricted view."""
    if not gate.enabled():
        return {"enabled": False, "widgets": [], "observations": [], "kpis": {}}
    board = compose_dashboard(principal, "executive")
    if board is None:
        # not authorized for the executive dashboard — return an empty, non-leaking envelope.
        return {"enabled": True, "authorized": False, "widgets": [], "observations": [], "kpis": {}}
    if not board.get("enabled"):
        return {"enabled": False, "widgets": [], "observations": [], "kpis": {}}
    widgets = board["dashboard"]["widgets"]
    kpis = {w["key"]: w["value"] for w in widgets if not w["restricted"] and w["value"] is not None}
    observations = []
    try:
        from app.services.analytics.intelligence import firm_intelligence
        observations = firm_intelligence(principal).get("observations", [])
    except Exception:
        stats.note("aggregation_failures", widget="firm_intelligence")
    return {"enabled": True, "authorized": True, "widgets": widgets, "kpis": kpis,
            "observations": observations, "generated_at": board["dashboard"]["generated_at"],
            "governing_services": board["dashboard"]["governing_services"]}


def get_widget(principal, key):
    """Compose a single widget by key. None when not registered / not explainable."""
    if not gate.enabled():
        return None
    w = compute_widget(principal, key)
    return w.to_dict() if w is not None else None
