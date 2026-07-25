"""Enterprise Environment Management, Deployment Topology & Platform Lifecycle Intelligence layer (Phase D.64).

A governed, READ-ONLY composition that provides a unified, governed view of the firm's environment and platform
landscape — environment inventory, deployment topology, runtime topology, platform ownership, lifecycle state,
infrastructure dependencies, runtime coverage, topology health, lifecycle readiness, retirement readiness,
environment gaps, and dependency visibility — WITHOUT introducing a second CMDB, infrastructure-management
platform, cloud-management platform, deployment orchestrator, asset inventory, configuration database,
environment manager, or monitoring platform. It composes named environment dashboards from declarative
environment + platform + deployment-topology + lifecycle + infrastructure-dependency registries over the
platform's AUTHORITATIVE owners: the Observability catalog (environment profiles, deployment references,
service inventory, the service dependency graph), the Observability health owner (runtime snapshots, the live
migration head), the Observability service overview, the Runtime + Policy engines, and the Integration
platform. Cloud resources, servers, containers, VMs, formal lifecycle state, retirement records, decommission
schedule, host / network topology, and live deployment execution have no authoritative owner in the platform
today — declared registry entries with a `not_configured` status, never a fabricated environment, deployment,
infrastructure, topology, lifecycle state, environment health, platform ownership, or retirement status. It
defines no new metrics, owns no persistence, and NEVER creates an environment, deploys code, provisions
infrastructure, modifies topology, changes lifecycle state, executes a cloud operation, writes configuration,
or deletes an environment; every panel is explainable, deep-links to its authoritative owner, and carries
counts / status / identifiers / coverage / verification only — never a credential, secret, token, environment
variable, connection string, private key, deployment payload, protected infrastructure detail, private
topology, or sensitive configuration value. The derived posture is an OPERATIONAL-VISIBILITY summary, never a
certified environment health, deployment status, provisioning outcome, or retirement decision: **environment
metadata is not live infrastructure, a deployment reference is not a deployment, and an active flag is not a
lifecycle guarantee.**
"""
from .service import (
    client_platform_dependencies,
    compose_dashboard,
    environment_summary,
    get_panel,
    household_platform_dependencies,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "environment_summary",
    "client_platform_dependencies",
    "household_platform_dependencies",
]
