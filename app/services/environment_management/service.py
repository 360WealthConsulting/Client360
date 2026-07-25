"""Enterprise Environment Management, Deployment Topology & Platform Lifecycle Intelligence engine (Phase D.64).

A READ-ONLY composition over the platform's authoritative environment / platform / deployment-topology /
lifecycle / infrastructure-dependency owners — the Observability catalog (environment profiles, deployment
references, service inventory, the service dependency graph), the Observability health owner (runtime
snapshots, the live migration head), the Observability service overview, the Runtime + Policy engines
(configuration coverage), and the Integration platform (integration dependencies). It composes named
environment dashboards from declarative environment + platform + deployment-topology + lifecycle +
infrastructure-dependency registries. It owns NO persistence, introduces NO second CMDB /
infrastructure-management platform / cloud-management platform / deployment orchestrator / asset inventory /
configuration database / environment manager / monitoring platform, defines NO new metrics, and NEVER creates
an environment, deploys code, provisions infrastructure, modifies topology, changes lifecycle state, executes a
cloud operation, writes configuration, or deletes an environment. Cloud resources, servers, containers, VMs,
formal lifecycle state, retirement records, decommission schedule, host / network topology, and live deployment
execution have no authoritative owner in the platform today — those are declared registry entries reporting
`not_configured` HONESTLY, never a fabricated environment, deployment, infrastructure, topology, lifecycle
state, environment health, platform ownership, or retirement status. Every dashboard carries its generated
timestamp, governing services, source inventory, explainable panels, deep links, and its configured /
not_configured domain lists. Gate- and policy-aware; returns ``None`` when a dashboard is not registered or the
principal lacks its required capability (route → 404/403). No credentials, secrets, tokens, environment
variables, connection strings, private keys, deployment payloads, protected infrastructure details, private
topology, or sensitive configuration values are ever emitted — counts, status, identifiers, coverage, and
verification only. The derived posture is an OPERATIONAL-VISIBILITY summary, never a certified environment
health, deployment status, provisioning outcome, or retirement decision: **environment metadata is not live
infrastructure, a deployment reference is not a deployment, and an active flag is not a lifecycle guarantee.**
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import EnvironmentDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered environment dashboard. None when not registered or unauthorized; disabled envelope
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
    board = EnvironmentDashboard(
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
    """The environment dashboards the principal may open (holds at least one required capability). Metadata
    only — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.ENVIRONMENT_DASHBOARDS:
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


def environment_summary(principal):
    """The firm environment / platform / deployment / lifecycle summary — a compact, non-leaking envelope
    backing the Advisor Workspace Environment & Platform Status panel + the Executive Dashboard + AI grounding.
    Never raises. Counts + status + coverage + verification only; never a credential / token / deployment
    payload / private topology / sensitive configuration value. An OPERATIONAL-VISIBILITY summary, never a
    fabricated environment / deployment / infrastructure / lifecycle / retirement status: environment metadata
    is not live infrastructure, a deployment reference is not a deployment, an active flag is not a lifecycle
    guarantee."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": [], "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("executive_platform_posture", "environment_profiles", "platform_inventory",
                  "deployment_reference_inventory", "lifecycle_readiness", "environment_governance_status",
                  "unconfigured_environment_domains")
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
            "operational_visibility_not_certification": True,
            "environment_metadata_is_not_live_infrastructure": True,
            "deployment_reference_is_not_a_deployment": True,
            "not_configured_domains": list(registry.not_configured_domains()),
            "governing_services": ["observability.catalog", "observability.health", "observability.service",
                                   "runtime", "integration"]}


def _record_platform_dependencies(scope, identifier):
    """Record-scoped platform dependencies — there is NO authoritative owner in the platform that maps a
    client / household RECORD to a platform / environment / infrastructure dependency. Exposing internal
    platform / environment / topology at record scope would INFER platform impact, which this layer must never
    do. Reported honestly as not_configured (available=False). Never a fabricated or inferred platform
    dependency; never internal infrastructure, deployment topology, or environment metadata unrelated to the
    record."""
    return {"enabled": True, "available": False, "config_status": registry.NOT_CONFIGURED,
            "source": f"environment_management.{scope}_platform_dependencies", "not_a_second_engine": True,
            "record_scoped_owner": registry.NOT_CONFIGURED,
            "internal_infrastructure_exposed": False, "platform_impact_inferred": False,
            "note": "no authoritative record-scoped platform / environment / infrastructure owner exists; "
                    "platform impact is never inferred at record scope",
            "signals": {}, "deep_link": "/environment-management"}


def client_platform_dependencies(principal, person_id):
    """A record-scoped platform-dependency summary for ONE client. There is no authoritative owner mapping a
    client record to platform / environment / infrastructure, so this is reported `not_configured` honestly —
    **internal infrastructure, deployment topology, and environment metadata unrelated to the record are never
    exposed, and platform impact is never inferred.** Read-only; never creates / deploys / provisions /
    modifies anything."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "available": False, "signals": {}}
    return _record_platform_dependencies("client", person_id)


def household_platform_dependencies(principal, household_id, member_ids=None):
    """A record-scoped platform-dependency summary for a household. No authoritative record-scoped platform /
    environment / infrastructure owner exists, so this is reported `not_configured` honestly — internal
    infrastructure and environment metadata unrelated to the household are never exposed, and platform impact
    is never inferred. Read-only; never creates / deploys / provisions / modifies anything."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "available": False, "signals": {}}
    return _record_platform_dependencies("household", household_id)
