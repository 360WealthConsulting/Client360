"""Enterprise Regulatory Examination Readiness, Evidence Governance & Supervisory Certification engine
(Phase D.59).

A READ-ONLY composition over the platform's authoritative regulatory / evidence / certification owners —
Compliance Intelligence + `compliance/reviews` + the rule catalog + the reviewer-authority owner, the
Exception Engine, Document Intelligence, Data Governance, Security Operations, Business Continuity, Vendor
Management, Financial Operations, Insurance licensing, audit logging, and the CI pipeline. It composes named
readiness dashboards from declarative obligation + evidence + examination-request + certification registries.
It owns NO persistence, introduces NO second compliance platform, examination-management system, audit
platform, document repository, records-management system, regulatory filing system, certification engine,
evidence vault, supervisory approval engine, or policy-management system, defines NO new metrics, and NEVER
creates an examination, opens a regulatory case, uploads or modifies evidence, approves a rule set, certifies
compliance, signs an attestation, files a form, submits evidence, closes a finding, resolves an exception,
changes retention, fabricates a filing acknowledgement, or infers regulatory acceptance. Regulatory filing,
examination-case ownership, certification reviewers, evidence export, and several obligation domains have no
authoritative owner in the platform today — those are declared registry entries reporting `not_configured`
honestly. The reviewer_authorities catalog is seeded empty, so every certification is `reviewer_not_confirmed`
/ blocked; reviewer authority is never inferred and business approval is never regulatory certification. Every
dashboard carries its generated timestamp, governing services, source inventory, explainable panels, deep
links, and its configured / not_configured / blocked domain lists. Gate- and policy-aware; returns ``None``
when a dashboard is not registered or the principal lacks its required capability (route → 404/403). No
sensitive evidence is ever emitted — counts, status, coverage, freshness, and age bands only. The derived
readiness summary describes OPERATIONAL READINESS, never regulatory certification, and never interprets an
absence of findings as compliance.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import ReadinessDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered readiness dashboard. None when not registered or unauthorized; disabled envelope
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
    blocked = tuple(p.key for p in panels if p.blocked)
    board = ReadinessDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy,
        configured_domains=registry.configured_obligations(),
        not_configured_domains=registry.not_configured_obligations(),
        blocked_domains=blocked)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The readiness dashboards the principal may open (holds at least one required capability). Metadata only
    — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.READINESS_DASHBOARDS:
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


def readiness_summary(principal):
    """The firm regulatory-readiness summary — a compact, non-leaking envelope backing the Advisor Workspace
    Regulatory Readiness panel + the Executive Dashboard + AI grounding. Never raises. Counts + status +
    coverage only; never sensitive evidence. **Operational readiness does NOT constitute regulatory
    certification, and an absent finding is never compliance.**"""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("derived_readiness_coverage", "evidence_availability", "documentation_gaps",
                  "unresolved_compliance_findings", "blocked_certifications", "licensing_evidence",
                  "stale_evidence")
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
            "operational_readiness_not_certification": True,
            "absence_of_findings_is_not_compliance": True,
            "not_configured_obligations": list(registry.not_configured_obligations()),
            "blocked_certifications": list(registry.blocked_certifications()),
            "governing_services": ["compliance_intelligence", "document_intelligence", "exception_engine",
                                   "insurance_licensing"]}


def _client_documents(principal, person_id):
    from app.services.document_intelligence import client_documents
    return client_documents(principal, person_id)


def _client_compliance(principal, person_id):
    from app.services.compliance_intelligence import compliance_summary
    return compliance_summary(principal, person_id=person_id)


def _guard(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def client_evidence_readiness(principal, person_id):
    """A compact evidence-&-supervisory-readiness summary in the context of ONE client — composed read-only
    from ONLY the authoritative owners that support per-client record scope (Document Intelligence for
    documentation completeness, Compliance Intelligence for client-specific compliance exceptions + suitability
    / replacement / workflow-approval evidence). It NEVER exposes firm-wide examination posture, firm-wide
    incidents, unrelated supervisory findings, other clients' evidence, or confidential regulator information.
    Counts + status only, never a payload; deep-links to the authoritative surfaces. Record scope is validated
    at the Client 360 boundary. Never mutates anything. Operational readiness is not regulatory
    certification."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "signals": {}}
    signals = {}
    dc = _guard(_client_documents, principal, person_id)
    if isinstance(dc, dict) and dc.get("enabled"):
        signals["documentation_gaps"] = dc.get("open_documentation_gaps")
        signals["document_count"] = dc.get("document_count")
    cs = _guard(_client_compliance, principal, person_id)
    if isinstance(cs, dict) and cs.get("enabled"):
        signals["compliance_exceptions"] = cs.get("open_exceptions")
        signals["open_reviews"] = cs.get("open_reviews")   # suitability / replacement / workflow approvals
    return {"enabled": True, "source": "regulatory_readiness.client_evidence_readiness",
            "not_a_second_engine": True, "operational_readiness_not_certification": True,
            "signals": signals, "deep_link": "/regulatory-readiness"}


def _household_documents(principal, household_id, ids):
    from app.services.document_intelligence import household_documents
    return household_documents(principal, household_id, ids)


def household_evidence_readiness(principal, household_id, member_ids=None):
    """A compact evidence-&-supervisory-readiness summary in the context of a household — aggregated read-only
    across members from ONLY the authoritative owners that support record scope. Deduplication of shared
    household documents / findings is handled by composing the household-scoped owner reads (never re-summing
    member items). Counts + status only; a rollup, never a payload. Preserves record scope; never exposes
    firm-wide examination information; never mutates. Operational readiness is not regulatory certification."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "signals": {}}
    ids = list(member_ids or [])
    signals = {}
    dh = _guard(_household_documents, principal, household_id, ids)
    if isinstance(dh, dict) and dh.get("enabled"):
        signals["documentation_gaps"] = dh.get("open_documentation_gaps")
        signals["document_count"] = dh.get("document_count")
    return {"enabled": True, "source": "regulatory_readiness.household_evidence_readiness",
            "not_a_second_engine": True, "operational_readiness_not_certification": True,
            "member_count": len(ids), "signals": signals, "deep_link": "/regulatory-readiness"}
