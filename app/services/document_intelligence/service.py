"""Enterprise Document Intelligence & Records Lifecycle engine (Phase D.50).

A READ-ONLY composition over the platform's authoritative document systems — the Document Platform (Phase
D.16, the single document + metadata + folder + version + lifecycle + retention-policy owner), Governance
retention (Phase D.23, the records retention / legal-hold / disposition owner), and Compliance Intelligence
(Phase D.47, which normalizes the authoritative exception engine for documentation gaps). It composes named
document dashboards (inventory, retention, archive, lifecycle, missing documentation, completeness) from a
declarative document + retention + panel registry. It owns NO persistence, introduces NO second DMS / OCR
engine / index / archive / metadata store / records repository, defines NO new metrics, and NEVER mutates,
archives, deletes, re-classifies, or alters retention. Every dashboard carries its generated timestamp,
governing services, source inventory, explainable panels, and deep links. Gate- and policy-aware; returns
``None`` when a dashboard is not registered or the principal lacks its required capability (route → 404/403).
No document content or client-sensitive text is ever emitted — counts + status only.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import IntelligenceDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered document dashboard. None when not registered or unauthorized; disabled envelope
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
    board = IntelligenceDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The document dashboards the principal may open (holds at least one required capability). Metadata
    only — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.INTELLIGENCE_DASHBOARDS:
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


def document_summary(principal):
    """The firm document-intelligence summary — a compact, non-leaking envelope backing the Advisor
    Workspace Document Intelligence panel + the Executive Dashboard + AI grounding. Never raises. Counts +
    status only; never document content."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("inventory_by_status", "missing_documents", "expiring_documents", "completeness_score")
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
            "governing_services": ["document_platform", "governance.retention", "compliance_intelligence"]}


def _entity_documents(principal, entity_type, entity_id):
    """Rollup the authoritative Document Platform entity read (documents_for_entity) into counts + status —
    never the document content. Record scope is already validated at the Client360 boundary."""
    from app.services.document_platform.relationships import documents_for_entity
    docs = documents_for_entity(principal, entity_type, entity_id, limit=200)
    by_class, by_status = {}, {}
    for d in docs:
        c = d.get("classification") or "unclassified"
        s = d.get("status") or "active"
        by_class[c] = by_class.get(c, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1
    return docs, by_class, by_status


def client_documents(principal, person_id):
    """A compact document-intelligence summary for ONE client — composed read-only from the Document
    Platform entity read + Compliance Intelligence documentation gaps. Counts + status only; deep-links to
    the authoritative document surface. Record scope is already validated at the Client360 boundary."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "document_count": 0, "by_classification": {}}
    try:
        docs, by_class, by_status = _entity_documents(principal, "person", person_id)
        gaps = 0
        try:
            from app.services.compliance_intelligence import compliance_summary
            gaps = compliance_summary(principal, person_id=person_id).get("open_exceptions", 0)
        except Exception:
            pass
        return {"enabled": True, "source": "document_platform.documents_for_entity",
                "not_a_second_engine": True, "document_count": len(docs), "by_classification": by_class,
                "by_status": by_status, "open_documentation_gaps": gaps,
                "deep_link": f"/document-library?person_id={person_id}"}
    except Exception:
        stats.note("aggregation_failures", panel="client_documents")
        return {"enabled": True, "document_count": 0, "by_classification": {}, "error": "unavailable"}


def household_documents(principal, household_id, member_ids=None):
    """Aggregated document-intelligence summary for a household — composed read-only from the Document
    Platform entity read across the household + its members (deduped by document id), rolled up to counts +
    status. Never re-stores or copies a document; a count rollup only."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "document_count": 0, "by_classification": {}}
    try:
        seen, by_class, by_status = set(), {}, {}
        from app.services.document_platform.relationships import documents_for_entity
        pairs = [("household", household_id)] + [("person", m) for m in (member_ids or [])]
        for etype, eid in pairs:
            for d in documents_for_entity(principal, etype, eid, limit=200):
                if d["id"] in seen:
                    continue
                seen.add(d["id"])
                c = d.get("classification") or "unclassified"
                s = d.get("status") or "active"
                by_class[c] = by_class.get(c, 0) + 1
                by_status[s] = by_status.get(s, 0) + 1
        return {"enabled": True, "source": "document_platform.documents_for_entity",
                "not_a_second_engine": True, "deduped_by": "document_id", "document_count": len(seen),
                "by_classification": by_class, "by_status": by_status, "member_count": len(member_ids or []),
                "deep_link": f"/document-library?household_id={household_id}"}
    except Exception:
        stats.note("aggregation_failures", panel="household_documents")
        return {"enabled": True, "document_count": 0, "by_classification": {}, "error": "unavailable"}
