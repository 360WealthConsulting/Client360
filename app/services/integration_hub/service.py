"""Enterprise Integration Hub & Connected Platform Governance engine (Phase D.53).

A READ-ONLY composition over the platform's authoritative integration owners — the D.24 Integration Platform
(`integration.service` overview, `sync` synchronization, `connectors` provider/connector/credential,
`webhooks` delivery, `api` client, `events` catalog), the Event outbox + Event registry, and the M365 /
insurance / signature connectors. It composes named integration dashboards (integrations, synchronization,
authentication, webhooks, connectors, API health, event routing) from a declarative integration + connector +
panel registry. It owns NO persistence, introduces NO second integration platform, ESB, API gateway,
synchronization engine, webhook processor, message broker, or event bus, defines NO new metrics, and NEVER
mutates an external system, triggers synchronization, invokes an API, refreshes a token, reconnects a system,
or changes an integration setting. Every dashboard carries its generated timestamp, governing services,
source inventory, explainable panels, and deep links. Gate- and policy-aware; returns ``None`` when a
dashboard is not registered or the principal lacks its required capability (route → 404/403). No secrets,
tokens, credentials, or client payloads are ever emitted — counts + status only.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import IntegrationDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered integration dashboard. None when not registered or unauthorized; disabled
    envelope when gated off."""
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
    board = IntegrationDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The integration dashboards the principal may open (holds at least one required capability). Metadata
    only — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.INTEGRATION_DASHBOARDS:
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


def integration_summary(principal):
    """The firm integration-health summary — a compact, non-leaking envelope backing the Advisor Workspace
    Integration Health panel + the Executive Dashboard + AI grounding. Never raises. Counts + status only;
    never a secret/token/credential/payload."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("integration_overview", "sync_metrics", "connector_status", "webhook_metrics")
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
            "governing_services": ["integration", "integration.sync", "events"]}


def _entity_source_systems(principal, member_ids):
    """Rollup the external source systems a set of people connected from — composed read-only from the
    authoritative person lineage (governance.mdm.person_lineage, which reads person_source_links). This is
    the external-integration provenance for the entity; never an external-system call."""
    from app.services.governance.mdm import person_lineage
    systems = set()
    records = 0
    for mid in member_ids:
        try:
            lineage = person_lineage(principal, mid)
        except Exception:
            continue
        records += len(lineage)
        systems |= {r.get("source_system") for r in lineage if r.get("source_system")}
    return sorted(systems), records


def client_integrations(principal, person_id):
    """A compact external-integrations summary for ONE client — the external systems the client's data
    connected from, composed read-only from the authoritative person lineage. Counts + source-system names
    only, never a payload; deep-links to the authoritative integration surface. Never connects/syncs/invokes
    anything. Record scope is validated at the Client360 boundary."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "source_systems": []}
    try:
        systems, records = _entity_source_systems(principal, [person_id])
        return {"enabled": True, "source": "governance.mdm.person_lineage", "not_a_second_engine": True,
                "source_systems": systems, "connected_system_count": len(systems),
                "provenance_records": records, "deep_link": "/integration"}
    except Exception:
        stats.note("aggregation_failures", panel="client_integrations")
        return {"enabled": True, "source_systems": [], "error": "unavailable"}


def household_integrations(principal, household_id, member_ids=None):
    """Aggregated external-integrations summary for a household — the external systems the household's members
    connected from, composed read-only from the authoritative person lineage across members. Counts +
    source-system names only; a rollup, never an external-system call."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "source_systems": []}
    try:
        systems, records = _entity_source_systems(principal, member_ids or [])
        return {"enabled": True, "source": "governance.mdm.person_lineage", "not_a_second_engine": True,
                "source_systems": systems, "connected_system_count": len(systems),
                "provenance_records": records, "member_count": len(member_ids or []),
                "deep_link": "/integration"}
    except Exception:
        stats.note("aggregation_failures", panel="household_integrations")
        return {"enabled": True, "source_systems": [], "error": "unavailable"}
