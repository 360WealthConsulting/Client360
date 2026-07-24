"""Security Operations panel composition (Phase D.54).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric,
and never any password / secret / token / session ID / authentication payload. Authentication / policy /
provider / incident / posture panels compose the AUTHORITATIVE Security metadata domain
(`security.service` / `providers` / `policies` / `incidents`); identity / RBAC panels compose the Identity
owner (`identity.list_identity_data`); MFA panels compose the Identity owner's `mfa_enabled` flag via the
Analytics Registry; audit / session panels compose the hash-chain audit log (`audit_export`). Every compose
is fail-closed (a source outage or missing `audit.read` yields an unavailable panel, never an exception) and
self-restricts: a principal lacking the panel's capability is shown a ``restricted`` panel, never its value.
This layer NEVER authenticates a user, creates a user, revokes a session, issues a token, resets a password,
or alters a permission — it only composes counts + status.
"""
from __future__ import annotations

from . import registry, stats
from .model import PanelResult

_AUDIT_LIMIT = 1000
_PRIVILEGED_CODES = ("record.read_all", "record.write_all")


def _restricted(pdef):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=None,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, restricted=True,
                       available=False)


def _result(pdef, value, *, available=True):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=value,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, available=available)


# --- declarative-registry panels ----------------------------------------------------------------------

def _registered_identities(principal, pdef):
    try:
        return _result(pdef, {"count": len(registry.IDENTITY_REGISTRY),
                              "identity_classes": [i.key for i in registry.IDENTITY_REGISTRY]})
    except Exception:
        return _result(pdef, None, available=False)


def _registered_security_domains(principal, pdef):
    try:
        by_cat = {}
        for s in registry.SECURITY_REGISTRY:
            by_cat[s.category] = by_cat.get(s.category, 0) + 1
        return _result(pdef, {"count": len(registry.SECURITY_REGISTRY),
                              "domains": [s.key for s in registry.SECURITY_REGISTRY], "by_category": by_cat})
    except Exception:
        return _result(pdef, None, available=False)


# --- Security metadata domain (providers / policies / incidents / overview) ----------------------------

