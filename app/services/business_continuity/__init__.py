"""Enterprise Business Continuity, Disaster Recovery & Operational Resilience layer (Phase D.55).

A governed, READ-ONLY composition that provides a unified operational view of platform resilience — backup
status, recovery readiness, restore validation, infrastructure health, runtime resilience, maintenance,
notifications, and operational readiness — WITHOUT introducing a second backup platform, monitoring system,
disaster-recovery engine, scheduler, notification system, or incident manager. It composes named continuity
dashboards from declarative resilience + recovery + panel registries over the platform's AUTHORITATIVE
operational-resilience owners: the Observability domain (`service` / `catalog` / `health` / `incidents` /
`alerts`), the Runtime engine (`runtime.service` / `coordination` / `consumption`), the Automation scheduler,
and Communications. Backup / restore / DR have no authoritative owner in the platform today — those panels
report ``not_configured`` honestly, never a fabricated status. It defines no new metrics, owns no
persistence, and never starts a backup, restores data, acknowledges an incident, changes monitoring, alters
runtime, or modifies infrastructure; every panel is explainable, deep-links to its authoritative
resilience-owner surface, and carries counts + status only — never an infrastructure payload.
"""
from .service import (
    client_continuity,
    compose_dashboard,
    continuity_summary,
    get_panel,
    household_continuity,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "continuity_summary",
    "client_continuity",
    "household_continuity",
]
