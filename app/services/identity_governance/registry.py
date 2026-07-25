"""Enterprise Identity & Access Governance registries (Phase D.65) — the declarative catalogs of the identity /
role / capability / authentication / authorization composition layer.

Seven frozen, declarative catalogs; the layer owns NO persistence and defines NO second identity provider,
authentication service, authorization engine, RBAC system, directory, SSO platform, policy engine, or
user-management platform:

  * IDENTITY_REGISTRY — the identity domains (user directory, user status, MFA coverage, teams, session
    management; identity lifecycle / JML provisioning, service accounts are NOT_CONFIGURED), each naming its
    authoritative owner, read surface, prohibited mutation surface, evidence source, capabilities, runtime
    gate, identity scope, deep links, and config status.
  * ROLE_REGISTRY — the role domains (role inventory, role activation, system roles, user-role assignments,
    role-capability mappings; birthright roles / entitlement catalog and role certification / access review are
    NOT_CONFIGURED).
  * CAPABILITY_REGISTRY — the capability domains (capability inventory, sensitive capabilities, capability
    coverage, least-privilege indicators; segregation of duties / toxic combinations and entitlement review are
    NOT_CONFIGURED).
  * AUTHENTICATION_REGISTRY — the authentication domains (authentication providers, session management, MFA
    enrollment; SSO / external IdP, MFA enforcement policy, API-key / token authentication, and password
    management are NOT_CONFIGURED — no authoritative owner exists).
  * AUTHORIZATION_REGISTRY — the authorization domains (authorization policies, policy-engine coverage,
    record-scope authorization, the capability policy, authorization events; privileged access management and
    authorization certification / access review are NOT_CONFIGURED).
  * PANEL_REGISTRY — every dashboard panel. * IDENTITY_DASHBOARDS — every identity dashboard.

Governance verifies every registry key is unique, every configured entry names an authoritative owner, every
panel names an authoritative owner + source + deep link, every derived value is labeled, and that this layer
never becomes a second identity provider / authentication service / authorization engine / RBAC system /
directory / SSO platform / policy engine / user-management platform. Where no authoritative owner exists (SSO /
external IdP, MFA enforcement, service accounts, API-key identities, access reviews, PAM, segregation of duties,
identity lifecycle, password management), the entry is declared `not_configured` and reported honestly — never
a fabricated user, identity, role, permission, authentication provider, session, capability, policy assignment,
or access review. **A capability inventory is not a grant, a role definition is not an assignment, a provider
registration is not an authentication, and coverage is not certification.**
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"
CONFIGURED = "configured"


@dataclass(frozen=True)
class DomainEntry:
    key: str
    label: str
    owner: str                 # authoritative owner (or "not_configured")
    read_surface: str          # the authoritative read
    mutation_surface: str      # the prohibited mutation surface (never called)
    evidence_source: str       # where the evidence lives
    capabilities: tuple
    runtime_gate: str
    identity_scope: str        # firm | user | role | record | not_scoped
    deep_links: tuple
    config_status: str = CONFIGURED


def _e(key, label, owner, read_surface, mutation_surface, evidence_source, deep_links, *,
       capabilities=("identity.manage",), runtime_gate="identity_governance.enabled",
       identity_scope="firm", config_status=CONFIGURED):
    return DomainEntry(key, label, owner, read_surface, mutation_surface, evidence_source, tuple(capabilities),
                       runtime_gate, identity_scope, tuple(deep_links), config_status)


_ADMIN = ("/admin",)
_NC = NOT_CONFIGURED


# --- identity registry -------------------------------------------------------

IDENTITY_REGISTRY = (
    _e("user_directory", "User Directory", "identity", "identity.list_identity_data",
       "invite_user", "users", _ADMIN, identity_scope="user"),
    _e("user_status", "User Status", "identity", "identity.list_identity_data",
       "set_user_status", "users.status", _ADMIN, identity_scope="user"),
    _e("mfa_coverage", "MFA Coverage", "identity", "identity.list_identity_data",
       "set_user_status", "users.mfa_enabled", _ADMIN, identity_scope="user"),
    _e("team_directory", "Team Directory", "identity", "identity.list_identity_data",
       "add_team_membership", "teams + team_memberships", _ADMIN),
    _e("session_management", "Session Management", "security.service", "security.authentication.list_providers",
       "create_session", "user_sessions", _ADMIN,
       runtime_gate="authentication_landscape.enabled"),
    _e("identity_lifecycle", "Identity Lifecycle (JML)", _NC, "n/a", "n/a", "n/a", _ADMIN, config_status=_NC),
    _e("service_accounts", "Service Accounts", _NC, "n/a", "n/a", "n/a", _ADMIN, config_status=_NC),
    _e("account_provisioning", "Account Provisioning", _NC, "n/a", "n/a", "n/a", _ADMIN, config_status=_NC),
)


# --- role registry -----------------------------------------------------------

ROLE_REGISTRY = (
    _e("role_inventory", "Role Inventory", "security.rbac", "identity.list_identity_data",
       "compose_role", "roles", _ADMIN, identity_scope="role"),
    _e("role_activation", "Role Activation State", "security.rbac", "identity.list_identity_data",
       "compose_role", "roles.active", _ADMIN, identity_scope="role"),
    _e("system_roles", "System Roles", "security.rbac", "identity.list_identity_data",
       "compose_role", "roles.system_role", _ADMIN, identity_scope="role"),
    _e("user_role_assignments", "User-Role Assignments", "security.rbac", "security.rbac.resolve_roles",
       "assign_role", "user_roles", _ADMIN, identity_scope="user"),
    _e("role_capability_mappings", "Role-Capability Mappings", "security.rbac",
       "security.rbac.resolve_capabilities", "compose_role", "role_capabilities", _ADMIN,
       identity_scope="role"),
    _e("birthright_roles", "Birthright Roles / Entitlement Catalog", _NC, "n/a", "n/a", "n/a", _ADMIN,
       config_status=_NC),
    _e("role_certification", "Role Certification / Access Review", _NC, "n/a", "n/a", "n/a", _ADMIN,
       config_status=_NC),
)


# --- capability registry -----------------------------------------------------

CAPABILITY_REGISTRY = (
    _e("capability_inventory", "Capability Inventory", "security.rbac", "identity.list_identity_data",
       "compose_role", "capabilities", _ADMIN, identity_scope="firm"),
    _e("sensitive_capabilities", "Sensitive Capabilities", "security.rbac", "identity.list_identity_data",
       "compose_role", "capabilities.sensitive", _ADMIN, identity_scope="firm"),
    _e("capability_coverage", "Capability Coverage", "security.rbac", "identity.list_identity_data",
       "compose_role", "capabilities + roles", _ADMIN, identity_scope="firm"),
    _e("least_privilege_indicators", "Least-Privilege Indicators", "security.rbac",
       "identity.list_identity_data", "compose_role", "capabilities.sensitive + roles.system_role", _ADMIN,
       identity_scope="firm"),
    _e("segregation_of_duties", "Segregation of Duties / Toxic Combinations", _NC, "n/a", "n/a", "n/a",
       _ADMIN, config_status=_NC),
    _e("entitlement_review", "Entitlement Review", _NC, "n/a", "n/a", "n/a", _ADMIN, config_status=_NC),
)


# --- authentication registry -------------------------------------------------

AUTHENTICATION_REGISTRY = (
    _e("authentication_providers", "Authentication Providers", "security.authentication",
       "security.authentication.list_providers", "register_provider", "authentication providers", _ADMIN,
       runtime_gate="authentication_landscape.enabled"),
    _e("session_inventory", "Session Management", "security.service",
       "security.authentication.list_providers", "create_session", "user_sessions", _ADMIN,
       runtime_gate="authentication_landscape.enabled"),
    _e("mfa_enrollment", "MFA Enrollment", "identity", "identity.list_identity_data", "set_user_status",
       "users.mfa_enabled", _ADMIN, runtime_gate="authentication_landscape.enabled", identity_scope="user"),
    _e("sso_providers", "SSO / External Identity Providers", _NC, "n/a", "n/a", "n/a", _ADMIN,
       runtime_gate="authentication_landscape.enabled", config_status=_NC),
    _e("mfa_enforcement", "MFA Enforcement Policy", _NC, "n/a", "n/a", "n/a", _ADMIN,
       runtime_gate="authentication_landscape.enabled", config_status=_NC),
    _e("api_authentication", "API-Key / Token Authentication", _NC, "n/a", "n/a", "n/a", _ADMIN,
       runtime_gate="authentication_landscape.enabled", config_status=_NC),
    _e("password_management", "Password Management", _NC, "n/a", "n/a", "n/a", _ADMIN,
       runtime_gate="authentication_landscape.enabled", config_status=_NC),
)


# --- authorization registry --------------------------------------------------

AUTHORIZATION_REGISTRY = (
    _e("authorization_policies", "Authorization Policies", "security.rbac", "security.rbac.list_policies",
       "register_policy", "rbac policies", _ADMIN, runtime_gate="authorization_landscape.enabled"),
    _e("policy_engine_coverage", "Policy-Engine Coverage", "policy", "policy.registry.coverage",
       "policy mutation", "policy engine", ("identity.manage",), runtime_gate="authorization_landscape.enabled"),
    _e("record_scope_authorization", "Record-Scope Authorization", "security.authorization",
       "security.authorization.record_in_scope", "assign_record", "record_assignments", _ADMIN,
       runtime_gate="authorization_landscape.enabled", identity_scope="record"),
    _e("capability_policy", "Capability Policy", "security.rbac", "security.rbac.list_policies",
       "register_policy", "CapabilityPolicy", _ADMIN, runtime_gate="authorization_landscape.enabled"),
    _e("authorization_events", "Authorization Events", "security.rbac",
       "security.rbac.emit_authorization_event", "n/a (append-only ledger)", "audit_events", _ADMIN,
       runtime_gate="authorization_landscape.enabled"),
    _e("privileged_access_management", "Privileged Access Management", _NC, "n/a", "n/a", "n/a", _ADMIN,
       runtime_gate="authorization_landscape.enabled", config_status=_NC),
    _e("authorization_certification", "Authorization Certification / Access Review", _NC, "n/a", "n/a", "n/a",
       _ADMIN, runtime_gate="authorization_landscape.enabled", config_status=_NC),
)

_CD_BY_KEY = {}
for _reg in (IDENTITY_REGISTRY, ROLE_REGISTRY, CAPABILITY_REGISTRY, AUTHENTICATION_REGISTRY,
             AUTHORIZATION_REGISTRY):
    for _entry in _reg:
        _CD_BY_KEY[_entry.key] = _entry


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str
    source: str
    measure: str
    unit: str
    viz: str
    permission: str
    deep_link: str
    explainability: str
    derived: bool = False
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       derived=False, refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    derived, refresh, lifecycle)


_NC_NOTE = "NO authoritative owner exists in the platform today; reported not_configured, never fabricated."

PANEL_REGISTRY = (
    # identity
    _p("identity_inventory", "identity", "identity_governance.registry", "identity", "count", "list",
       "identity.manage", "/identity-governance",
       "The registered identity domains — each naming its authoritative owner + read + prohibited mutation "
       "surface + evidence + config status. Metadata only. A capability inventory is not a grant.",
       derived=True),
    _p("user_directory_coverage", "identity", "identity.list_identity_data", "identity", "count", "card",
       "identity.manage", "/admin",
       "User-directory size (a count of provisioned users), from the Identity service. Count only — never a "
       "raw identity / email / auth_subject."),
    _p("user_status_distribution", "identity", "identity.list_identity_data", "identity", "distribution",
       "chart", "identity.manage", "/admin",
       "User-status distribution (invited / active / disabled), from the Identity service. Counts only."),
    _p("mfa_coverage", "identity", "identity.list_identity_data", "authentication", "coverage", "gauge",
       "identity.manage", "/admin",
       "MFA-enrollment coverage — users with MFA enabled vs total (the `mfa_enabled` flag). A DERIVED coverage "
       "ratio; MFA enrollment is not MFA enforcement (which has no owner).", derived=True),
    _p("team_coverage", "identity", "identity.list_identity_data", "identity", "count", "card",
       "identity.manage", "/admin",
       "Team-directory size (a count of teams), from the Identity service. Counts only."),
    _p("identity_gaps", "identity_governance", "identity_governance.registry", "identity", "list", "list",
       "identity.manage", "/identity-governance",
       "Identity / role / capability / authentication / authorization areas with no authoritative owner (SSO, "
       "service accounts, access reviews, PAM, segregation of duties) — reported honestly.", derived=True),
    # role
    _p("role_inventory", "security.rbac", "identity.list_identity_data", "role", "count", "card",
       "identity.manage", "/admin",
       "Role-inventory size (a count of defined roles), from the Identity service / Security RBAC. A role "
       "definition is not an assignment."),
    _p("active_roles", "security.rbac", "identity.list_identity_data", "role", "coverage", "gauge",
       "identity.manage", "/admin",
       "Active vs inactive roles (the `active` flag) — a DERIVED coverage ratio.", derived=True),
    _p("system_roles", "security.rbac", "identity.list_identity_data", "role", "count", "card",
       "identity.manage", "/admin",
       "System roles vs custom roles (the `system_role` flag). Counts only."),
    _p("role_capability_governance", "security.rbac", "identity_governance.compose", "role", "verification",
       "card", "identity.manage", "/admin",
       "DERIVED role-capability governance posture — roles + capabilities + system roles as a structure "
       "indicator. Detailed per-role capability maps are surfaced only at the authoritative admin surface, "
       "never here.", derived=True),
    # capability
    _p("capability_inventory", "security.rbac", "identity.list_identity_data", "capability", "count", "card",
       "identity.manage", "/admin",
       "Capability-inventory size (a count of defined capabilities), from the Identity service / Security "
       "RBAC. A capability inventory is not a grant."),
    _p("sensitive_capability_coverage", "security.rbac", "identity.list_identity_data", "capability",
       "coverage", "gauge", "identity.manage", "/admin",
       "Sensitive vs total capabilities (the `sensitive` flag) — a DERIVED coverage ratio.", derived=True),
    _p("capability_coverage", "security.rbac", "identity_governance.compose", "capability", "verification",
       "card", "identity.manage", "/admin",
       "DERIVED capability-coverage posture — capabilities + roles + sensitive-capability ratio. A governance "
       "indicator; never a user-level permission map.", derived=True),
    _p("least_privilege_indicators", "security.rbac", "identity_governance.compose", "capability", "ratio",
       "gauge", "identity.manage", "/admin",
       "DERIVED least-privilege indicators — sensitive-capability ratio + system-role ratio. A posture "
       "indicator only; never a privilege-escalation recommendation.", derived=True),
    # authentication
    _p("authentication_providers", "security.authentication", "security.authentication.list_providers",
       "authentication", "count", "card", "identity.manage", "/admin",
       "Registered authentication providers (session today; SSO / MFA are future Protocols), from Security "
       "Authentication. A provider registration is not an authentication."),
    _p("session_management_status", "security.service", "security.authentication.list_providers",
       "authentication", "status", "card", "identity.manage", "/admin",
       "Session-management status (managed by Security service via the session provider). Status only — never "
       "a session ID / token / credential."),
    _p("mfa_enrollment", "identity", "identity.list_identity_data", "authentication", "coverage", "gauge",
       "identity.manage", "/admin",
       "MFA-enrollment coverage (users with MFA enabled). A DERIVED coverage ratio; enrollment is not "
       "enforcement.", derived=True),
    _p("sso_availability", "not_configured", "identity_governance.registry", "authentication", "status",
       "card", "identity.manage", "/identity-governance",
       "SSO / external identity providers. " + _NC_NOTE, derived=True),
    _p("mfa_enforcement_availability", "not_configured", "identity_governance.registry", "authentication",
       "status", "card", "identity.manage", "/identity-governance",
       "MFA enforcement policy. " + _NC_NOTE + " Enrollment is not enforcement.", derived=True),
    _p("api_authentication_availability", "not_configured", "identity_governance.registry", "authentication",
       "status", "card", "identity.manage", "/identity-governance",
       "API-key / token authentication. " + _NC_NOTE, derived=True),
    _p("password_management_availability", "not_configured", "identity_governance.registry", "authentication",
       "status", "card", "identity.manage", "/identity-governance",
       "Password management. " + _NC_NOTE + " Authentication is external (claims / auth_subject).",
       derived=True),
    # authorization
    _p("authorization_policy_coverage", "security.rbac", "security.rbac.list_policies", "authorization",
       "count", "card", "identity.manage", "/admin",
       "Registered authorization policies (the RBAC policy set), from Security RBAC. Counts only — never an "
       "authorization decision."),
    _p("policy_engine_coverage", "policy", "policy.registry.coverage", "policy", "coverage", "gauge",
       "identity.manage", "/runtime",
       "Policy-engine coverage (decision-area coverage), from the Policy engine. Coverage only — never a "
       "policy payload or a decision."),
    _p("record_scope_authorization_status", "security.authorization",
       "security.authorization.record_in_scope", "authorization", "status", "card", "identity.manage",
       "/admin",
       "Record-scope authorization is configured (Security Authorization owns `record_in_scope` over "
       "record_assignments). Status only — a per-record decision is composed at record scope, never a "
       "firm-wide permission map."),
    _p("capability_policy_status", "security.rbac", "security.rbac.list_policies", "authorization", "status",
       "card", "identity.manage", "/admin",
       "The Capability policy is registered as the default authorization policy, from Security RBAC. Status "
       "only."),
    _p("authorization_event_coverage", "security.rbac", "identity_governance.compose", "authorization",
       "status", "card", "identity.manage", "/admin",
       "Authorization events (granted / denied) are emitted to the append-only audit ledger, from Security "
       "RBAC. A DERIVED availability indicator; never an event payload or a decision.", derived=True),
    _p("pam_availability", "not_configured", "identity_governance.registry", "authorization", "status",
       "card", "identity.manage", "/identity-governance",
       "Privileged access management (PAM). " + _NC_NOTE, derived=True),
    _p("access_review_availability", "not_configured", "identity_governance.registry", "authorization",
       "status", "card", "identity.manage", "/identity-governance",
       "Authorization certification / access review. " + _NC_NOTE, derived=True),
    # governance + executive
    _p("configured_identity_domains", "identity_governance", "identity_governance.registry", "identity",
       "coverage", "gauge", "identity.manage", "/identity-governance",
       "Configured vs not_configured identity / role / capability / authentication / authorization coverage — "
       "a DERIVED coverage summary.", derived=True),
    _p("unconfigured_identity_domains", "identity_governance", "identity_governance.registry", "identity",
       "list", "list", "identity.manage", "/identity-governance",
       "The identity / role / capability / authentication / authorization areas with no authoritative owner — "
       "reported honestly, never fabricated.", derived=True),
    _p("identity_governance_status", "identity_governance", "identity_governance.compose", "verification",
       "count", "card", "identity.manage", "/identity-governance",
       "Composed governance status across the read-only layers — a DERIVED count of clean vs failing "
       "governance checkers. Governance coverage, never certification.", derived=True),
    _p("access_governance_readiness", "identity_governance", "identity_governance.compose", "verification",
       "coverage", "gauge", "identity.manage", "/identity-governance",
       "DERIVED access-governance readiness — user / role / capability / authentication / authorization "
       "coverage − not_configured areas. GOVERNANCE READINESS ONLY, never an authentication result, "
       "authorization decision, or certified access review.", derived=True),
    _p("executive_identity_posture", "identity_governance", "identity_governance.compose", "verification",
       "distribution", "gauge", "analytics.executive", "/identity-governance",
       "DERIVED executive identity & access posture — users + roles + capabilities + authentication coverage + "
       "configured vs not_configured domains. Governance coverage only, never a certified access review or an "
       "authorization decision.", derived=True),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str
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


_IG_CAPS = ("identity.manage", "analytics.executive")

IDENTITY_DASHBOARDS = (
    _d("identity_overview", "identity_governance", "administration", "identity_governance.enabled",
       ("user_directory_coverage", "user_status_distribution", "mfa_coverage", "team_coverage",
        "configured_identity_domains"),
       _IG_CAPS, "/identity-governance?dashboard=identity_overview",
       ("identity", "security.rbac")),
    _d("authentication_landscape", "identity_governance", "administration", "authentication_landscape.enabled",
       ("authentication_providers", "session_management_status", "mfa_enrollment", "sso_availability",
        "mfa_enforcement_availability"),
       _IG_CAPS, "/identity-governance?dashboard=authentication_landscape",
       ("security.authentication", "identity")),
    _d("authorization_landscape", "identity_governance", "administration", "authorization_landscape.enabled",
       ("authorization_policy_coverage", "policy_engine_coverage", "record_scope_authorization_status",
        "capability_policy_status", "authorization_event_coverage"),
       _IG_CAPS, "/identity-governance?dashboard=authorization_landscape",
       ("security.rbac", "policy", "security.authorization")),
    _d("role_governance", "identity_governance", "administration", "identity_governance.enabled",
       ("role_inventory", "active_roles", "system_roles", "role_capability_governance"),
       _IG_CAPS, "/identity-governance?dashboard=role_governance",
       ("security.rbac", "identity")),
    _d("capability_coverage", "identity_governance", "administration", "identity_governance.enabled",
       ("capability_inventory", "sensitive_capability_coverage", "capability_coverage",
        "least_privilege_indicators"),
       _IG_CAPS, "/identity-governance?dashboard=capability_coverage",
       ("security.rbac", "identity")),
    _d("policy_coverage", "identity_governance", "administration", "authorization_landscape.enabled",
       ("policy_engine_coverage", "authorization_policy_coverage", "capability_policy_status",
        "identity_governance_status"),
       _IG_CAPS, "/identity-governance?dashboard=policy_coverage",
       ("policy", "security.rbac", "identity_governance")),
    _d("executive_identity_governance", "identity_governance", "executive", "identity_governance.enabled",
       ("executive_identity_posture", "access_governance_readiness", "user_directory_coverage",
        "mfa_coverage"),
       _IG_CAPS, "/identity-governance?dashboard=executive_identity_governance",
       ("identity_governance", "identity")),
    _d("identity_readiness", "identity_governance", "administration", "identity_governance.enabled",
       ("access_governance_readiness", "identity_governance_status", "unconfigured_identity_domains",
        "least_privilege_indicators"),
       _IG_CAPS, "/identity-governance?dashboard=identity_readiness",
       ("identity_governance", "security.rbac")),
)

_DASH_BY_KEY = {d.key: d for d in IDENTITY_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def domain(key) -> DomainEntry | None:
    return _CD_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def _all_entries():
    return (*IDENTITY_REGISTRY, *ROLE_REGISTRY, *CAPABILITY_REGISTRY, *AUTHENTICATION_REGISTRY,
            *AUTHORIZATION_REGISTRY)


def not_configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == NOT_CONFIGURED)


def configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == CONFIGURED)


def coverage() -> dict:
    return {
        "identity_domains": len(IDENTITY_REGISTRY),
        "role_domains": len(ROLE_REGISTRY),
        "capability_domains": len(CAPABILITY_REGISTRY),
        "authentication_domains": len(AUTHENTICATION_REGISTRY),
        "authorization_domains": len(AUTHORIZATION_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(IDENTITY_DASHBOARDS),
        "configured_domains": len(configured_domains()),
        "not_configured_domains": len(not_configured_domains()),
    }