def _security_providers(principal, pdef):
    try:
        from app.services.security.providers import metrics
        m = metrics(principal)
        return _result(pdef, {"enabled_providers": m.get("enabled_providers", 0),
                              "total_providers": m.get("total_providers", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _identity_providers(principal, pdef):
    try:
        from app.services.security.providers import list_providers
        by_kind = {}
        for r in list_providers():
            k = r.get("provider_kind") or "other"
            by_kind[k] = by_kind.get(k, 0) + 1
        return _result(pdef, {"count": sum(by_kind.values()), "by_kind": by_kind})
    except Exception:
        return _result(pdef, None, available=False)


def _mfa_policies(principal, pdef):
    try:
        from app.services.security.policies import list_policies
        return _result(pdef, {"mfa_policies": len(list_policies(policy_type="mfa"))})
    except Exception:
        return _result(pdef, None, available=False)


def _security_overview(principal, pdef):
    try:
        from app.services.security.service import overview_metrics
        m = overview_metrics(principal)
        return _result(pdef, {"active_policies": m.get("active_policies", 0),
                              "enabled_providers": m.get("enabled_providers", 0),
                              "open_incidents": m.get("open_incidents", 0),
                              "overdue_secret_rotations": m.get("overdue_secret_rotations", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _open_incidents(principal, pdef):
    try:
        from app.services.security.incidents import metrics
        m = metrics(principal)
        return _result(pdef, {"open_incidents": m.get("open_incidents", 0),
                              "open_findings": m.get("open_findings", 0),
                              "pending_exceptions": m.get("pending_exceptions", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Identity owner (list_identity_data) ---------------------------------------------------------------

def _identity_data():
    from app.services.identity import list_identity_data
    return list_identity_data()


def _roles_capabilities(principal, pdef):
    try:
        data = _identity_data()
        return _result(pdef, {"roles": len(data.get("roles", [])),
                              "capabilities": len(data.get("capabilities", []))})
    except Exception:
        return _result(pdef, None, available=False)


def _record_scope(principal, pdef):
    try:
        data = _identity_data()
        codes = {c.get("code") for c in data.get("capabilities", [])}
        privileged = sum(1 for c in codes if c in _PRIVILEGED_CODES or (c or "").endswith(".admin"))
        return _result(pdef, {"privileged_capabilities": privileged, "total_capabilities": len(codes)})
    except Exception:
        return _result(pdef, None, available=False)


def _user_inventory(principal, pdef):
    try:
        data = _identity_data()
        by_status = {}
        for u in data.get("users", []):
            s = u.get("status") or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        return _result(pdef, {"total": sum(by_status.values()), "by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


def _teams_roles(principal, pdef):
    try:
        data = _identity_data()
        return _result(pdef, {"teams": len(data.get("teams", [])), "roles": len(data.get("roles", []))})
    except Exception:
        return _result(pdef, None, available=False)


def _active_users(principal, pdef):
    try:
        data = _identity_data()
        active = sum(1 for u in data.get("users", []) if u.get("status") == "active")
        return _result(pdef, {"active_users": active})
    except Exception:
        return _result(pdef, None, available=False)


# --- MFA (Identity mfa_enabled flag via Analytics) -----------------------------------------------------

def _mfa_enabled_users(principal, pdef):
    try:
        from app.services.analytics.sources import security_mfa_enabled_user_count
        return _result(pdef, {"mfa_enabled_users": security_mfa_enabled_user_count(principal)})
    except Exception:
        return _result(pdef, None, available=False)


def _mfa_coverage(principal, pdef):
    try:
        from app.services.analytics.sources import security_mfa_enabled_user_count
        enabled = security_mfa_enabled_user_count(principal)
        active = 0
        try:
            data = _identity_data()
            active = sum(1 for u in data.get("users", []) if u.get("status") == "active")
        except Exception:
            pass
        coverage = round(enabled / active * 100, 1) if active else 0.0
        return _result(pdef, {"coverage_percent": coverage, "mfa_enabled_users": enabled,
                              "active_users": active})
    except Exception:
        return _result(pdef, None, available=False)


# --- Hash-chain audit log (audit_export) ---------------------------------------------------------------

def _audit_events(principal, **filters):
    """Read a bounded recent window of audit events. Returns list, or None when the principal lacks
    ``audit.read`` (fail-closed) or the read fails."""
    try:
        from app.security.audit_export import read_audit_events
        return read_audit_events(principal, filters=filters or None, limit=_AUDIT_LIMIT)
    except Exception:
        return None


def _authentication_activity(principal, pdef):
    ev = _audit_events(principal)
    if ev is None:
        return _result(pdef, None, available=False)
    logins = sum(1 for e in ev if "login" in (e.get("action") or ""))
    failed = sum(1 for e in ev if e.get("outcome") in ("failure", "denied")
                 and ("auth" in (e.get("action") or "") or "authentic" in (e.get("action") or "")))
    return _result(pdef, {"login_events": logins, "failed_authentication_events": failed})


def _authorization_failures(principal, pdef):
    ev = _audit_events(principal, outcome="denied")
    if ev is None:
        return _result(pdef, None, available=False)
    return _result(pdef, {"denied_events": len(ev)})


def _session_activity(principal, pdef):
    ev = _audit_events(principal)
    if ev is None:
        return _result(pdef, None, available=False)
    logins = sum(1 for e in ev if "login" in (e.get("action") or ""))
    logouts = sum(1 for e in ev if "logout" in (e.get("action") or ""))
    return _result(pdef, {"logins": logins, "logouts": logouts})


def _session_revocations(principal, pdef):
    ev = _audit_events(principal)
    if ev is None:
        return _result(pdef, None, available=False)
    revocations = sum(1 for e in ev if "revoked" in (e.get("action") or "")
                      or "session_revoked" in (e.get("action") or ""))
    return _result(pdef, {"session_revocations": revocations})


def _audit_activity(principal, pdef):
    ev = _audit_events(principal)
    if ev is None:
        return _result(pdef, None, available=False)
    return _result(pdef, {"recent_events": len(ev), "window": _AUDIT_LIMIT})


def _audit_outcomes(principal, pdef):
    ev = _audit_events(principal)
    if ev is None:
        return _result(pdef, None, available=False)
    by_outcome = {}
    for e in ev:
        o = e.get("outcome") or "unknown"
        by_outcome[o] = by_outcome.get(o, 0) + 1
    return _result(pdef, {"by_outcome": by_outcome})


def _audit_integrity(principal, pdef):
    try:
        from app.security.audit_export import verify_integrity
        r = verify_integrity(principal)
        return _result(pdef, {"ok": bool(r.get("ok")), "checked": r.get("checked", 0),
                              "first_failure_id": r.get("first_failure_id")})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "registered_identities": _registered_identities,
    "registered_security_domains": _registered_security_domains,
    "security_providers": _security_providers,
    "identity_providers": _identity_providers,
    "mfa_policies": _mfa_policies,
    "security_overview": _security_overview,
    "open_incidents": _open_incidents,
    "roles_capabilities": _roles_capabilities,
    "record_scope": _record_scope,
    "user_inventory": _user_inventory,
    "teams_roles": _teams_roles,
    "active_users": _active_users,
    "mfa_enabled_users": _mfa_enabled_users,
    "mfa_coverage": _mfa_coverage,
    "authentication_activity": _authentication_activity,
    "authorization_failures": _authorization_failures,
    "session_activity": _session_activity,
    "session_revocations": _session_revocations,
    "audit_activity": _audit_activity,
    "audit_outcomes": _audit_outcomes,
    "audit_integrity": _audit_integrity,
}


def compute_panel(principal, key):
    """Compose one panel by key. Read-only, fail-closed, self-restricting. Returns a PanelResult, or None
    if the panel is not registered / not explainable."""
    pdef = registry.panel(key)
    fn = _COMPUTE.get(key)
    if pdef is None or fn is None:
        return None
    try:
        entitled = principal.can(pdef.permission)
    except Exception:
        entitled = False
    if not entitled:
        stats.note("restricted_panels")
        return _restricted(pdef)
    try:
        result = fn(principal, pdef)
    except Exception:
        stats.note("aggregation_failures", panel=key)
        return None
    if result is None or not result.is_explainable:
        stats.note("missing_explainability", panel=key)
        return None
    stats.note("panels_composed")
    return result
