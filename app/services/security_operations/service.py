"""Enterprise Security Operations, Identity Governance & Platform Security Intelligence engine (Phase D.54).

A READ-ONLY composition over the platform's authoritative security owners — the Security metadata domain
(`security.service` overview, `providers`, `policies`, `incidents`), the Identity owner
(`identity.list_identity_data`), the RBAC foundation, the hash-chain audit log (`audit_export`), and the MFA
flag (via the Analytics Registry). It composes named security dashboards (authentication, authorization,
identity governance, MFA, sessions, audit, security posture) from a declarative identity + security + panel
registry. It owns NO persistence, introduces NO second IAM platform, identity provider, RBAC engine,
authentication system, authorization engine, MFA provider, audit-logging platform, or SIEM, defines NO new
metrics, and NEVER authenticates a user, creates a user, revokes a session, issues a token, resets a
password, or alters a permission. Every dashboard carries its generated timestamp, governing services,
source inventory, explainable panels, and deep links. Gate- and policy-aware; returns ``None`` when a
dashboard is not registered or the principal lacks its required capability (route → 404/403). No passwords,
secrets, tokens, session IDs, or authentication payloads are ever emitted — counts + status only.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import SecurityDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered security dashboard. None when not registered or unauthorized; disabled envelope
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
    board = SecurityDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The security dashboards the principal may open (holds at least one required capability). Metadata
    only — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.SECURITY_DASHBOARDS:
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


def security_summary(principal):
    """The firm security-operations summary — a compact, non-leaking envelope backing the Advisor Workspace
    Security Operations panel + the Executive Dashboard + AI grounding. Never raises. Counts + status only;
    never a password/secret/token/session-ID/payload."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("security_overview", "mfa_coverage", "authorization_failures", "audit_integrity")
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
            "governing_services": ["security", "identity", "security.audit"]}


def _entity_access(principal, entity_type, entity_id):
    """Rollup the authoritative record-assignment access grants for one entity (who can access this record),
    composed read-only from the authorization owner (object_security.resolve_assignments). Counts only,
    never a payload; never alters an assignment. Record scope is validated at the Client360 boundary."""
    from app.security.object_security import resolve_assignments
    assignments = resolve_assignments(entity_type, entity_id)
    users = {a.get("user_id") for a in assignments if a.get("user_id")}
    teams = {a.get("team_id") for a in assignments if a.get("team_id")}
    by_type = {}
    for a in assignments:
        t = a.get("assignment_type") or "assigned"
        by_type[t] = by_type.get(t, 0) + 1
    return len(users), len(teams), by_type


def client_security(principal, person_id):
    """A compact security & access summary for ONE client — who can access this client's record, composed
    read-only from the authoritative authorization owner (record assignments). Counts only, never a payload;
    deep-links to the authoritative admin surface. Never authenticates/authorizes/alters anything."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "assigned_users": 0}
    try:
        users, teams, by_type = _entity_access(principal, "person", person_id)
        return {"enabled": True, "source": "security.object_security.resolve_assignments",
                "not_a_second_engine": True, "assigned_users": users, "assigned_teams": teams,
                "by_assignment_type": by_type, "deep_link": "/admin"}
    except Exception:
        stats.note("aggregation_failures", panel="client_security")
        return {"enabled": True, "assigned_users": 0, "error": "unavailable"}


def household_security(principal, household_id, member_ids=None):
    """Aggregated security & access summary for a household — who can access the household + its members'
    records, composed read-only from the authoritative authorization owner. Counts only; a rollup, never a
    payload; never alters an assignment."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "assigned_users": 0}
    try:
        users, teams = set(), set()
        by_type = {}
        pairs = [("household", household_id)] + [("person", m) for m in (member_ids or [])]
        from app.security.object_security import resolve_assignments
        for etype, eid in pairs:
            for a in resolve_assignments(etype, eid):
                if a.get("user_id"):
                    users.add(a["user_id"])
                if a.get("team_id"):
                    teams.add(a["team_id"])
                t = a.get("assignment_type") or "assigned"
                by_type[t] = by_type.get(t, 0) + 1
        return {"enabled": True, "source": "security.object_security.resolve_assignments",
                "not_a_second_engine": True, "assigned_users": len(users), "assigned_teams": len(teams),
                "by_assignment_type": by_type, "member_count": len(member_ids or []), "deep_link": "/admin"}
    except Exception:
        stats.note("aggregation_failures", panel="household_security")
        return {"enabled": True, "assigned_users": 0, "error": "unavailable"}
