"""Enterprise Data Governance, Lineage & Information Stewardship Intelligence engine (Phase D.66).

A READ-ONLY composition over the platform's authoritative data-governance owners — the Governance catalog (data
domains, elements, quality rules, survivorship rules, stewardship), Governance MDM (lineage & provenance, merge
candidates), Governance Quality (findings), and Governance Retention (assignments, legal holds, deletion
requests, cases). It composes named data-governance dashboards from declarative data-domain + lineage +
stewardship + quality + retention registries. It owns NO persistence, introduces NO second data catalog /
metadata repository / ETL platform / MDM platform / warehouse / governance platform / lineage engine / quality
engine, defines NO new metrics, and NEVER transforms data, synchronizes systems, mutates metadata, repairs
data, creates lineage, assigns a steward, executes a quality rule, or enforces retention. External data
catalog, business glossary, data classification, automated column-level lineage, data contracts, DQ scorecards
/ SLAs, retention-policy catalog, and DPIA have no authoritative owner in the platform today — those are
declared registry entries reporting `not_configured` HONESTLY, never fabricated lineage, source systems,
stewardship assignments, quality scores, retention policies, metadata, catalog entries, or data owners. Every
dashboard carries its generated timestamp, governing services, source inventory, explainable panels, deep
links, and its configured / not_configured domain lists. Gate- and policy-aware; returns ``None`` when a
dashboard is not registered or the principal lacks its required capability (route → 404/403). No sensitive data
values, client PII, credentials, secrets, tokens, confidential metadata, internal governance notes, or
quality-rule internals are ever emitted — counts, coverage, status, and ratios only. The derived posture is a
GOVERNANCE-READINESS summary, never a repaired dataset, a created lineage edge, an assigned steward, an
executed quality rule, or an enforced retention decision: **a registered rule is not an executed check, a
steward assignment is not a governance guarantee, a lineage record is not a complete lineage, and coverage is
not certification.**

NOTE: distinct from the D.52 Data Governance layer (`/data-governance`, `governance.view`); both are read-only
views over the single authoritative D.23 Governance package, neither owns or duplicates governance data. This
layer's master gate is `data_governance_intelligence.enabled` (NOT D.52's `data_governance.enabled`).
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import DataGovernanceDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered data-governance dashboard. None when not registered or unauthorized; disabled
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
    board = DataGovernanceDashboard(
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
    """The data-governance dashboards the principal may open (holds at least one required capability). Metadata
    only — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.DATA_GOVERNANCE_DASHBOARDS:
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


def data_governance_summary(principal):
    """The firm data-governance summary — a compact, non-leaking envelope backing the Advisor Workspace Data
    Governance Status panel + the Executive Dashboard + AI grounding. Never raises. Counts + coverage + status
    only; never a sensitive data value / client PII / confidential metadata / quality-rule internal. A
    GOVERNANCE-READINESS summary, never fabricated lineage / metadata / stewardship / quality score / retention
    policy: a registered rule is not an executed check, coverage is not certification."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("executive_data_governance_posture", "governance_readiness", "data_domain_coverage",
                  "lineage_coverage", "quality_finding_summary", "governance_health_status",
                  "unconfigured_data_domains")
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
            "governance_coverage_not_certification": True,
            "registered_rule_is_not_an_executed_check": True,
            "lineage_record_is_not_complete_lineage": True,
            "not_configured_domains": list(registry.not_configured_domains()),
            "governing_services": ["governance.catalog", "governance.mdm", "governance.quality",
                                   "governance.retention"]}


def _person_lineage(principal, person_id):
    from app.services.governance.mdm import person_lineage
    return person_lineage(principal, person_id)


def _record_governance_metadata(principal, person_ids):
    """Record-scoped governance metadata — composed read-only from the authoritative Governance MDM owner
    (`person_lineage`), the ONLY record-scoped governance owner. Exposes the count of source-system provenance
    records + the DISTINCT source-system names touching the record(s) — **never an internal governance note,
    confidential metadata, a quality-rule internal, system architecture, platform configuration, or an INFERRED
    governance state.** Read-only; never mutates metadata / creates lineage / assigns a steward / repairs data."""
    records = 0
    systems = set()
    for pid in person_ids:
        if pid is None:
            continue
        try:
            rows = _person_lineage(principal, pid) or []
        except Exception:
            stats.note("aggregation_failures", panel="record_governance_metadata")
            continue
        records += len(rows)
        for r in rows:
            src = r.get("source_system") if isinstance(r, dict) else None
            if src:
                systems.add(src)
    return {"enabled": True, "available": True, "config_status": registry.CONFIGURED,
            "source": "governance.mdm.person_lineage", "not_a_second_engine": True,
            "internal_governance_notes_exposed": False, "confidential_metadata_exposed": False,
            "governance_state_inferred": False,
            "signals": {"lineage_records": records, "source_systems": len(systems)},
            "deep_link": "/data-governance-intelligence"}


def client_data_governance(principal, person_id):
    """A record-scoped data-governance-metadata summary for ONE client — ONLY the source-system lineage /
    provenance for this client's record, composed read-only from the authoritative Governance MDM owner
    (`person_lineage`). **No internal governance notes, confidential metadata, quality-rule internals, system
    architecture, or platform configuration are ever exposed, and governance state is never inferred.**
    Read-only; never mutates metadata / creates lineage / assigns a steward / repairs data. Record scope is
    validated at the Client 360 boundary."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "available": False, "signals": {}}
    return _record_governance_metadata(principal, [person_id])


def household_data_governance(principal, household_id, member_ids=None):
    """A record-scoped data-governance-metadata summary for a household — ONLY the source-system lineage /
    provenance across the household's members, composed read-only from the authoritative Governance MDM owner.
    No internal governance notes, confidential metadata, quality-rule internals, system architecture, or
    platform configuration are ever exposed, and governance state is never inferred. Read-only; never mutates /
    creates lineage / assigns a steward / repairs data."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "available": False, "signals": {}}
    return _record_governance_metadata(principal, list(member_ids or []))
