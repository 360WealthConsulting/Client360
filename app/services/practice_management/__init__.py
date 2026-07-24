"""Enterprise Practice Management, Capacity Planning & Resource Operations layer (Phase D.49).

A governed, READ-ONLY composition that provides firm-wide practice visibility — advisor/department
utilization, staffing signals, workload, backlog, workflow aging, seasonal forecasts, and service-level
indicators — WITHOUT introducing a second workflow engine, scheduler, staffing/assignment engine, work
queue, capacity/planning engine, metrics registry, or persistence store. It composes named practice
dashboards from declarative capacity + resource + panel registries over the platform's AUTHORITATIVE
operational owners: Operations Capacity (the capacity/utilization owner, Phase D.20), the Unified Work
Queue, Workflow Automation, Operational + Compliance Intelligence, the opportunity + Analytics
firm-intelligence layers, and the tax domain. It defines no new metrics, owns no persistence, and never
mutates, assigns, rebalances, or reschedules; every panel is explainable and deep-links to its
authoritative surface.
"""
from .service import (
    client_workload,
    compose_dashboard,
    get_panel,
    household_workload,
    list_dashboards,
    practice_summary,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "practice_summary",
    "client_workload",
    "household_workload",
]
