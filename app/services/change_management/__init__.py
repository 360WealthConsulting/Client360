"""Enterprise Change Management, Release Governance & Configuration Intelligence layer (Phase D.63).

A governed, READ-ONLY composition that provides a unified, governed view of the firm's change posture —
change-domain inventory, release readiness, CI-evidence verification, configuration governance, migration
readiness, deployment evidence, rollback readiness, and executive change posture — WITHOUT introducing a second
ITSM / change-management / deployment / CI-CD / Git / CMDB / feature-flag / release-approval / incident /
maintenance-scheduling platform. It composes named change dashboards from declarative change-domain + release +
configuration + change-evidence registries over the platform's AUTHORITATIVE owners: the architecture manifest
(declared release line / migration head / route + capability counts), the live Alembic script head
(`observability.health._expected_head`), the live route / ADR / Client 360 section / Executive dashboard
counts, the Runtime + Policy engines, the Observability catalog / alerts / incidents / health owners, Security
incidents, Compliance Intelligence, and the CI pipeline evidence the manifest records. Live git / PR / CI
status, deployment execution / status, rollback readiness, production verification, change calendar, and
post-change review have no authoritative owner in the platform today — declared registry entries with a
`not_configured` status, never a fabricated change request, deployment status, release approval, rollback
readiness, configuration state, production verification, environment health, or change success. It defines no
new metrics, owns no persistence, and NEVER creates a branch, merges a pull request, pushes a commit, tags a
release, deploys code, runs a migration, changes a feature flag, approves a change, schedules maintenance,
acknowledges an incident, executes rollback, or certifies production; every panel is explainable, deep-links to
its authoritative owner, and carries counts / status / identifiers / hashes / timestamps / coverage /
verification only — never a credential, secret, token, environment variable, connection string, private key,
deployment payload, protected infrastructure detail, sensitive configuration value, or private incident
narrative. The derived posture is an OPERATIONAL-READINESS summary, never approval / certification / deployment
success / production safety: **a green build is not production certification, a merged pull request is not
deployment, and an absent incident is not change success.**
"""
from .service import (
    change_summary,
    client_change_impact,
    compose_dashboard,
    get_panel,
    household_change_impact,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "change_summary",
    "client_change_impact",
    "household_change_impact",
]
