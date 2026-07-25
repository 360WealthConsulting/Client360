"""Enterprise Identity, Access Governance & Authorization Intelligence engine (Phase D.65).

A READ-ONLY composition over the platform's authoritative identity / role / capability / authentication /
authorization owners — the Identity service (`list_identity_data` — the user / role / capability / team
directory), Security RBAC (role & capability resolution, authorization policies), Security Authentication
(providers), the Policy engine (policy coverage), and Security Authorization (record-scope decisions). It
composes named identity dashboards from declarative identity + role + capability + authentication +
authorization registries. It owns NO persistence, introduces NO second identity provider / authentication
service / authorization engine / RBAC system / directory / SSO platform / policy engine / user-management
platform, defines NO new metrics, and NEVER authenticates a user, authorizes a request, assigns a role, grants
or revokes a permission, modifies a policy, creates an identity, creates a session, manages a password, or
mutates identity lifecycle. SSO / external IdP, MFA enforcement, service accounts, API-key identities, access
reviews, PAM, segregation of duties, identity lifecycle, and password management have no authoritative owner in
the platform today — those are declared registry entries reporting `not_configured` HONESTLY, never a
fabricated user, identity, role, permission, authentication provider, session, capability, policy assignment,
or access review. Every dashboard carries its generated timestamp, governing services, source inventory,
explainable panels, deep links, and its configured / not_configured domain lists. Gate- and policy-aware;
returns ``None`` when a dashboard is not registered or the principal lacks its required capability (route →
404/403). No passwords, secrets, tokens, session IDs, credentials, authentication payloads, raw identities,
privileged-role membership, or user-level permission maps are ever emitted — counts, coverage, status, and
ratios only. The derived posture is a GOVERNANCE-READINESS summary, never an authentication result, an
authorization decision, a granted permission, or a certified access review: **a capability inventory is not a
grant, a role definition is not an assignment, a provider registration is not an authentication, and coverage
is not certification.**
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import IdentityDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered identity dashboard. None when not registered or unauthorized; disabled envelope
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
    board = IdentityDashboard(
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
    """The identity dashboards the principal may open (holds at least one required capability). Metadata only —
    never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.IDENTITY_DASHBOARDS:
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


def identity_summary(principal):
    """The firm identity / access-governance summary — a compact, non-leaking envelope backing the Advisor
    Workspace Identity & Access Status panel + the Executive Dashboard + AI grounding. Never raises. Counts +
    coverage + status only; never a password / secret / token / session ID / credential / raw identity /
    privileged-role membership / user-level permission map. A GOVERNANCE-READINESS summary, never a fabricated
    identity / role / permission / authentication / access review: a capability inventory is not a grant, a
    role definition is not an assignment, coverage is not certification."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("executive_identity_posture", "access_governance_readiness", "user_directory_coverage",
                  "mfa_coverage", "authorization_policy_coverage", "identity_governance_status",
                  "unconfigured_identity_domains")
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
            "capability_inventory_is_not_a_grant": True,
            "role_definition_is_not_an_assignment": True,
            "not_configured_domains": list(registry.not_configured_domains()),
            "governing_services": ["identity", "security.rbac", "security.authentication", "policy",
                                   "security.authorization"]}


def _in_scope(principal, entity_type, entity_id):
    from app.security.authorization import record_in_scope
    return bool(record_in_scope(principal, entity_type, entity_id))


def _record_authorization_context(principal, entity_type, entity_id):
    """Record-scoped authorization CONTEXT — composed read-only from the authoritative Security Authorization
    owner (`record_in_scope` over record_assignments). Exposes ONLY the current principal's OWN authorization
    decision for this record (the platform's actual, already-made decision) — **never another user's identity,
    a privileged role, a permission map, authentication metadata, or security configuration, and never an
    INFERRED authorization.** Read-only; never authenticates / authorizes / assigns / grants anything."""
    if entity_id is None:
        return {"enabled": True, "available": False, "config_status": registry.NOT_CONFIGURED, "signals": {}}
    try:
        in_scope = _in_scope(principal, entity_type, entity_id)
    except Exception:
        stats.note("aggregation_failures", panel="record_authorization_context")
        return {"enabled": True, "available": False, "signals": {}, "error": "unavailable"}
    return {"enabled": True, "available": True, "config_status": registry.CONFIGURED,
            "source": "security.authorization.record_in_scope", "not_a_second_engine": True,
            "internal_identities_exposed": False, "privileged_roles_exposed": False,
            "permission_map_exposed": False, "authorization_inferred": False,
            "signals": {"principal_in_scope": in_scope},
            "deep_link": "/identity-governance"}


def client_authorization_context(principal, person_id):
    """A record-scoped authorization-context summary for ONE client — ONLY the current principal's OWN
    authorization decision for this record, composed read-only from the authoritative Security Authorization
    owner (`record_in_scope`). **No internal identities, privileged roles, permission maps, authentication
    metadata, or security configuration are ever exposed, and authorization is never inferred.** Read-only;
    never authenticates / authorizes / assigns / grants anything. Record scope is validated at the Client 360
    boundary."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "available": False, "signals": {}}
    return _record_authorization_context(principal, "person", person_id)


def household_authorization_context(principal, household_id, member_ids=None):
    """A record-scoped authorization-context summary for a household — ONLY the current principal's OWN
    authorization decision for this household record, composed read-only from the authoritative Security
    Authorization owner. No internal identities, privileged roles, permission maps, authentication metadata, or
    security configuration are ever exposed, and authorization is never inferred. Read-only; never
    authenticates / authorizes / assigns / grants anything."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "available": False, "signals": {}}
    return _record_authorization_context(principal, "household", household_id)
