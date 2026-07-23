"""Advisor Workspace widget registry (Phase D.38).

Declares the workspace widgets: identity, the section they group under, the capability that gates
them (an advisor never sees a widget they could not open — no shown-then-403), the visualization
kind, whether the widget is projection-backed (D.37), and a deep link into the owning surface (no
dead-end tiles). The dict order IS the default widget order. The actual data-compute functions live
in ``widgets.py`` (keyed by widget key) to keep this module import-light and declarative.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WidgetDef:
    key: str
    title: str
    section: str            # today | priorities | activity | opportunities | compliance | operations
    capability: str         # the capability required to see this widget
    kind: str               # "count" (a tile) | "list" (a short worklist)
    detail_href: str        # deep link into the owning surface
    projection_backed: bool # served from a D.37 projection when healthy+fresh, else authoritative
    description: str


# Order here is the DEFAULT widget order.
WIDGETS: dict[str, WidgetDef] = {
    "calendar_today": WidgetDef(
        "calendar_today", "Today's Calendar", "today", "client.read", "list",
        "/scheduling", False, "Appointments scheduled for today (book-scoped)."),
    "active_clients": WidgetDef(
        "active_clients", "Active Clients", "today", "client.read", "count",
        "/people", True, "Clients in your book (people.summary projection)."),
    "workflow_exceptions": WidgetDef(
        "workflow_exceptions", "Workflow Exceptions", "priorities", "exception.read", "count",
        "/exceptions", True, "Open workflow exceptions (exception.dashboard projection)."),
    "operational_tasks": WidgetDef(
        "operational_tasks", "Operational Tasks", "priorities", "task.read", "count",
        "/tasks", True, "Open operational tasks (operations.tasks projection)."),
    "recent_activity": WidgetDef(
        "recent_activity", "Recent Activity", "activity", "client.read", "list",
        "/activities", False, "Recent client activity across your book (scoped timeline)."),
    "revenue_pipeline": WidgetDef(
        "revenue_pipeline", "Revenue Pipeline", "opportunities", "opportunity.read", "count",
        "/opportunities", True, "Open opportunities (opportunity.pipeline projection)."),
    "compliance_queue": WidgetDef(
        "compliance_queue", "Compliance Queue", "compliance", "compliance.read", "count",
        "/compliance", True, "Open compliance reviews (compliance.queue projection)."),
    "tax_pipeline": WidgetDef(
        "tax_pipeline", "Tax Pipeline", "operations", "tax.read", "count",
        "/tax/returns", True, "Tax returns in flight (tax.pipeline projection)."),
    "insurance_pipeline": WidgetDef(
        "insurance_pipeline", "Insurance Pipeline", "operations", "insurance.read", "count",
        "/insurance", True, "Insurance cases in flight (insurance.pipeline projection)."),
    "benefits_pipeline": WidgetDef(
        "benefits_pipeline", "Benefits Pipeline", "operations", "benefits.read", "count",
        "/benefits", True, "Benefit enrollments (benefits.enrollment projection)."),
    "document_review": WidgetDef(
        "document_review", "Document Review", "operations", "document.read", "count",
        "/documents", False, "Documents awaiting review (scoped)."),
    "team_workload": WidgetDef(
        "team_workload", "Team Workload", "operations", "capacity.read", "count",
        "/work/team", False, "Team members over capacity (firm-level)."),
}

DEFAULT_ORDER = tuple(WIDGETS.keys())

SECTIONS = ("today", "priorities", "activity", "opportunities", "compliance", "operations")
SECTION_LABELS = {
    "today": "Today", "priorities": "Priorities", "activity": "Client Activity",
    "opportunities": "Opportunities", "compliance": "Compliance", "operations": "Operations",
}


def eligible_keys(principal) -> list[str]:
    """Widget keys whose capability the principal holds — in default order."""
    return [k for k, w in WIDGETS.items() if principal.can(w.capability)]
