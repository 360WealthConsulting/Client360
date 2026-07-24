"""Enterprise Automation Orchestration & Business Process Composition engine (Phase D.51).

A READ-ONLY composition over the platform's authoritative operational services — the Workflow Engine
(`workflow_automation` + the `workflow_orchestration` facade), the Automation scheduled-job engine (ADR-027),
the Trigger engine + action catalog, the Event outbox, Scheduling, and Communications. It composes named
automation dashboards (inventory, workflow, triggers, execution, pending, failed) from a declarative
automation + trigger + action + panel registry. It owns NO persistence, introduces NO second workflow engine,
scheduler, rules engine, orchestration engine, event bus, or automation platform, defines NO new metrics, and
NEVER executes an automation, launches a workflow, fires a trigger, routes an event, or mutates anything.
Every dashboard carries its generated timestamp, governing services, source inventory, explainable panels,
and deep links. Gate- and policy-aware; returns ``None`` when a dashboard is not registered or the principal
lacks its required capability (route → 404/403). No workflow payloads or client-sensitive automation data are
ever emitted — counts + status only.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import OrchestrationDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered automation dashboard. None when not registered or unauthorized; disabled envelope
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
    board = OrchestrationDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The automation dashboards the principal may open (holds at least one required capability). Metadata
    only — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.ORCHESTRATION_DASHBOARDS:
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


def automation_summary(principal):
    """The firm automation-orchestration summary — a compact, non-leaking envelope backing the Advisor
    Workspace Automation Status panel + the Executive Dashboard + AI grounding. Never raises. Counts + status
    only; never workflow payloads."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("workflow_status", "workflow_pending_approvals", "failed_runs", "open_escalations")
    panels = []
    for pkey in panel_keys:
        p = compute_panel(principal, pkey)
        if p is not None:
            panels.append(p.to_dict())
    kpis = {p["key"]: p["value"] for p in panels if not p["restricted"] and p["value"] is not None}
    stats.note("summaries_composed")
    stats.note_ms((time.monotonic() - t0) * 1000)
    dashboards = list_dashboards(principal).get("dashboards", [])
    return {"enabled": True, "generated_at": datetime.now(UTC).isoformat(), "panels": panels,
            "kpis": kpis, "dashboards": dashboards,
            "governing_services": ["workflow_automation", "automation", "events"]}


def _entity_automation(principal, *, person_id=None, household_id=None):
    """Rollup the authoritative Workflow Orchestration facade read (list_instances) for one entity into
    counts + status — never a workflow payload. Record scope is enforced by the facade; this additionally
    filters the in-scope rows to the entity."""
    from app.services.workflow_orchestration.service import list_instances
    q = list_instances(principal, page=1, page_size=200)
    rows = q.get("rows", [])
    by_status = {}
    matched = 0
    for r in rows:
        if person_id is not None and r.get("person_id") != person_id:
            continue
        if household_id is not None and r.get("household_id") != household_id:
            continue
        matched += 1
        s = r.get("status") or "active"
        by_status[s] = by_status.get(s, 0) + 1
    return matched, by_status, q.get("total", 0)


def client_automation(principal, person_id):
    """A compact automation-history summary for ONE client — composed read-only from the Workflow
    Orchestration facade (record-scoped), rolled up to counts + status. Never executes/launches anything;
    deep-links to the authoritative workflow surface. Record scope is validated at the Client360 boundary."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "workflow_count": 0, "by_status": {}}
    try:
        matched, by_status, _total = _entity_automation(principal, person_id=person_id)
        return {"enabled": True, "source": "workflow_orchestration.service.list_instances",
                "not_a_second_engine": True, "workflow_count": matched, "by_status": by_status,
                "deep_link": f"/workflows?person_id={person_id}"}
    except Exception:
        stats.note("aggregation_failures", panel="client_automation")
        return {"enabled": True, "workflow_count": 0, "by_status": {}, "error": "unavailable"}


def household_automation(principal, household_id, member_ids=None):
    """Aggregated automation activity for a household — composed read-only from the Workflow Orchestration
    facade across the household + its members, rolled up to counts + status. Never re-executes or duplicates
    a workflow; a count rollup only."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "workflow_count": 0, "by_status": {}}
    try:
        from app.services.workflow_orchestration.service import list_instances
        q = list_instances(principal, page=1, page_size=200)
        members = set(member_ids or [])
        by_status, matched = {}, 0
        for r in q.get("rows", []):
            if r.get("household_id") == household_id or r.get("person_id") in members:
                matched += 1
                s = r.get("status") or "active"
                by_status[s] = by_status.get(s, 0) + 1
        return {"enabled": True, "source": "workflow_orchestration.service.list_instances",
                "not_a_second_engine": True, "workflow_count": matched, "by_status": by_status,
                "member_count": len(members), "deep_link": f"/workflows?household_id={household_id}"}
    except Exception:
        stats.note("aggregation_failures", panel="household_automation")
        return {"enabled": True, "workflow_count": 0, "by_status": {}, "error": "unavailable"}
