"""Security Operations registries (Phase D.54) — the declarative catalogs of the security-operations layer.

Four frozen, declarative catalogs; the layer owns NO persistence and defines NO new IAM platform, identity
provider, RBAC engine, authentication system, authorization engine, MFA provider, audit-logging platform, or
SIEM:

  * IDENTITY_REGISTRY — every user class (advisor identities, employee identities, service accounts, system
    identities, external identities, client identities). Each names its authoritative owner, authentication
    owner, authorization owner, runtime gate, and deep links. The layer authenticates NOTHING — it references
    these owners.
  * SECURITY_REGISTRY — every security domain (authentication, MFA, sessions, audit, policies, monitoring).
    Each names its authoritative owner, provider owner, monitoring owner, category, and runtime gate.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * SECURITY_DASHBOARDS — every security dashboard (owner, audience, runtime gate, panel list, required
    capabilities, navigation, refresh, governing services).

Governance verifies every identity class + security domain is registered, every panel names an authoritative
owner + source + deep link, and that this layer never becomes a second IAM / RBAC / MFA / audit platform or
SIEM.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


# --- identity registry -------------------------------------------------------

@dataclass(frozen=True)
class IdentityClass:
    key: str
    label: str
    authoritative_owner: str   # the authoritative identity owner (never re-implemented)
    authentication_owner: str  # the authoritative authentication owner
    authorization_owner: str   # the authoritative authorization/RBAC owner
    runtime_gate: str
    deep_links: tuple


def _ident(key, label, deep_links, *, authoritative_owner="identity",
           authentication_owner="security.service", authorization_owner="security.rbac",
           runtime_gate="identity.enabled"):
    return IdentityClass(key, label, authoritative_owner, authentication_owner, authorization_owner,
                         runtime_gate, tuple(deep_links))


IDENTITY_REGISTRY = (
    _ident("advisor_identities", "Advisor Identities", ("/admin", "/security")),
    _ident("employee_identities", "Employee Identities", ("/admin",)),
    _ident("service_accounts", "Service Accounts", ("/admin", "/integration"),
           authentication_owner="integration.connectors"),
    _ident("system_identities", "System Identities", ("/admin",),
           authentication_owner="security.service"),
    _ident("external_identities", "External Identities", ("/security", "/integration"),
           authentication_owner="security.providers"),
    _ident("client_identities", "Client Identities", ("/client", "/portal"),
           authoritative_owner="identity", authentication_owner="portal"),
)

_IDENT_BY_KEY = {i.key: i for i in IDENTITY_REGISTRY}


# --- security registry -------------------------------------------------------

@dataclass(frozen=True)
class SecurityDomain:
    key: str
    label: str
    category: str              # authentication | mfa | session | audit | policy | monitoring
    authoritative_owner: str   # the authoritative owner of the domain (never re-implemented)
    provider_owner: str        # the authoritative provider owner
    monitoring_owner: str      # the authoritative monitoring owner
    runtime_gate: str
    deep_links: tuple


def _sec(key, label, category, authoritative_owner, provider_owner, deep_links, *,
         monitoring_owner="security.service", runtime_gate="security.enabled"):
    return SecurityDomain(key, label, category, authoritative_owner, provider_owner, monitoring_owner,
                          runtime_gate, tuple(deep_links))


SECURITY_REGISTRY = (
    _sec("authentication", "Authentication", "authentication", "security.service", "security.providers",
         ("/security", "/security/providers")),
    _sec("mfa", "Multi-Factor Authentication", "mfa", "security.service", "security.policies",
         ("/security/policies",), monitoring_owner="analytics"),
    _sec("sessions", "Sessions", "session", "security.service", "security.service",
         ("/security",), monitoring_owner="security.audit"),
    _sec("audit", "Audit Log", "audit", "security.audit", "security.audit", ("/admin/audit",),
         monitoring_owner="security.audit"),
    _sec("policies", "Security Policies", "policy", "security.policies", "security.policies",
         ("/security/policies",)),
    _sec("monitoring", "Security Monitoring", "monitoring", "security.incidents", "observability",
         ("/security/incidents", "/observability"), monitoring_owner="observability"),
)

_SEC_BY_KEY = {s.key: s for s in SECURITY_REGISTRY}


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str                 # authoritative owning service
    source: str                # the authoritative read the value is composed from
    measure: str
    unit: str
    viz: str
    permission: str            # capability required to see the panel value (else restricted)
    deep_link: str             # the authoritative security-owner surface to drill into
    explainability: str
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    refresh, lifecycle)


PANEL_REGISTRY = (
    # authentication
    _p("security_providers", "security", "security.providers.metrics", "authentication", "count", "card",
       "security.view", "/security/providers",
       "Authentication/identity providers (enabled vs total), from the Security metadata domain. No second "
       "identity provider."),
    _p("authentication_activity", "security.audit", "security.audit_export.read_audit_events",
       "authentication", "count", "chart", "security.view", "/admin/audit",
       "Authentication activity (login / failed-login events), from the hash-chain audit log. No second "
       "authentication system."),
    _p("identity_providers", "security", "security.providers.list_providers", "authentication", "count",
       "list", "security.view", "/security/providers",
       "Registered identity/federation providers by kind, from the Security metadata domain."),
    # authorization
    _p("roles_capabilities", "identity", "identity.list_identity_data", "authorization", "count", "card",
       "security.view", "/admin",
       "RBAC inventory (roles + capabilities), from the Identity owner. No second RBAC engine."),
    _p("authorization_failures", "security.audit", "security.audit_export.read_audit_events",
       "authorization", "count", "card", "security.view", "/admin/audit",
       "Authorization-denied events, from the hash-chain audit log."),
    _p("record_scope", "identity", "identity.list_identity_data", "authorization", "count", "card",
       "security.view", "/admin",
       "Firm-wide / privileged capabilities in the catalog (record.read_all, record.write_all, *.admin), "
       "from the Identity owner."),
    # identity governance
    _p("registered_identities", "security_operations", "security_operations.registry", "identity", "count",
       "list", "security.view", "/security-operations",
       "The registered identity-class catalog — each naming its authoritative / authentication / "
       "authorization owner. No duplicated identities."),
    _p("user_inventory", "identity", "identity.list_identity_data", "identity", "count", "chart",
       "security.view", "/admin",
       "User inventory by status (active / inactive), from the Identity owner."),
    _p("teams_roles", "identity", "identity.list_identity_data", "identity", "count", "card", "security.view",
       "/admin", "Teams + roles inventory, from the Identity owner."),
    # mfa
    _p("mfa_coverage", "security_operations", "security_operations.compose", "mfa", "percent", "gauge",
       "security.view", "/security/policies",
       "Deterministic MFA coverage indicator (MFA-enabled active users vs active users) — from the Identity "
       "owner's mfa_enabled flag. No second MFA provider."),
    _p("mfa_enabled_users", "analytics", "analytics.metrics:security_mfa_enabled_users", "mfa", "count",
       "card", "security.view", "/admin",
       "MFA-enabled active users, from the Analytics Registry (the users.mfa_enabled flag)."),
    _p("mfa_policies", "security", "security.policies.list_policies", "mfa", "count", "card", "security.view",
       "/security/policies", "Registered MFA security policies, from the Security metadata domain."),
    # sessions
    _p("session_activity", "security.audit", "security.audit_export.read_audit_events", "sessions", "count",
       "chart", "security.view", "/admin/audit",
       "Session activity (login / logout events), from the hash-chain audit log. No second session store."),
    _p("active_users", "identity", "identity.list_identity_data", "sessions", "count", "card", "security.view",
       "/admin", "Active users, from the Identity owner (the session denominator)."),
    _p("session_revocations", "security.audit", "security.audit_export.read_audit_events", "sessions",
       "count", "card", "security.view", "/admin/audit",
       "Session-revocation events, from the hash-chain audit log."),
    # audit
    _p("audit_integrity", "security.audit", "security.audit_export.verify_integrity", "audit", "status",
       "card", "security.view", "/admin/audit",
       "Audit hash-chain integrity (ok / checked / first failure), from the audit-log verifier. No second "
       "audit platform."),
    _p("audit_activity", "security.audit", "security.audit_export.read_audit_events", "audit", "count", "card",
       "security.view", "/admin/audit",
       "Recent audit-event volume, from the hash-chain audit log."),
    _p("audit_outcomes", "security.audit", "security.audit_export.read_audit_events", "audit", "count", "chart",
       "security.view", "/admin/audit",
       "Audit events by outcome (success / denied / failure), from the hash-chain audit log."),
    # security posture
    _p("security_overview", "security", "security.service.overview_metrics", "posture", "count", "card",
       "security.view", "/security",
       "Firm security overview (policies / providers / secrets / incidents), from the Security metadata "
       "domain."),
    _p("open_incidents", "security", "security.incidents.metrics", "posture", "count", "card", "security.view",
       "/security/incidents",
       "Open security incidents / findings / exceptions, from the Security metadata domain."),
    _p("registered_security_domains", "security_operations", "security_operations.registry", "posture",
       "count", "list", "security.view", "/security-operations",
       "The registered security-domain catalog — each naming its authoritative / provider / monitoring "
       "owner."),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # security | operations | executive
    runtime_gate: str
    panels: tuple
    required_capabilities: tuple
    navigation: str
    refresh_policy: str
    governing_services: tuple
    lifecycle: str = "active"


def _d(key, owner, audience, gate, panels, caps, navigation, governing, *, refresh="on_view",
       lifecycle="active"):
    return DashboardDef(key, owner, audience, gate, tuple(panels), tuple(caps), navigation, refresh,
                        tuple(governing), lifecycle)


SECURITY_DASHBOARDS = (
    _d("authentication", "security_operations", "security", "security.enabled",
       ("security_providers", "authentication_activity", "identity_providers"),
       ("security.view",), "/security-operations?dashboard=authentication", ("security", "security.audit")),
    _d("authorization", "security_operations", "security", "security.enabled",
       ("roles_capabilities", "authorization_failures", "record_scope"),
       ("security.view",), "/security-operations?dashboard=authorization", ("identity", "security.audit")),
    _d("identity_governance", "security_operations", "security", "identity.enabled",
       ("registered_identities", "user_inventory", "teams_roles"),
       ("security.view",), "/security-operations?dashboard=identity_governance", ("identity",)),
    _d("mfa", "security_operations", "security", "security.enabled",
       ("mfa_coverage", "mfa_enabled_users", "mfa_policies"),
       ("security.view",), "/security-operations?dashboard=mfa", ("identity", "security", "analytics")),
    _d("sessions", "security_operations", "security", "security.enabled",
       ("session_activity", "active_users", "session_revocations"),
       ("security.view",), "/security-operations?dashboard=sessions", ("security.audit", "identity")),
    _d("audit", "security_operations", "security", "audit.enabled",
       ("audit_integrity", "audit_activity", "audit_outcomes"),
       ("security.view",), "/security-operations?dashboard=audit", ("security.audit",)),
    _d("security_posture", "security_operations", "operations", "security.enabled",
       ("security_overview", "open_incidents", "registered_security_domains"),
       ("security.view",), "/security-operations?dashboard=security_posture", ("security",)),
)

_DASH_BY_KEY = {d.key: d for d in SECURITY_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def identity_class(key) -> IdentityClass | None:
    return _IDENT_BY_KEY.get(key)


def security_domain(key) -> SecurityDomain | None:
    return _SEC_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def identity_registered(key) -> bool:
    return key in _IDENT_BY_KEY


def security_registered(key) -> bool:
    return key in _SEC_BY_KEY


def coverage() -> dict:
    return {
        "identity_classes": len(IDENTITY_REGISTRY),
        "security_domains": len(SECURITY_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(SECURITY_DASHBOARDS),
    }
