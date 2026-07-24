"""Enterprise Business Continuity, Disaster Recovery & Operational Resilience engine (Phase D.55).

A READ-ONLY composition over the platform's authoritative operational-resilience owners — the Observability
domain (`service` overview, `catalog`, `health`, `incidents`, `alerts`), the Runtime engine (`runtime.service`
readiness, `coordination` cluster, `consumption` adoption), the Automation scheduler, and Communications. It
composes named continuity dashboards (backup status, recovery readiness, restore validation, infrastructure
health, runtime resilience, maintenance, notifications, operational readiness) from a declarative resilience
+ recovery + panel registry. It owns NO persistence, introduces NO second backup platform, monitoring system,
disaster-recovery engine, scheduler, notification system, or incident manager, defines NO new metrics, and
NEVER starts a backup, restores data, acknowledges an incident, changes monitoring, alters runtime, or
modifies infrastructure. Backup / restore / DR have no authoritative owner in the platform today — those
panels report ``not_configured`` honestly. Every dashboard carries its generated timestamp, governing
services, source inventory, explainable panels, and deep links. Gate- and policy-aware; returns ``None`` when
a dashboard is not registered or the principal lacks its required capability (route → 404/403). No
infrastructure payloads or client-sensitive data are ever emitted — counts + status only.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import ContinuityDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered continuity dashboard. None when not registered or unauthorized; disabled envelope
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
    board = ContinuityDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The continuity dashboards the principal may open (holds at least one required capability). Metadata
    only — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.CONTINUITY_DASHBOARDS:
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


def continuity_summary(principal):
    """The firm operational-resilience summary — a compact, non-leaking envelope backing the Advisor
    Workspace Operational Resilience panel + the Executive Dashboard + AI grounding. Never raises. Counts +
    status only; never an infrastructure payload."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("resilience_score", "infrastructure_availability", "service_incidents", "backup_coverage")
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
            "governing_services": ["observability", "runtime", "business_continuity"]}


def _firm_continuity(principal):
    """The firm-level continuity posture (resilience score + operational overview + backup coverage),
    composed read-only. Business continuity is a firm-level posture; the Client 360 / Household 360 sections
    surface it in context (a client's data is protected by the firm's continuity posture). Never mutates."""
    from .panels import compute_panel as _cp
    out = {"enabled": True, "not_a_second_engine": True, "deep_link": "/business-continuity"}
    for key in ("resilience_score", "infrastructure_availability", "backup_coverage"):
        p = _cp(principal, key)
        if p is not None and not p.restricted and p.available:
            out[key] = p.value
    return out


def client_continuity(principal, person_id):
    """A compact business-continuity summary in the context of ONE client — the firm-level operational
    resilience posture protecting the client's data, composed read-only. Counts + status only, never a
    payload; deep-links to the authoritative continuity surface. Record scope is validated at the Client360
    boundary."""
    if not gate.enabled():
        return {"enabled": False, "resilience_score": None}
    try:
        return {**_firm_continuity(principal), "source": "business_continuity.firm_posture"}
    except Exception:
        stats.note("aggregation_failures", panel="client_continuity")
        return {"enabled": True, "resilience_score": None, "error": "unavailable"}


def household_continuity(principal, household_id, member_ids=None):
    """A compact business-continuity summary in the context of a household — the firm-level operational
    resilience posture protecting the household's data, composed read-only. Counts + status only; never a
    payload. (Business continuity is firm-level; the same posture protects every household.)"""
    if not gate.enabled():
        return {"enabled": False, "resilience_score": None}
    try:
        return {**_firm_continuity(principal), "source": "business_continuity.firm_posture",
                "member_count": len(member_ids or [])}
    except Exception:
        stats.note("aggregation_failures", panel="household_continuity")
        return {"enabled": True, "resilience_score": None, "error": "unavailable"}
