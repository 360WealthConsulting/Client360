"""Enterprise Capacity Planning, Workforce Operations & Resource Intelligence layer (Phase D.61).

A governed, READ-ONLY composition that provides a unified, governed view of firm workforce operations,
capacity, and utilization — staffing summaries, workload summaries, queue health, utilization summaries,
capacity forecasts, assignment distribution, operational / advisor / automation workload, staffing gaps, and
availability summaries — WITHOUT introducing a second HR platform, HCM, scheduling application, calendar
system, project-management system, PSA, time-tracking platform, payroll platform, or workforce-management
system. It composes named resource dashboards from declarative workforce + capacity + utilization registries
over the platform's AUTHORITATIVE owners: the Operations capacity owner (firm utilization / resources /
capacity plans), the Work Queue (workload / backlog / queue health / assignments), Practice Management
(staffing recommendations), and Automation Orchestration (automation-worker workload). A full HR employee
directory, contractors, PTO / availability, time-tracking, payroll, calendar-scheduling capacity, and
onboarding / planning capacity have no authoritative owner in the platform today — declared registry entries
with a `not_configured` status, never a fabricated staffing / utilization / availability figure. It defines no
new metrics, owns no persistence, and never assigns work, approves PTO, creates meetings, moves calendar
events, schedules resources, or modifies assignments; every panel is explainable, deep-links to its
authoritative owner, and carries counts / status / coverage only — never an employee detail, payroll, HR
record, calendar content, or time entry. The derived executive posture is an operational summary, never a
certified staffing / utilization figure and never an HR record.
"""
from .service import (
    capacity_summary,
    client_staffing,
    compose_dashboard,
    get_panel,
    household_staffing,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "capacity_summary",
    "client_staffing",
    "household_staffing",
]
