"""Enterprise Risk Management, Internal Controls & Assurance Governance engine (Phase D.58).

A READ-ONLY composition over the platform's authoritative risk / control / assurance owners — Compliance
Intelligence + the Exception Engine (findings, exceptions, remediation), Security Operations + incidents
(cybersecurity, identity/access), Data Governance (data-quality), the Integration Platform (integration /
sync), Business Continuity (resilience), Vendor Management (third-party), Financial Operations + the commission
ledger (financial control), Document Intelligence (documentation), Automation Orchestration (workflow),
Insurance licensing (licensing), and the Runtime + Policy engines + audit logging (governance/assurance). It
composes named risk dashboards from a declarative risk + control + assurance + panel registry. It owns NO
persistence, introduces NO second GRC platform, risk register, compliance engine, exception system, audit
platform, incident-management system, control-testing application, policy engine, or approval engine, defines
NO new metrics, and NEVER creates a risk, changes a rating, closes a finding, approves a control, accepts an
exception, acknowledges an incident, assigns remediation, alters evidence, certifies compliance, or modifies
policy. Control testing / effectiveness, model/AI risk, and privacy risk have no authoritative owner in the
platform today — those are declared registry entries reporting `not_configured` honestly. Every dashboard
carries its generated timestamp, governing services, source inventory, explainable panels, deep links, and its
configured / not_configured domain lists. Gate- and policy-aware; returns ``None`` when a dashboard is not
registered or the principal lacks its required capability (route → 404/403). No sensitive evidence is ever
emitted — counts, status, severity distributions, and coverage summaries only; the enterprise-risk-posture
panel is a DERIVED coverage summary, never a certified composite risk score.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import RiskDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered risk dashboard. None when not registered or unauthorized; disabled envelope when
    gated off."""
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
    board = RiskDashboard(
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
    """The risk dashboards the principal may open (holds at least one required capability). Metadata only —
    never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.RISK_DASHBOARDS:
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


def risk_summary(principal):
    """The firm enterprise-risk & controls summary — a compact, non-leaking envelope backing the Advisor
    Workspace Enterprise Risk & Controls panel + the Executive Dashboard + AI grounding. Never raises. Counts +
    status only; never sensitive evidence. Absence of a visible finding does NOT imply the firm is compliant —
    it reflects only what the authorized composed owners currently report."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("enterprise_risk_posture", "open_compliance_findings", "security_incidents",
                  "workflow_escalations", "vendor_risk_findings", "continuity_gaps",
                  "financial_reconciliation_status", "control_coverage")
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
            "not_compliance_certification": True,
            "configured_domains": list(registry.configured_domains()),
            "not_configured_domains": list(registry.not_configured_domains()),
            "governing_services": ["compliance_intelligence", "exception_engine", "security", "vendor_management"]}


def _client_compliance(principal, person_id):
    from app.services.compliance_intelligence import compliance_summary
    return compliance_summary(principal, person_id=person_id)


def _client_documents(principal, person_id):
    from app.services.document_intelligence import client_documents
    return client_documents(principal, person_id)


def _client_governance(principal, person_id):
    from app.services.data_governance import client_governance
    return client_governance(principal, person_id)


def _client_integrations(principal, person_id):
    from app.services.integration_hub import client_integrations
    return client_integrations(principal, person_id)


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


def client_risk_controls(principal, person_id):
    """A compact risk-&-controls summary in the context of ONE client — composed read-only from ONLY the
    authoritative owners that support per-client record scope (Compliance Intelligence, Document Intelligence,
    Data Governance, the Integration Hub). Firm-wide incidents / findings are NEVER exposed to a client-scoped
    view. Counts + status only, never a payload; deep-links to the authoritative surfaces. Record scope is
    validated at the Client 360 boundary. Never mutates anything. Absence of a signal does NOT certify the
    client relationship is compliant."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "signals": {}}
    signals = {}
    cs = _guard(_client_compliance, principal, person_id)
    if isinstance(cs, dict) and cs.get("enabled"):
        signals["compliance_exceptions"] = cs.get("open_exceptions")
        signals["open_reviews"] = cs.get("open_reviews")
    dc = _guard(_client_documents, principal, person_id)
    if isinstance(dc, dict) and dc.get("enabled"):
        signals["documentation_gaps"] = dc.get("missing_documents") or dc.get("gaps")
    dg = _guard(_client_governance, principal, person_id)
    if isinstance(dg, dict) and dg.get("enabled"):
        signals["data_quality_issues"] = dg.get("issues") or dg.get("duplicate_candidates")
    signals["integration_dependencies"] = _dep_count(_guard(_client_integrations, principal, person_id))
    return {"enabled": True, "source": "enterprise_risk.client_risk_controls", "not_a_second_engine": True,
            "not_compliance_certification": True, "signals": signals, "deep_link": "/enterprise-risk"}


def _household_documents(principal, household_id, ids):
    from app.services.document_intelligence import household_documents
    return household_documents(principal, household_id, ids)


def _household_governance(principal, household_id, ids):
    from app.services.data_governance import household_governance
    return household_governance(principal, household_id, ids)


def _household_integrations(principal, household_id, ids):
    from app.services.integration_hub import household_integrations
    return household_integrations(principal, household_id, ids)


def household_risk_controls(principal, household_id, member_ids=None):
    """A compact risk-&-controls summary in the context of a household — aggregated read-only across members
    from ONLY the authoritative owners that support record scope. Deduplication of shared household findings
    is handled by composing the household-scoped owner reads (never re-summing member items). Counts + status
    only; a rollup, never a payload. Preserves record scope; never mutates. Absence of a signal does NOT
    certify the household is compliant."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "signals": {}}
    ids = list(member_ids or [])
    signals = {}
    dh = _guard(_household_documents, principal, household_id, ids)
    if isinstance(dh, dict) and dh.get("enabled"):
        signals["documentation_gaps"] = dh.get("missing_documents") or dh.get("gaps")
    dgh = _guard(_household_governance, principal, household_id, ids)
    if isinstance(dgh, dict) and dgh.get("enabled"):
        signals["data_quality_issues"] = dgh.get("issues") or dgh.get("duplicate_candidates")
    signals["integration_dependencies"] = _dep_count(
        _guard(_household_integrations, principal, household_id, ids))
    return {"enabled": True, "source": "enterprise_risk.household_risk_controls", "not_a_second_engine": True,
            "not_compliance_certification": True, "member_count": len(ids), "signals": signals,
            "deep_link": "/enterprise-risk"}
