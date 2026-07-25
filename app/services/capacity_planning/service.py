"""Enterprise Capacity Planning, Workforce Operations & Resource Intelligence engine (Phase D.61).

A READ-ONLY composition over the platform's authoritative workforce / capacity / utilization owners — the
Operations capacity owner (firm utilization, resources, capacity plans), the Work Queue (workload, backlog,
queue health, assignments), Practice Management (staffing recommendations), and Automation Orchestration
(automation-worker workload). It composes named resource dashboards from declarative workforce + capacity +
utilization registries. It owns NO persistence, introduces NO second HR platform, HCM, scheduling application,
calendar system, project-management system, PSA, time-tracking platform, payroll platform, or
workforce-management system, defines NO new metrics, and NEVER assigns work, approves PTO, creates meetings,
moves calendar events, schedules resources, or modifies assignments. A full HR employee directory, contractors,
PTO / availability, time-tracking, payroll, calendar-scheduling capacity, and onboarding / planning capacity
have no authoritative owner in the platform today — those are declared registry entries reporting
`not_configured` honestly. Every dashboard carries its generated timestamp, governing services, source
inventory, explainable panels, deep links, and its configured / not_configured domain lists. Gate- and
policy-aware; returns ``None`` when a dashboard is not registered or the principal lacks its required
capability (route → 404/403). No employee details, payroll, HR records, calendar contents, time entries, or
sensitive staffing data are ever emitted — counts, status, and coverage only. The derived executive posture is
an operational summary, never a certified staffing / utilization figure and never an HR record.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import ResourceDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered resource dashboard. None when not registered or unauthorized; disabled envelope
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
    board = ResourceDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy,
        configured_domains=registry.configured_domains(),
        not_configured_domains=registry.not_configured_domains())
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The resource dashboards the principal may open (holds at least one required capability). Metadata only
    — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.RESOURCE_DASHBOARDS:
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


def capacity_summary(principal):
    """The firm capacity & workload summary — a compact, non-leaking envelope backing the Advisor Workspace
    Capacity & Workload panel + the Executive Dashboard + AI grounding. Never raises. Counts + status +
    coverage only; never an employee detail / payroll / HR record / calendar content / time entry. An
    operational summary, never a certified staffing / utilization figure."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("executive_workforce_status", "firm_capacity_utilization", "queue_health",
                  "advisor_workload_distribution", "staffing_readiness", "staffing_gaps",
                  "automation_workload")
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
            "operational_summary_not_hr_record": True,
            "not_configured_domains": list(registry.not_configured_domains()),
            "governing_services": ["operations.capacity", "work_queue", "practice_management"]}


def _assignments(entity_type, entity_id):
    from app.security.object_security import resolve_assignments
    return resolve_assignments(entity_type, entity_id)


def _guard(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def client_staffing(principal, person_id):
    """A compact staffing summary in the context of ONE client — ONLY the record-scoped staffing directly
    related to servicing this client (who is assigned to service the record), composed read-only from the
    authoritative authorization owner (`object_security.resolve_assignments`). **Employee workload, firm
    utilization, and unrelated staffing data are NEVER exposed at client scope.** Counts only, never an
    employee detail; deep-links to the authoritative surface. Record scope is validated at the Client 360
    boundary. Never assigns or modifies anything."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "signals": {}}
    rows = _guard(_assignments, "person", person_id)
    count = len(rows) if isinstance(rows, list) else None
    kinds = sorted({r.get("assignment_type") for r in rows
                    if isinstance(r, dict) and r.get("assignment_type")}) \
        if isinstance(rows, list) else []
    return {"enabled": True, "source": "capacity_planning.client_staffing", "not_a_second_engine": True,
            "firm_utilization_exposed": False, "employee_workload_exposed": False,
            "signals": {"assigned_servicers": count, "assignment_types": kinds},
            "deep_link": "/capacity-planning"}


def household_staffing(principal, household_id, member_ids=None):
    """A compact staffing summary in the context of a household — ONLY the record-scoped staffing directly
    related to servicing this household, composed read-only from the authoritative authorization owner across
    members. Employee workload, firm utilization, and unrelated staffing data are NEVER exposed at household
    scope. Counts only; a rollup, never an employee detail. Preserves record scope; never assigns anything."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "signals": {}}
    ids = list(member_ids or [])
    servicers = set()
    hh = _guard(_assignments, "household", household_id)
    for r in (hh if isinstance(hh, list) else []):
        if isinstance(r, dict) and r.get("user_id") is not None:
            servicers.add(("household", r.get("user_id")))
    for pid in ids:
        rows = _guard(_assignments, "person", pid)
        for r in (rows if isinstance(rows, list) else []):
            if isinstance(r, dict) and r.get("user_id") is not None:
                servicers.add(("person", r.get("user_id")))
    return {"enabled": True, "source": "capacity_planning.household_staffing", "not_a_second_engine": True,
            "firm_utilization_exposed": False, "employee_workload_exposed": False,
            "member_count": len(ids), "signals": {"assigned_servicers": len(servicers)},
            "deep_link": "/capacity-planning"}
