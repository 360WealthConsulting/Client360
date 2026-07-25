"""Enterprise Change Management, Release Governance & Configuration Intelligence engine (Phase D.63).

A READ-ONLY composition over the platform's authoritative change / release / configuration / evidence owners —
the architecture manifest (declared release line / migration head / route + capability counts), the live Alembic
script head (`observability.health._expected_head`), the live route / ADR / Client 360 section / Executive
dashboard counts, the Runtime + Policy engines, the Observability catalog / alerts / incidents / health owners,
Security incidents, Compliance Intelligence, and the CI pipeline evidence the manifest records. It composes
named change dashboards from declarative change-domain + release + configuration + change-evidence registries.
It owns NO persistence, introduces NO second ITSM / change-management / deployment / CI-CD / Git / CMDB /
feature-flag / release-approval / incident / maintenance-scheduling platform, defines NO new metrics, and NEVER
creates a branch, merges a pull request, pushes a commit, tags a release, deploys code, runs a migration,
changes a feature flag, approves a change, schedules maintenance, acknowledges an incident, executes rollback,
or certifies production. Live git / PR / CI status, deployment execution / status, rollback readiness,
production verification, change calendar, and post-change review have no authoritative owner in the platform
today — those are declared registry entries reporting `not_configured` HONESTLY, never a fabricated change
request, deployment status, release approval, rollback readiness, configuration state, production verification,
environment health, or change success. Every dashboard carries its generated timestamp, governing services,
source inventory, explainable panels, deep links, and its configured / not_configured domain lists. Gate- and
policy-aware; returns ``None`` when a dashboard is not registered or the principal lacks its required
capability (route → 404/403). No credentials, secrets, tokens, environment variables, connection strings,
private keys, deployment payloads, protected infrastructure details, sensitive configuration values, private
incident narratives, or repository credentials are ever emitted — counts, status, identifiers, hashes,
timestamps, coverage, and verification results only. The derived posture is an OPERATIONAL-READINESS summary,
never approval / certification / deployment success / production safety: **a green build is not production
certification, a merged pull request is not deployment, and an absent incident is not change success.**
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import ChangeDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered change dashboard. None when not registered or unauthorized; disabled envelope when
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
    board = ChangeDashboard(
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
    """The change dashboards the principal may open (holds at least one required capability). Metadata only —
    never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.CHANGE_DASHBOARDS:
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


def change_summary(principal):
    """The firm change / release / configuration summary — a compact, non-leaking envelope backing the Advisor
    Workspace Change & Release Status panel + the Executive Dashboard + AI grounding. Never raises. Counts +
    status + coverage + verification only; never a credential / token / deployment payload / sensitive
    configuration value. An OPERATIONAL-READINESS summary, never fabricated change / deployment / release
    approval / rollback readiness / production verification: green CI is not production, merged is not
    deployed, an absent incident is not success."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("executive_change_posture", "derived_change_readiness_coverage", "current_release_line",
                  "migration_head_status", "route_count_verification", "governance_status",
                  "unconfigured_change_domains")
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
            "operational_readiness_not_deployment_or_certification": True,
            "green_ci_is_not_production": True, "merged_is_not_deployed": True,
            "absent_incident_is_not_success": True,
            "not_configured_domains": list(registry.not_configured_domains()),
            "governing_services": ["architecture_manifest", "observability.health", "runtime", "policy",
                                   "continuous_integration"]}


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


def client_change_impact(principal, person_id):
    """A record-scoped change-impact summary for ONE client — ONLY the external systems / integrations whose
    configuration changes could touch THIS client's data, composed read-only from the authoritative person
    lineage (via Integration Hub `client_integrations`, over `governance.mdm.person_lineage`). **Firm-wide
    change / release / deployment / CI status is NOT record-scoped and is NEVER exposed here** — there is no
    authoritative record-scoped change-management owner, so beyond the affected-integration surface this
    section reports `not_configured` honestly. Counts + source-system names only, never a payload / deployment
    detail / configuration value; deep-links to the authoritative surface. Record scope is validated at the
    Client 360 boundary. Never creates / merges / deploys / changes / approves anything."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "signals": {}}
    ci = _guard(_client_integrations, principal, person_id)
    if not isinstance(ci, dict) or not ci.get("enabled"):
        return {"enabled": True, "source": "change_management.client_change_impact",
                "not_a_second_engine": True, "firm_wide_change_status_exposed": False,
                "record_scoped_owner": registry.NOT_CONFIGURED, "signals": {},
                "merged_is_not_deployed": True, "deep_link": "/change-management"}
    return {"enabled": True, "source": "change_management.client_change_impact", "not_a_second_engine": True,
            "firm_wide_change_status_exposed": False,
            "signals": {"affected_integrations": ci.get("connected_system_count"),
                        "source_systems": ci.get("source_systems")},
            "merged_is_not_deployed": True, "deep_link": "/change-management"}


def household_change_impact(principal, household_id, member_ids=None):
    """A record-scoped change-impact summary for a household — ONLY the external systems / integrations whose
    configuration changes could touch the household's members' data, composed read-only across members from the
    authoritative person lineage (via Integration Hub `household_integrations`). Firm-wide change / release /
    deployment status is NEVER exposed at household scope. Counts + source-system names only; a rollup, never a
    deployment detail. Preserves record scope; never creates / merges / deploys / approves."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "signals": {}}
    ids = list(member_ids or [])
    hh = _guard(_household_integrations, principal, household_id, ids)
    signals = {}
    if isinstance(hh, dict) and hh.get("enabled"):
        signals = {"affected_integrations": hh.get("connected_system_count"),
                   "source_systems": hh.get("source_systems")}
    return {"enabled": True, "source": "change_management.household_change_impact",
            "not_a_second_engine": True, "firm_wide_change_status_exposed": False,
            "member_count": len(ids), "signals": signals, "merged_is_not_deployed": True,
            "deep_link": "/change-management"}
