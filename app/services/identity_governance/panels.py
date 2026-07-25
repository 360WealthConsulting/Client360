"""Enterprise Identity & Access Governance panel composition (Phase D.65).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second directory /
RBAC / metric, and never any password / secret / token / session ID / credential / authentication payload / raw
identity (email / name / auth_subject) / privileged-role membership / user-level permission map. Identity /
role / capability panels compose the Identity service (`list_identity_data` — users / roles / capabilities /
teams) and Security RBAC; authentication panels compose Security Authentication (`list_providers`);
authorization panels compose Security RBAC (`list_policies`), the Policy engine (`registry.coverage`), and
Security Authorization (`record_in_scope`). SSO / external IdP, MFA enforcement, service accounts, API-key
identities, access reviews, PAM, segregation of duties, identity lifecycle, and password management have no
authoritative owner and are emitted ``available=False`` with ``config_status='not_configured'`` — honest, never
a fabricated user, identity, role, permission, authentication provider, session, capability, policy
assignment, or access review. Every compose is fail-closed and self-restricts. This layer NEVER authenticates a
user, authorizes a request, assigns a role, grants or revokes a permission, modifies a policy, creates an
identity, or creates a session. A derived value describes GOVERNANCE READINESS / COVERAGE, never an
authentication result, an authorization decision, a granted permission, or a certified access review — **a
capability inventory is not a grant, a role definition is not an assignment, a provider registration is not an
authentication, and coverage is not certification.**
"""
from __future__ import annotations

from collections import Counter

from . import registry, stats
from .model import PanelResult


def _restricted(pdef):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=None,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, restricted=True,
                       available=False, derived=pdef.derived)


def _result(pdef, value, *, available=True, config_status="configured"):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=value,
                       explanation=pdef.explainability, deep_link=pdef.deep_link,
                       available=available, derived=pdef.derived, config_status=config_status)


def _not_configured(pdef, note):
    return _result(pdef, {"status": registry.NOT_CONFIGURED, "note": note}, available=False,
                   config_status=registry.NOT_CONFIGURED)


# --- read helpers (read-only, guarded) -----------------------------------------------------------------

def _identity():
    from app.services.identity import list_identity_data
    return list_identity_data()


def _providers():
    from app.security.authentication import list_providers
    return list_providers()


def _policies():
    from app.security.rbac import list_policies
    return list_policies()


def _pct(n, d):
    return round(n / d * 100, 1) if d else 0.0


# --- identity panels -----------------------------------------------------------------------------------

def _identity_inventory(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"identity_domains": len(registry.IDENTITY_REGISTRY),
                              "role_domains": len(registry.ROLE_REGISTRY),
                              "capability_domains": len(registry.CAPABILITY_REGISTRY),
                              "authentication_domains": len(registry.AUTHENTICATION_REGISTRY),
                              "authorization_domains": len(registry.AUTHORIZATION_REGISTRY),
                              "not_configured": len(nc), "capability_inventory_is_not_a_grant": True})
    except Exception:
        return _result(pdef, None, available=False)


