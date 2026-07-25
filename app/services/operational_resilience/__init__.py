"""Enterprise Operational Resilience, Incident Management & Service Continuity Intelligence layer
(Phase D.60).

A governed, READ-ONLY composition that provides a unified, governed view of firm operational resilience —
operational service health, incident inventory, alerts, maintenance windows, continuity coverage, recovery
readiness, dependency health, and vendor operational status — WITHOUT introducing a second incident-management
platform, ticketing system, monitoring platform, help desk, disaster-recovery platform, change-management
platform, CMDB, scheduler, or alerting engine. It composes named resilience dashboards from declarative
operational-service + incident-category + continuity-capability + recovery-objective + operational-dependency
registries over the platform's AUTHORITATIVE owners: the Observability service catalog / health / incidents /
alerts, Security incidents, the Integration Platform, Vendor Management, Automation Orchestration, and Business
Continuity. Backup, restore, disaster recovery, recovery testing, failover, outage-history/uptime, and vendor
incidents have no authoritative owner in the platform today — declared registry entries with a `not_configured`
status, never a fabricated operational status. It defines no new metrics, owns no persistence, and never
creates an incident, acknowledges an alert, executes recovery, modifies monitoring, schedules maintenance, or
closes an incident; every panel is explainable, deep-links to its authoritative owner, and carries counts /
status / coverage only — never a sensitive operational payload. The derived executive posture describes
operational posture, never a certification that production is healthy or continuity assured, and never infers
recovery success.
"""
from .service import (
    client_operational_impact,
    compose_dashboard,
    get_panel,
    household_operational_impact,
    list_dashboards,
    resilience_summary,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "resilience_summary",
    "client_operational_impact",
    "household_operational_impact",
]
