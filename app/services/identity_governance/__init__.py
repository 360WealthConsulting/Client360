"""Enterprise Identity, Access Governance & Authorization Intelligence layer (Phase D.65).

A governed, READ-ONLY composition that provides a unified, governed view of the firm's identity and access
posture — identity inventory, role coverage, capability coverage, permission mappings, authentication coverage,
authorization coverage, policy coverage, least-privilege indicators, access-governance readiness, and identity
gaps — WITHOUT introducing a second identity provider, authentication service, authorization engine, RBAC
system, directory, SSO platform, policy engine, or user-management platform. It composes named identity
dashboards from declarative identity + role + capability + authentication + authorization registries over the
platform's AUTHORITATIVE owners: the Identity service (`list_identity_data` — the user / role / capability /
team directory), Security RBAC (role & capability resolution, authorization policies), Security Authentication
(providers), the Policy engine (policy coverage), and Security Authorization (record-scope decisions). SSO /
external IdP, MFA enforcement, service accounts, API-key identities, access reviews, PAM, segregation of duties,
identity lifecycle, and password management have no authoritative owner in the platform today — declared
registry entries with a `not_configured` status, never a fabricated user, identity, role, permission,
authentication provider, session, capability, policy assignment, or access review. It defines no new metrics,
owns no persistence, and NEVER authenticates a user, authorizes a request, assigns a role, grants or revokes a
permission, modifies a policy, creates an identity, creates a session, or manages a password; every panel is
explainable, deep-links to its authoritative owner, and carries counts / coverage / status / ratios only —
never a password, secret, token, session ID, credential, authentication payload, raw identity, privileged-role
membership, or user-level permission map. The derived posture is a GOVERNANCE-READINESS summary, never an
authentication result, an authorization decision, a granted permission, or a certified access review: **a
capability inventory is not a grant, a role definition is not an assignment, a provider registration is not an
authentication, and coverage is not certification.**
"""
from .service import (
    client_authorization_context,
    compose_dashboard,
    get_panel,
    household_authorization_context,
    identity_summary,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "identity_summary",
    "client_authorization_context",
    "household_authorization_context",
]
