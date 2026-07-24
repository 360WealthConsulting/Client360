"""Enterprise Automation Orchestration & Business Process Composition layer (Phase D.51).

A governed, READ-ONLY composition that provides firm-wide visibility into automation — inventory, workflow
status, trigger activity, execution status, pending automations, and failed automations — WITHOUT introducing
a second workflow engine, scheduler, rules engine, orchestration engine, event bus, or automation platform.
It composes named automation dashboards from declarative automation + trigger + action + panel registries
over the platform's AUTHORITATIVE operational services: the Workflow Engine (`workflow_automation` + the
`workflow_orchestration` facade), the Automation scheduled-job engine (ADR-027), the Trigger engine + action
catalog, the Event outbox, Scheduling, and Communications. It defines no new metrics, owns no persistence,
and never executes an automation, launches a workflow, fires a trigger, routes an event, or mutates anything;
every panel is explainable, deep-links to its authoritative workflow/automation surface, and carries counts +
status only — never a workflow payload.
"""
from .service import (
    automation_summary,
    client_automation,
    compose_dashboard,
    get_panel,
    household_automation,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "automation_summary",
    "client_automation",
    "household_automation",
]
