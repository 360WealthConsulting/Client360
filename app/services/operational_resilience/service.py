"""Enterprise Operational Resilience, Incident Management & Service Continuity Intelligence engine
(Phase D.60).

A READ-ONLY composition over the platform's authoritative operational-resilience owners — the Observability
service catalog / health / incidents / alerts (service health, reliability incidents, alerts, maintenance
windows), Security incidents, the Integration Platform (integration / sync failures), Vendor Management
(vendor operational status), Automation Orchestration (workflow escalations), and Business Continuity
(resilience posture, continuity coverage, RPO/RTO objectives). It composes named resilience dashboards from
declarative operational-service + incident-category + continuity-capability + recovery-objective +
operational-dependency registries. It owns NO persistence, introduces NO second incident-management platform,
ticketing system, monitoring platform, help desk, disaster-recovery platform, change-management platform,
CMDB, scheduler, or alerting engine, defines NO new metrics, and NEVER creates an incident, acknowledges an
alert, executes recovery, modifies monitoring, schedules maintenance, or closes an incident. Backup, restore,
disaster recovery, recovery testing, failover, outage-history/uptime, and vendor incidents have no
authoritative owner in the platform today — those are declared registry entries reporting `not_configured`
honestly. Every dashboard carries its generated timestamp, governing services, source inventory, explainable
panels, deep links, and its configured / not_configured domain lists. Gate- and policy-aware; returns ``None``
when a dashboard is not registered or the principal lacks its required capability (route → 404/403). No
sensitive operational payloads are ever emitted — counts, status, and coverage only. The derived executive
posture describes operational posture, never a certification that production is healthy or continuity assured,
and never infers recovery success.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import ResilienceDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered resilience dashboard. None when not registered or unauthorized; disabled envelope
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
    board = ResilienceDashboard(
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
    """The resilience dashboards the principal may open (holds at least one required capability). Metadata only
    — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.RESILIENCE_DASHBOARDS:
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


def resilience_summary(principal):
    """The firm operational-resilience summary — a compact, non-leaking envelope backing the Advisor Workspace
    Operational Status panel + the Executive Dashboard + AI grounding. Never raises. Counts + status only;
    never a sensitive operational payload. Operational posture is NOT a certification that production is healthy
    or continuity assured, and an absent incident is not health."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("executive_operational_status", "service_health", "degraded_services",
                  "reliability_incidents", "open_alerts", "active_maintenance_windows", "resilience_gaps")
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
            "operational_posture_not_certification": True,
            "absent_incident_is_not_health": True,
            "not_configured_domains": list(registry.not_configured_domains()),
            "governing_services": ["observability", "security", "business_continuity"]}


def _client_integrations(principal, person_id):
    from app.services.integration_hub import client_integrations
    return client_integrations(principal, person_id)


def _household_integrations(principal, household_id, ids):
    from app.services.integration_hub import household_integrations
    return household_integrations(principal, household_id, ids)


def _guard(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def _dep_count(result):
    if not isinstance(result, dict):
        return None
    systems = result.get("source_systems", [])
    return len(systems) if isinstance(systems, list) else None


def client_operational_impact(principal, person_id):
    """A compact operational-impact summary in the context of ONE client — composed read-only from ONLY the
    genuinely record-scoped owner (the Integration Hub per-entity read: the external services / vendors the
    client's data depends on). **Firm-wide operational information (incidents, alerts, service health) is NEVER
    exposed at client scope** — per-client incident impact has no authoritative owner (not_configured). Counts
    only, never a payload; deep-links to the authoritative surface. Record scope is validated at the Client 360
    boundary. Never mutates anything."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "signals": {}}
    dep = _dep_count(_guard(_client_integrations, principal, person_id))
    return {"enabled": True, "source": "operational_resilience.client_operational_impact",
            "not_a_second_engine": True, "firm_wide_operational_status_exposed": False,
            "per_client_incident_impact": registry.NOT_CONFIGURED,
            "signals": {"service_dependencies": dep}, "deep_link": "/operational-resilience"}


def household_operational_impact(principal, household_id, member_ids=None):
    """A compact operational-impact summary in the context of a household — aggregated read-only across members
    from ONLY the genuinely record-scoped owner (the Integration Hub per-entity read). Firm-wide operational
    information is NEVER exposed at household scope; per-household incident impact has no authoritative owner
    (not_configured). Counts only; a rollup, never a payload. Preserves record scope; never mutates."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "signals": {}}
    ids = list(member_ids or [])
    dep = _dep_count(_guard(_household_integrations, principal, household_id, ids))
    return {"enabled": True, "source": "operational_resilience.household_operational_impact",
            "not_a_second_engine": True, "firm_wide_operational_status_exposed": False,
            "per_household_incident_impact": registry.NOT_CONFIGURED, "member_count": len(ids),
            "signals": {"service_dependencies": dep}, "deep_link": "/operational-resilience"}
