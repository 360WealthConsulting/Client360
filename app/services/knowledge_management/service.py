"""Enterprise Knowledge Management, SOP Governance & Institutional Intelligence engine (Phase D.62).

A READ-ONLY composition over the platform's authoritative knowledge / SOP / documentation owners — the Document
Platform (documents, classification, lifecycle, immutable versions, ownership), Document Intelligence
(documentation completeness / gaps / freshness), and Data Governance retention (legal holds, disposition). It
composes named knowledge dashboards from declarative knowledge-domain + SOP-category + documentation-owner +
knowledge-source + publication-status registries. It owns NO persistence, introduces NO second wiki,
document-management platform, Confluence replacement, SharePoint, records-management platform, search engine,
AI knowledge store, or document repository, defines NO new metrics, and NEVER creates a document, edits a
document, approves a document, publishes documentation, changes a version, or alters metadata. SOP governance,
runbooks, playbooks, onboarding SOPs, a knowledge base, institutional memory, a wiki, Confluence, and a
dedicated full-text / vector search index have no authoritative owner in the platform today — those are
declared registry entries reporting `not_configured` honestly. Every dashboard carries its generated
timestamp, governing services, source inventory, explainable panels, deep links, and its configured /
not_configured domain lists. Gate- and policy-aware; returns ``None`` when a dashboard is not registered or the
principal lacks its required capability (route → 404/403). No document contents, confidential procedures,
credentials, tokens, or client-sensitive documentation are ever emitted — counts, status, and coverage only.
The derived executive posture is a documentation-coverage summary, never fabricated documentation, SOP
approval, version history, or institutional knowledge.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import KnowledgeDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered knowledge dashboard. None when not registered or unauthorized; disabled envelope
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
    board = KnowledgeDashboard(
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
    """The knowledge dashboards the principal may open (holds at least one required capability). Metadata only
    — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.KNOWLEDGE_DASHBOARDS:
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


def knowledge_summary(principal):
    """The firm knowledge & documentation summary — a compact, non-leaking envelope backing the Advisor
    Workspace Knowledge & SOPs panel + the Executive Dashboard + AI grounding. Never raises. Counts + status +
    coverage only; never a document content / confidential procedure / credential / token. A
    documentation-coverage summary, never fabricated documentation or institutional knowledge."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("executive_knowledge_status", "documentation_completeness", "documentation_gaps",
                  "sop_coverage", "publication_readiness", "knowledge_gaps", "knowledge_health")
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
            "documentation_coverage_not_fabricated_knowledge": True,
            "not_configured_domains": list(registry.not_configured_domains()),
            "governing_services": ["document_platform", "document_intelligence", "governance.retention"]}


def _client_documents(principal, person_id):
    from app.services.document_intelligence import client_documents
    return client_documents(principal, person_id)


def _household_documents(principal, household_id, ids):
    from app.services.document_intelligence import household_documents
    return household_documents(principal, household_id, ids)


def _guard(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def client_documentation(principal, person_id):
    """A compact documentation summary in the context of ONE client — ONLY the record-scoped documentation
    relevant to servicing this client (this client's documents + documentation gaps), composed read-only from
    the authoritative Document Intelligence per-entity read (over the Document Platform's scoped
    `documents_for_entity`). **Internal SOPs, unrelated documentation, confidential operational procedures, and
    firm-wide documentation metrics are NEVER exposed at client scope.** Counts + status only, never document
    contents; deep-links to the authoritative surface. Record scope is validated at the Client 360 boundary.
    Never creates / edits / publishes anything."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "signals": {}}
    dc = _guard(_client_documents, principal, person_id)
    if not isinstance(dc, dict) or not dc.get("enabled"):
        return {"enabled": True, "source": "knowledge_management.client_documentation",
                "not_a_second_engine": True, "firm_wide_metrics_exposed": False,
                "internal_sops_exposed": False, "signals": {}, "deep_link": "/knowledge-management"}
    return {"enabled": True, "source": "knowledge_management.client_documentation", "not_a_second_engine": True,
            "firm_wide_metrics_exposed": False, "internal_sops_exposed": False,
            "signals": {"document_count": dc.get("document_count"),
                        "documentation_gaps": dc.get("open_documentation_gaps")},
            "deep_link": "/knowledge-management"}


def household_documentation(principal, household_id, member_ids=None):
    """A compact documentation summary in the context of a household — ONLY the record-scoped documentation
    relevant to servicing this household, composed read-only across members from the authoritative Document
    Intelligence per-entity read (deduplicated by document id by the owner). Internal SOPs, unrelated
    documentation, and firm-wide documentation metrics are NEVER exposed at household scope. Counts + status
    only; a rollup, never document contents. Preserves record scope; never creates / edits / publishes."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "signals": {}}
    ids = list(member_ids or [])
    dh = _guard(_household_documents, principal, household_id, ids)
    signals = {}
    if isinstance(dh, dict) and dh.get("enabled"):
        signals = {"document_count": dh.get("document_count"),
                   "documentation_gaps": dh.get("open_documentation_gaps")}
    return {"enabled": True, "source": "knowledge_management.household_documentation",
            "not_a_second_engine": True, "firm_wide_metrics_exposed": False, "internal_sops_exposed": False,
            "member_count": len(ids), "signals": signals, "deep_link": "/knowledge-management"}
