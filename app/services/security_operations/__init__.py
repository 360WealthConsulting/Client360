"""Enterprise Security Operations, Identity Governance & Platform Security Intelligence layer (Phase D.54).

A governed, READ-ONLY composition that provides a single governed operational view of platform security
posture — authentication, authorization, identity governance, MFA, sessions, audit, and security posture —
WITHOUT introducing a second IAM platform, identity provider, RBAC engine, authentication system,
authorization engine, MFA provider, audit-logging platform, or SIEM. It composes named security dashboards
from declarative identity + security + panel registries over the platform's AUTHORITATIVE security owners:
the Security metadata domain (`security.service` / `providers` / `policies` / `incidents`), the Identity
owner (`identity.list_identity_data`), the RBAC foundation, the hash-chain audit log (`audit_export`), and
the MFA flag (via the Analytics Registry). It defines no new metrics, owns no persistence, and never
authenticates a user, creates a user, revokes a session, issues a token, resets a password, or alters a
permission; every panel is explainable, deep-links to its authoritative security-owner surface, and carries
counts + status only — never a password, secret, token, session ID, or authentication payload.
"""
from .service import (
    client_security,
    compose_dashboard,
    get_panel,
    household_security,
    list_dashboards,
    security_summary,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "security_summary",
    "client_security",
    "household_security",
]