def _user_directory_coverage(principal, pdef):
    try:
        d = _identity()
        return _result(pdef, {"users": len(d.get("users", [])), "raw_identity_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _user_status_distribution(principal, pdef):
    try:
        d = _identity()
        dist = Counter(u.get("status") for u in d.get("users", []))
        return _result(pdef, {"distribution": dict(dist), "total": len(d.get("users", []))})
    except Exception:
        return _result(pdef, None, available=False)


def _mfa_coverage(principal, pdef):
    try:
        d = _identity()
        users = d.get("users", [])
        enabled = sum(1 for u in users if u.get("mfa_enabled"))
        return _result(pdef, {"mfa_enabled": enabled, "total": len(users),
                              "coverage_percent": _pct(enabled, len(users)),
                              "enrollment_is_not_enforcement": True})
    except Exception:
        return _result(pdef, None, available=False)


def _team_coverage(principal, pdef):
    try:
        d = _identity()
        return _result(pdef, {"teams": len(d.get("teams", []))})
    except Exception:
        return _result(pdef, None, available=False)


def _identity_gaps(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


# --- role panels ---------------------------------------------------------------------------------------

def _role_inventory(principal, pdef):
    try:
        d = _identity()
        return _result(pdef, {"roles": len(d.get("roles", [])), "role_definition_is_not_an_assignment": True})
    except Exception:
        return _result(pdef, None, available=False)


def _active_roles(principal, pdef):
    try:
        d = _identity()
        roles = d.get("roles", [])
        active = sum(1 for r in roles if r.get("active"))
        return _result(pdef, {"active": active, "inactive": len(roles) - active, "total": len(roles),
                              "coverage_percent": _pct(active, len(roles))})
    except Exception:
        return _result(pdef, None, available=False)


def _system_roles(principal, pdef):
    try:
        d = _identity()
        roles = d.get("roles", [])
        system = sum(1 for r in roles if r.get("system_role"))
        return _result(pdef, {"system_roles": system, "custom_roles": len(roles) - system, "total": len(roles)})
    except Exception:
        return _result(pdef, None, available=False)


def _role_capability_governance(principal, pdef):
    try:
        d = _identity()
        roles = d.get("roles", [])
        caps = d.get("capabilities", [])
        return _result(pdef, {"derived": True, "roles": len(roles), "capabilities": len(caps),
                              "system_roles": sum(1 for r in roles if r.get("system_role")),
                              "per_role_capability_map_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


# --- capability panels ---------------------------------------------------------------------------------

def _capability_inventory(principal, pdef):
    try:
        d = _identity()
        return _result(pdef, {"capabilities": len(d.get("capabilities", [])),
                              "capability_inventory_is_not_a_grant": True})
    except Exception:
        return _result(pdef, None, available=False)


def _sensitive_capability_coverage(principal, pdef):
    try:
        d = _identity()
        caps = d.get("capabilities", [])
        sensitive = sum(1 for c in caps if c.get("sensitive"))
        return _result(pdef, {"sensitive": sensitive, "total": len(caps),
                              "sensitive_percent": _pct(sensitive, len(caps))})
    except Exception:
        return _result(pdef, None, available=False)


def _capability_coverage(principal, pdef):
    try:
        d = _identity()
        caps = d.get("capabilities", [])
        roles = d.get("roles", [])
        return _result(pdef, {"derived": True, "capabilities": len(caps), "roles": len(roles),
                              "sensitive": sum(1 for c in caps if c.get("sensitive")),
                              "user_level_permission_map_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _least_privilege_indicators(principal, pdef):
    try:
        d = _identity()
        caps = d.get("capabilities", [])
        roles = d.get("roles", [])
        sensitive = sum(1 for c in caps if c.get("sensitive"))
        system = sum(1 for r in roles if r.get("system_role"))
        return _result(pdef, {"derived": True, "sensitive_capability_ratio": _pct(sensitive, len(caps)),
                              "system_role_ratio": _pct(system, len(roles)),
                              "posture_indicator_not_an_escalation_recommendation": True})
    except Exception:
        return _result(pdef, None, available=False)


# --- authentication panels -----------------------------------------------------------------------------

def _authentication_providers(principal, pdef):
    try:
        providers = _providers()
        return _result(pdef, {"providers": len(providers), "provider_names": list(providers),
                              "provider_registration_is_not_an_authentication": True})
    except Exception:
        return _result(pdef, None, available=False)


def _session_management_status(principal, pdef):
    try:
        providers = _providers()
        return _result(pdef, {"session_management": "configured", "provider_present": bool(providers),
                              "session_id_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _mfa_enrollment(principal, pdef):
    try:
        d = _identity()
        users = d.get("users", [])
        enabled = sum(1 for u in users if u.get("mfa_enabled"))
        return _result(pdef, {"derived": True, "mfa_enabled": enabled, "total": len(users),
                              "coverage_percent": _pct(enabled, len(users)),
                              "enrollment_is_not_enforcement": True})
    except Exception:
        return _result(pdef, None, available=False)


def _sso_availability(principal, pdef):
    return _not_configured(pdef, "no SSO / external identity provider (SAML / OIDC / AD) is configured; only "
                                 "the session provider exists")


def _mfa_enforcement_availability(principal, pdef):
    return _not_configured(pdef, "no MFA-enforcement policy owner exists; the mfa_enabled flag is enrollment, "
                                 "not enforcement")


def _api_authentication_availability(principal, pdef):
    return _not_configured(pdef, "no platform API-key / token identity owner exists in the platform")


def _password_management_availability(principal, pdef):
    return _not_configured(pdef, "authentication is external (claims / auth_subject); no password store is "
                                 "owned by the platform")


# --- authorization panels ------------------------------------------------------------------------------

def _authorization_policy_coverage(principal, pdef):
    try:
        policies = _policies()
        return _result(pdef, {"policies": len(policies), "policy_names": list(policies),
                              "count_only_never_a_decision": True})
    except Exception:
        return _result(pdef, None, available=False)


def _policy_engine_coverage(principal, pdef):
    try:
        from app.services.policy import registry as pol
        cov = pol.coverage() if hasattr(pol, "coverage") else {}
        return _result(pdef, {"decision_areas": cov.get("decision_areas"),
                              "areas_covered": cov.get("areas_covered"),
                              "coverage_percent": cov.get("coverage_pct"),
                              "policy_payload_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _record_scope_authorization_status(principal, pdef):
    try:
        from app.security.authorization import record_in_scope
        return _result(pdef, {"record_scope_authorization": "configured",
                              "owner_present": callable(record_in_scope),
                              "per_record_decision_composed_at_record_scope": True,
                              "firm_wide_permission_map_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _capability_policy_status(principal, pdef):
    try:
        policies = _policies()
        return _result(pdef, {"capability_policy_registered": "capability" in policies,
                              "default_policy": "capability"})
    except Exception:
        return _result(pdef, None, available=False)


def _authorization_event_coverage(principal, pdef):
    try:
        from app.security import rbac
        return _result(pdef, {"derived": True, "authorization_events": "append_only_ledger",
                              "emit_present": hasattr(rbac, "emit_authorization_event"),
                              "event_payload_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _pam_availability(principal, pdef):
    return _not_configured(pdef, "no privileged access management (PAM) owner exists in the platform")


def _access_review_availability(principal, pdef):
    return _not_configured(pdef, "no authorization certification / access-review owner exists in the platform")


# --- governance + executive ----------------------------------------------------------------------------

def _configured_identity_domains(principal, pdef):
    try:
        total = len(registry._all_entries())
        cfg = len(registry.configured_domains())
        return _result(pdef, {"configured": cfg, "total": total, "coverage_percent": _pct(cfg, total)})
    except Exception:
        return _result(pdef, None, available=False)


def _unconfigured_identity_domains(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


def _identity_governance_status(principal, pdef):
    try:
        checked = clean = 0
        validators = (
            ("security_operations", "validate_security_operations"),
            ("change_management", "validate_change_management"),
            ("environment_management", "validate_environment_management"),
        )
        for mod, fn in validators:
            try:
                g = getattr(__import__(f"app.services.{mod}.governance", fromlist=[fn]), fn)()
                checked += 1
                if g.get("ok"):
                    clean += 1
            except Exception:
                pass
        return _result(pdef, {"checked": checked, "clean": clean,
                              "governance_coverage_not_certification": True})
    except Exception:
        return _result(pdef, None, available=False)


def _access_governance_readiness(principal, pdef):
    """DERIVED access-governance readiness — deterministic, authoritative inputs, labeled derived. GOVERNANCE
    READINESS ONLY, never an authentication result / authorization decision / certified access review."""
    try:
        signals = {}
        try:
            d = _identity()
            signals["users"] = len(d.get("users", []))
            signals["roles"] = len(d.get("roles", []))
            signals["capabilities"] = len(d.get("capabilities", []))
        except Exception:
            pass
        try:
            signals["authentication_providers"] = len(_providers())
        except Exception:
            pass
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"derived": True, "governance_coverage_not_certification": True,
                              "self_signals": signals, "not_configured_domains": len(nc),
                              "capability_inventory_is_not_a_grant": True,
                              "coverage_is_not_certification": True})
    except Exception:
        return _result(pdef, None, available=False)


def _executive_identity_posture(principal, pdef):
    try:
        d = _identity()
        users = d.get("users", [])
        configured = len(registry.configured_domains())
        not_configured = list(registry.not_configured_domains())
        return _result(pdef, {"derived": True, "governance_coverage_not_certification": True,
                              "users": len(users), "roles": len(d.get("roles", [])),
                              "capabilities": len(d.get("capabilities", [])),
                              "mfa_coverage_percent": _pct(sum(1 for u in users if u.get("mfa_enabled")),
                                                           len(users)),
                              "configured_domains": configured,
                              "not_configured_domains": len(not_configured),
                              "role_definition_is_not_an_assignment": True})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "identity_inventory": _identity_inventory,
    "user_directory_coverage": _user_directory_coverage,
    "user_status_distribution": _user_status_distribution,
    "mfa_coverage": _mfa_coverage,
    "team_coverage": _team_coverage,
    "identity_gaps": _identity_gaps,
    "role_inventory": _role_inventory,
    "active_roles": _active_roles,
    "system_roles": _system_roles,
    "role_capability_governance": _role_capability_governance,
    "capability_inventory": _capability_inventory,
    "sensitive_capability_coverage": _sensitive_capability_coverage,
    "capability_coverage": _capability_coverage,
    "least_privilege_indicators": _least_privilege_indicators,
    "authentication_providers": _authentication_providers,
    "session_management_status": _session_management_status,
    "mfa_enrollment": _mfa_enrollment,
    "sso_availability": _sso_availability,
    "mfa_enforcement_availability": _mfa_enforcement_availability,
    "api_authentication_availability": _api_authentication_availability,
    "password_management_availability": _password_management_availability,
    "authorization_policy_coverage": _authorization_policy_coverage,
    "policy_engine_coverage": _policy_engine_coverage,
    "record_scope_authorization_status": _record_scope_authorization_status,
    "capability_policy_status": _capability_policy_status,
    "authorization_event_coverage": _authorization_event_coverage,
    "pam_availability": _pam_availability,
    "access_review_availability": _access_review_availability,
    "configured_identity_domains": _configured_identity_domains,
    "unconfigured_identity_domains": _unconfigured_identity_domains,
    "identity_governance_status": _identity_governance_status,
    "access_governance_readiness": _access_governance_readiness,
    "executive_identity_posture": _executive_identity_posture,
}


def compute_panel(principal, key):
    """Compose one panel by key. Read-only, fail-closed, self-restricting. Returns a PanelResult, or None if
    the panel is not registered / not explainable."""
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
