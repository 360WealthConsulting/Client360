"""Enterprise Practice Management engine (Phase D.49).

A READ-ONLY composition over the platform's authoritative operational services — Operations Capacity (the
authoritative capacity/utilization owner, Phase D.20), the Unified Work Queue, Workflow Automation,
Operational + Compliance Intelligence, Executive Reporting, the opportunity + Analytics firm-intelligence
layers, and the tax domain. It composes named practice dashboards (advisor/department utilization, staffing,
workload, backlog, workflow aging, seasonal forecast, service-level) from a declarative capacity + resource
+ panel registry. It owns NO persistence, introduces NO second workflow / scheduler / staffing / queue /
planning engine, defines NO new metrics, and NEVER mutates, assigns, rebalances, or modifies schedules.
Every dashboard carries its generated timestamp, governing services, source inventory, explainable panels,
and deep links. Gate- and policy-aware; returns ``None`` when a dashboard is not registered or the principal
lacks its required capability (route → 404/403).
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import PracticeDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered practice dashboard. None when not registered or unauthorized; disabled envelope
    when gated off."""
    if not gate.enabled():
        return _disabled()
    dash = registry.dashboard(key)
    if dash is None:
        return None
    if not _authorized(principal, dash):
        stats.note("authorization_failures")
        return None
    if not gate.gate(dash.runtime_gate):
        return {"enabled": False, "dashboard": None, "gated": dash.runtime_gate}
    if not gate.policy_ok("dashboard"):
        return {"enabled": True, "dashboard": None, "denied": "policy"}
    t0 = time.monotonic()
    panels = []
    for pkey in dash.panels:
        p = compute_panel(principal, pkey)
        if p is not None:
            panels.append(p)
    sources = tuple(dict.fromkeys(p.source for p in panels))
    deep_links = {p.key: p.deep_link for p in panels if p.deep_link}
    board = PracticeDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The practice dashboards the principal may open (holds at least one required capability). Metadata
    only — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.PRACTICE_DASHBOARDS:
        if _authorized(principal, d):
            out.append({"key": d.key, "audience": d.audience, "navigation": d.navigation,
                        "panel_count": len(d.panels), "runtime_gate": d.runtime_gate,
                        "required_capabilities": list(d.required_capabilities),
                        "governing_services": list(d.governing_services)})
    return {"enabled": True, "dashboards": out}


def get_panel(principal, key):
    """Compose a single panel by key. None when not registered / not explainable."""
    if not gate.enabled():
        return None
    p = compute_panel(principal, key)
    return p.to_dict() if p is not None else None


def practice_summary(principal):
    """The firm practice-management summary — a compact, non-leaking envelope backing the Advisor Workspace
    Capacity Planning panel + the Executive Dashboard + AI grounding. Never raises. A principal lacking
    ``capacity.read`` still gets the work-scoped workload counts (their own book), never firm capacity."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("firm_capacity_utilization", "advisor_workload_distribution", "open_backlog",
                  "staffing_recommendations")
    panels = []
    for pkey in panel_keys:
        p = compute_panel(principal, pkey)
        if p is not None:
            panels.append(p.to_dict())
    kpis = {}
    for p in panels:
        if not p["restricted"] and p["value"] is not None:
            kpis[p["key"]] = p["value"]
    stats.note("summaries_composed")
    stats.note_ms((time.monotonic() - t0) * 1000)
    dashboards = list_dashboards(principal).get("dashboards", [])
    return {"enabled": True, "generated_at": datetime.now(UTC).isoformat(), "panels": panels,
            "kpis": kpis, "dashboards": dashboards,
            "governing_services": ["operations.capacity", "work_queue", "workflow_automation"]}


def client_workload(principal, person_id):
    """A compact operational-workload summary for ONE client — composed from the Unified Work Queue filtered
    to the person (book-scoped, deterministic). Read-only; deep-links to the authoritative work surface.
    Record scope is already validated at the Client360 boundary before this runs."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "open": 0, "overdue": 0, "by_domain": {}}
    try:
        from app.services.work_queue.service import compose_queue
        q = compose_queue(principal, filters={"person_id": person_id}, page=1, page_size=1)
        counts = q.get("counts", {})
        return {"enabled": True, "source": "work_queue.compose_queue", "not_a_second_engine": True,
                "open": q.get("total", 0), "overdue": counts.get("overdue", 0),
                "breached": counts.get("breached", 0), "unassigned": counts.get("unassigned", 0),
                "by_domain": counts.get("by_domain", {}), "deep_link": f"/work?person_id={person_id}"}
    except Exception:
        stats.note("aggregation_failures", panel="client_workload")
        return {"enabled": True, "open": 0, "overdue": 0, "by_domain": {}, "error": "unavailable"}


def household_workload(principal, household_id, member_ids=None):
    """Aggregated operational-workload summary for a household — composed from the Unified Work Queue filtered
    to the household (book-scoped, deterministic). Read-only. Member workload is NOT re-summed from
    incompatible units; this is a count rollup of open work items keyed to the household."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "open": 0, "overdue": 0, "by_domain": {}}
    try:
        from app.services.work_queue.service import compose_queue
        q = compose_queue(principal, filters={"household_id": household_id}, page=1, page_size=1)
        counts = q.get("counts", {})
        return {"enabled": True, "source": "work_queue.compose_queue", "not_a_second_engine": True,
                "open": q.get("total", 0), "overdue": counts.get("overdue", 0),
                "breached": counts.get("breached", 0), "unassigned": counts.get("unassigned", 0),
                "by_domain": counts.get("by_domain", {}), "member_count": len(member_ids or []),
                "deep_link": f"/work?household_id={household_id}"}
    except Exception:
        stats.note("aggregation_failures", panel="household_workload")
        return {"enabled": True, "open": 0, "overdue": 0, "by_domain": {}, "error": "unavailable"}
