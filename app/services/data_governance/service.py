"""Enterprise Data Governance, Master Data & Platform Stewardship engine (Phase D.52).

A READ-ONLY composition over the platform's authoritative data owners — the D.23 Governance package
(`governance.catalog` metadata, `governance.quality` validation, `governance.mdm` duplicate/lineage,
`governance.retention` cases, `governance.service` overview), the Person-merge / entity-resolution engine,
the Event registry (event lineage), and the domain entity owners. It composes named governance dashboards
(master data, stewardship, lineage, ownership, duplicate detection, validation, data quality) from a
declarative master-data + stewardship + panel registry. It owns NO persistence, introduces NO second
master-data platform, identity system, metadata repository, synchronization engine, entity-resolution
engine, or merge engine, defines NO new metrics, and NEVER merges an entity, alters an identity, modifies
metadata, approves stewardship, changes ownership, or mutates anything. Every dashboard carries its generated
timestamp, governing services, source inventory, explainable panels, and deep links. Gate- and policy-aware;
returns ``None`` when a dashboard is not registered or the principal lacks its required capability (route →
404/403). No client-sensitive data or entity payloads are ever emitted — counts + status only.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import GovernanceDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered governance dashboard. None when not registered or unauthorized; disabled envelope
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
    board = GovernanceDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The governance dashboards the principal may open (holds at least one required capability). Metadata
    only — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.GOVERNANCE_DASHBOARDS:
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


def governance_summary(principal):
    """The firm data-governance summary — a compact, non-leaking envelope backing the Advisor Workspace Data
    Governance panel + the Executive Dashboard + AI grounding. Never raises. Counts + status only; never a
    client-sensitive payload."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("validation_metrics", "duplicate_candidates", "governance_overview", "data_quality_score")
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
            "governing_services": ["governance", "governance.mdm", "governance.quality"]}


def client_governance(principal, person_id):
    """A compact data-governance summary for ONE client — composed read-only from the authoritative person
    lineage (governance.mdm.person_lineage, which reads person_source_links — never duplicated). Counts +
    status only, never a payload; deep-links to the authoritative governance surface. Never merges/alters an
    identity. Record scope is validated at the Client360 boundary."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "lineage_records": 0}
    try:
        from app.services.governance.mdm import person_lineage
        lineage = person_lineage(principal, person_id)
        sources = sorted({r.get("source_system") for r in lineage if r.get("source_system")})
        confirmed = sum(1 for r in lineage if r.get("confirmed"))
        return {"enabled": True, "source": "governance.mdm.person_lineage", "not_a_second_engine": True,
                "lineage_records": len(lineage), "confirmed_links": confirmed,
                "source_systems": sources, "deep_link": "/governance"}
    except Exception:
        stats.note("aggregation_failures", panel="client_governance")
        return {"enabled": True, "lineage_records": 0, "error": "unavailable"}


def household_governance(principal, household_id, member_ids=None):
    """Aggregated data-governance summary for a household — composed read-only from the authoritative person
    lineage across members, rolled up to counts. Never merges/duplicates; a count rollup only."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "lineage_records": 0}
    try:
        from app.services.governance.mdm import person_lineage
        total, confirmed, systems = 0, 0, set()
        for mid in (member_ids or []):
            try:
                lineage = person_lineage(principal, mid)
            except Exception:
                continue
            total += len(lineage)
            confirmed += sum(1 for r in lineage if r.get("confirmed"))
            systems |= {r.get("source_system") for r in lineage if r.get("source_system")}
        return {"enabled": True, "source": "governance.mdm.person_lineage", "not_a_second_engine": True,
                "lineage_records": total, "confirmed_links": confirmed,
                "source_systems": sorted(systems), "member_count": len(member_ids or []),
                "deep_link": "/governance"}
    except Exception:
        stats.note("aggregation_failures", panel="household_governance")
        return {"enabled": True, "lineage_records": 0, "error": "unavailable"}
