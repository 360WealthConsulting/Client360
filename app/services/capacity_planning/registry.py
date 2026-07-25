"""Enterprise Capacity Planning registries (Phase D.61) — the declarative catalogs of the workforce / capacity
/ utilization composition layer.

Five frozen, declarative catalogs; the layer owns NO persistence and defines NO second HR platform, HCM,
scheduling application, calendar system, project-management system, PSA, time-tracking platform, payroll
platform, or workforce-management system:

  * WORKFORCE_REGISTRY — workforce classes (advisors, tax / insurance professionals, operations + admin staff,
    contractors, automation workers, shared resources), each naming its authoritative owner. The resource
    inventory of record is the Operations capacity/resource owner; a full HR employee directory + contractor
    HR have no authoritative owner → declared not_configured.
  * CAPACITY_REGISTRY — capacity categories (client, meeting, review, workflow, operational, onboarding,
    tax-season, planning, service). Metadata only. Meeting / onboarding / planning capacity have no
    authoritative owner → not_configured.
  * UTILIZATION_REGISTRY — utilization categories + workload / staffing / queue / assignment indicators, each
    referencing an authoritative owner (Operations capacity, the Work Queue, Practice Management).
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * RESOURCE_DASHBOARDS — every resource dashboard.

Governance verifies every registry key is unique, every configured entry names an authoritative owner, every
panel names an authoritative owner + source + deep link, every derived value is labeled, and that this layer
never becomes a second HR / scheduling / workforce / PSA / time-tracking system. Where no authoritative owner
exists (HR employee directory, contractors, PTO / availability, time-tracking, payroll, calendar-scheduling
capacity, onboarding / planning capacity), the entry is declared `not_configured` and reported honestly — never
a fabricated staffing, utilization, availability, scheduling, or capacity figure.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"
CONFIGURED = "configured"


# --- workforce registry ------------------------------------------------------

@dataclass(frozen=True)
class WorkforceClass:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _wf(key, label, owner, deep_links, *, capabilities=("capacity.read",),
        runtime_gate="workforce.enabled", config_status=CONFIGURED):
    return WorkforceClass(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                          config_status)


WORKFORCE_REGISTRY = (
    _wf("advisors", "Advisors", "operations.capacity", ("/practice-management",)),
    _wf("tax_professionals", "Tax Professionals", "operations.capacity", ("/practice-management", "/tax")),
    _wf("insurance_professionals", "Insurance Professionals", "operations.capacity",
        ("/practice-management", "/insurance")),
    _wf("operations_staff", "Operations Staff", "operations.capacity", ("/practice-management",)),
    _wf("administrative_staff", "Administrative Staff", "operations.capacity", ("/practice-management",)),
    _wf("contractors", "Contractors", NOT_CONFIGURED, ("/capacity-planning",), config_status=NOT_CONFIGURED),
    _wf("automation_workers", "Automation Workers", "automation_orchestration", ("/automation",),
        capabilities=("automation.view",)),
    _wf("shared_resources", "Shared Resources", "operations.capacity", ("/practice-management",)),
)

_WF_BY_KEY = {w.key: w for w in WORKFORCE_REGISTRY}


# --- capacity registry -------------------------------------------------------

@dataclass(frozen=True)
class CapacityCategory:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _cap(key, label, owner, deep_links, *, capabilities=("capacity.read",),
         runtime_gate="capacity.enabled", config_status=CONFIGURED):
    return CapacityCategory(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                            config_status)


CAPACITY_REGISTRY = (
    _cap("client_capacity", "Client Capacity", "operations.capacity", ("/practice-management",)),
    _cap("meeting_capacity", "Meeting Capacity", NOT_CONFIGURED, ("/capacity-planning",),
         config_status=NOT_CONFIGURED),
    _cap("review_capacity", "Review Capacity", "work_queue", ("/work",), capabilities=("work.read",)),
    _cap("workflow_capacity", "Workflow Capacity", "workflow_automation", ("/automation",),
         capabilities=("automation.view",)),
    _cap("operational_capacity", "Operational Capacity", "operations.capacity", ("/practice-management",)),
    _cap("onboarding_capacity", "Onboarding Capacity", NOT_CONFIGURED, ("/capacity-planning",),
         config_status=NOT_CONFIGURED),
    _cap("tax_season_capacity", "Tax-Season Capacity", "tax_domain", ("/tax", "/practice-management")),
    _cap("planning_capacity", "Planning Capacity", NOT_CONFIGURED, ("/capacity-planning",),
         config_status=NOT_CONFIGURED),
    _cap("service_capacity", "Service Capacity", "operations.capacity", ("/practice-management", "/work")),
)

_CAP_BY_KEY = {c.key: c for c in CAPACITY_REGISTRY}


# --- utilization registry ----------------------------------------------------

@dataclass(frozen=True)
class UtilizationCategory:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _util(key, label, owner, deep_links, *, capabilities=("capacity.read",),
          runtime_gate="resource_intelligence.enabled", config_status=CONFIGURED):
    return UtilizationCategory(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                               config_status)


UTILIZATION_REGISTRY = (
    _util("utilization_categories", "Utilization Categories", "operations.capacity",
          ("/practice-management",)),
    _util("workload_indicators", "Workload Indicators", "work_queue", ("/work",),
          capabilities=("work.read",)),
    _util("staffing_indicators", "Staffing Indicators", "practice_management", ("/practice-management",)),
    _util("queue_health", "Queue Health", "work_queue", ("/work",), capabilities=("work.read",)),
    _util("assignment_health", "Assignment Health", "work_queue", ("/work",), capabilities=("work.read",)),
)

_UTIL_BY_KEY = {u.key: u for u in UTILIZATION_REGISTRY}


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str
    source: str
    measure: str
    unit: str
    viz: str
    permission: str
    deep_link: str
    explainability: str
    derived: bool = False
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       derived=False, refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    derived, refresh, lifecycle)


PANEL_REGISTRY = (
    # capacity / utilization (Operations capacity owner)
    _p("firm_capacity_utilization", "operations.capacity", "operations.capacity.capacity_overview",
       "utilization", "percent", "gauge", "capacity.read", "/practice-management",
       "Firm capacity utilization across active resources, from the Operations capacity owner. No second "
       "workforce-management system. Counts + percent only — no employee details."),
    _p("over_capacity_resources", "operations.capacity", "operations.capacity.capacity_overview", "capacity",
       "count", "card", "capacity.read", "/practice-management",
       "Resources currently over capacity, from the Operations capacity owner. Counts only."),
    _p("department_capacity", "operations.capacity", "operations.capacity.capacity_overview", "capacity",
       "count", "chart", "capacity.read", "/practice-management",
       "Resource count + over-capacity by department, from the Operations capacity owner."),
    _p("capacity_horizon", "operations.capacity", "operations.capacity.list_capacity_plans", "forecast",
       "count", "card", "capacity.read", "/practice-management",
       "Declared capacity plans (planning horizon), from the Operations capacity owner. Declarative plans "
       "only — the layer never schedules resources."),
    _p("utilization_summary", "operations.capacity", "operations.capacity.capacity_overview", "utilization",
       "percent", "card", "capacity.read", "/practice-management",
       "Operational utilization summary (resource count + over-capacity), from the Operations capacity owner."),
    # staffing (Practice Management composition)
    _p("staffing_recommendations", "practice_management", "practice_management.practice_summary", "staffing",
       "count", "card", "capacity.read", "/practice-management",
       "Advisory staffing recommendations (over-capacity signals), from the D.49 Practice Management layer. "
       "Advisory only — the layer never assigns staff."),
    _p("staffing_readiness", "capacity_planning", "capacity_planning.registry", "staffing", "coverage",
       "gauge", "capacity.read", "/capacity-planning?dashboard=staffing_readiness",
       "Staffing readiness — workforce classes with an authoritative owner vs not_configured (HR directory, "
       "contractors). A DERIVED coverage summary, never a certified staffing figure.", derived=True),
    _p("staffing_gaps", "capacity_planning", "capacity_planning.compose", "staffing", "list", "list",
       "capacity.read", "/capacity-planning?dashboard=staffing_readiness",
       "Staffing gaps — not_configured workforce / capacity domains (HR directory, contractors, meeting / "
       "onboarding / planning capacity, PTO / availability). A DERIVED honesty summary, never fabricated.",
       derived=True),
    _p("availability_summary", "not_configured", "capacity_planning.registry", "staffing", "status", "card",
       "capacity.read", "/capacity-planning",
       "Availability / PTO summary — NO authoritative PTO / availability owner exists in the platform today; "
       "reported not_configured, never a fabricated availability figure.", derived=True),
    # workload / queue (Work Queue)
    _p("advisor_workload_distribution", "work_queue", "work_queue.summary", "workload", "count", "chart",
       "work.read", "/work",
       "Advisor workload distribution, from the Work Queue. No second PSA / project-management system."),
    _p("workload_by_domain", "work_queue", "work_queue.summary", "workload", "count", "chart", "work.read",
       "/work",
       "Open workload by domain, from the Work Queue."),
    _p("open_backlog", "work_queue", "work_queue.summary", "queue", "count", "card", "work.read", "/work",
       "Open work backlog, from the Work Queue. No second work engine."),
    _p("unassigned_backlog", "work_queue", "work_queue.summary", "queue", "count", "card", "work.read",
       "/work",
       "Unassigned team backlog, from the Work Queue. The layer never assigns work."),
    _p("sla_backlog", "work_queue", "work_queue.summary", "queue", "count", "card", "work.read", "/work",
       "SLA-breached backlog, from the Work Queue."),
    _p("queue_health", "work_queue", "work_queue.summary", "queue", "count", "card", "work.read", "/work",
       "Queue health (overdue + SLA breaches + unassigned), from the Work Queue."),
    _p("assignment_distribution", "work_queue", "work_queue.summary", "assignment", "distribution", "chart",
       "work.read", "/work",
       "Assignment distribution (by owner team / domain), from the Work Queue. The layer never modifies an "
       "assignment."),
    # automation workload
    _p("automation_workload", "automation_orchestration", "automation_orchestration.automation_summary",
       "workload", "count", "card", "automation.view", "/automation",
       "Automation-worker workload (workflow status + failed runs), from the D.51 Automation Orchestration "
       "layer. No second scheduler."),
    _p("workflow_escalations", "automation_orchestration", "automation_orchestration.automation_summary",
       "workload", "count", "card", "automation.view", "/automation",
       "Workflow escalations + pending approvals, from the D.51 Automation Orchestration layer."),
    # forecast
    _p("capacity_forecast", "capacity_planning", "capacity_planning.compose", "forecast", "coverage", "gauge",
       "capacity.read", "/capacity-planning?dashboard=capacity_planning",
       "Capacity forecast coverage — capacity categories with an authoritative owner vs not_configured "
       "(meeting / onboarding / planning). A DERIVED coverage summary, never a fabricated forecast.",
       derived=True),
    # registry-derived (DERIVED, catalog)
    _p("workforce_inventory", "capacity_planning", "capacity_planning.registry", "workforce", "count", "list",
       "capacity.read", "/capacity-planning",
       "The registered workforce-class catalog — each naming its authoritative owner + config status. "
       "Metadata only — never an employee directory.", derived=True),
    _p("registered_workforce", "capacity_planning", "capacity_planning.registry", "workforce", "count", "list",
       "capacity.read", "/capacity-planning",
       "Workforce classes with an authoritative owner vs not_configured (HR directory + contractors). "
       "Metadata only.", derived=True),
    _p("registered_capacity", "capacity_planning", "capacity_planning.registry", "capacity", "count", "list",
       "capacity.read", "/capacity-planning?dashboard=capacity_planning",
       "The registered capacity-category catalog — each naming its authoritative owner + config status.",
       derived=True),
    _p("executive_workforce_status", "capacity_planning", "capacity_planning.compose", "utilization",
       "distribution", "gauge", "analytics.executive", "/capacity-planning",
       "DERIVED executive workforce posture — configured vs not_configured domains + firm utilization + "
       "queue-health counts across the authoritative owners. An operational summary only, never a certified "
       "staffing / utilization figure and never an HR record.", derived=True),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str
    runtime_gate: str
    panels: tuple
    required_capabilities: tuple
    navigation: str
    refresh_policy: str
    governing_services: tuple
    lifecycle: str = "active"


def _d(key, owner, audience, gate, panels, caps, navigation, governing, *, refresh="on_view",
       lifecycle="active"):
    return DashboardDef(key, owner, audience, gate, tuple(panels), tuple(caps), navigation, refresh,
                        tuple(governing), lifecycle)


_CP_CAPS = ("capacity.read", "analytics.executive")

RESOURCE_DASHBOARDS = (
    _d("workforce_overview", "capacity_planning", "operations", "workforce.enabled",
       ("workforce_inventory", "registered_workforce", "staffing_readiness"),
       _CP_CAPS, "/capacity-planning?dashboard=workforce_overview",
       ("operations.capacity", "capacity_planning")),
    _d("capacity_planning", "capacity_planning", "operations", "capacity.enabled",
       ("firm_capacity_utilization", "capacity_horizon", "capacity_forecast", "registered_capacity"),
       _CP_CAPS, "/capacity-planning?dashboard=capacity_planning",
       ("operations.capacity", "capacity_planning")),
    _d("advisor_utilization", "capacity_planning", "operations", "resource_intelligence.enabled",
       ("advisor_workload_distribution", "workload_by_domain", "over_capacity_resources"),
       _CP_CAPS, "/capacity-planning?dashboard=advisor_utilization",
       ("work_queue", "operations.capacity")),
    _d("operations_utilization", "capacity_planning", "operations", "resource_intelligence.enabled",
       ("utilization_summary", "department_capacity", "automation_workload"),
       _CP_CAPS, "/capacity-planning?dashboard=operations_utilization",
       ("operations.capacity", "automation_orchestration")),
    _d("queue_health", "capacity_planning", "operations", "resource_intelligence.enabled",
       ("queue_health", "open_backlog", "sla_backlog"),
       _CP_CAPS, "/capacity-planning?dashboard=queue_health",
       ("work_queue",)),
    _d("staffing_readiness", "capacity_planning", "operations", "workforce.enabled",
       ("staffing_readiness", "staffing_recommendations", "staffing_gaps", "availability_summary"),
       _CP_CAPS, "/capacity-planning?dashboard=staffing_readiness",
       ("practice_management", "capacity_planning")),
    _d("resource_allocation", "capacity_planning", "operations", "resource_intelligence.enabled",
       ("assignment_distribution", "unassigned_backlog", "workflow_escalations"),
       _CP_CAPS, "/capacity-planning?dashboard=resource_allocation",
       ("work_queue", "automation_orchestration")),
    _d("executive_workforce_status", "capacity_planning", "executive", "workforce.enabled",
       ("executive_workforce_status", "firm_capacity_utilization", "staffing_readiness"),
       _CP_CAPS, "/capacity-planning?dashboard=executive_workforce_status",
       ("operations.capacity", "work_queue")),
)

_DASH_BY_KEY = {d.key: d for d in RESOURCE_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def workforce_class(key) -> WorkforceClass | None:
    return _WF_BY_KEY.get(key)


def capacity_category(key) -> CapacityCategory | None:
    return _CAP_BY_KEY.get(key)


def utilization_category(key) -> UtilizationCategory | None:
    return _UTIL_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def _all_entries():
    return (*WORKFORCE_REGISTRY, *CAPACITY_REGISTRY, *UTILIZATION_REGISTRY)


def not_configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == NOT_CONFIGURED)


def configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == CONFIGURED)


def coverage() -> dict:
    return {
        "workforce_classes": len(WORKFORCE_REGISTRY),
        "capacity_categories": len(CAPACITY_REGISTRY),
        "utilization_categories": len(UTILIZATION_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(RESOURCE_DASHBOARDS),
        "configured_domains": len(configured_domains()),
        "not_configured_domains": len(not_configured_domains()),
    }
