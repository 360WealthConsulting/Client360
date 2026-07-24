"""Practice Management registries (Phase D.49) — the declarative catalogs of the practice-management layer.

Four frozen, declarative catalogs; the layer owns NO persistence and defines NO new metrics or engines:

  * CAPACITY_REGISTRY — every capacity model (advisor capacity, tax prep, insurance servicing, investment
    ops, compliance reviewers, admin staff, onboarding, client service, seasonal workload). Each names its
    OWNER (the authoritative service that owns the underlying numbers), governing workflow, workload source,
    utilization method, planning horizon, runtime gate, refresh policy, and deep links. The layer computes
    NOTHING new — it composes these owners.
  * RESOURCE_REGISTRY — every resource type (advisors, tax preparers, reviewers, operations, compliance,
    admin staff). Each names its capabilities and the authoritative workload / assignment / scheduling /
    utilization / availability sources it is measured against.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * PRACTICE_DASHBOARDS — every practice dashboard (owner, audience, runtime gate, panel list, required
    capabilities, navigation, refresh, governing services).

Governance verifies every capacity model + resource is registered, every panel names an authoritative owner
+ source + deep link, and that this layer never becomes a second workflow / scheduling / staffing / queue /
planning engine.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


# --- capacity registry -------------------------------------------------------

@dataclass(frozen=True)
class CapacityModel:
    key: str
    label: str
    owner: str                 # authoritative service that owns the underlying capacity/workload numbers
    governing_workflow: str    # the workflow/process that generates the workload
    workload_source: str       # the authoritative read the committed workload is composed from
    utilization_method: str    # how utilization is derived (always deterministic; no optimization/AI)
    planning_horizon: str      # daily | weekly | monthly | seasonal | annual
    runtime_gate: str
    refresh_policy: str
    deep_links: tuple          # authoritative surfaces to drill into


def _cap(key, label, owner, governing_workflow, workload_source, utilization_method, planning_horizon,
         deep_links, *, runtime_gate="capacity.enabled", refresh_policy="on_view"):
    return CapacityModel(key, label, owner, governing_workflow, workload_source, utilization_method,
                         planning_horizon, runtime_gate, refresh_policy, tuple(deep_links))


CAPACITY_REGISTRY = (
    _cap("advisor_capacity", "Advisor Capacity", "operations.capacity", "advisor_work",
         "work_queue.compose_queue", "committed_minutes / declared_capacity (operations.capacity)", "weekly",
         ("/operations/capacity", "/work")),
    _cap("tax_preparation", "Tax Preparation Capacity", "operations.capacity", "tax_engagement",
         "tax_domain.dashboard", "returns_due vs preparer capacity (deterministic count)", "seasonal",
         ("/operations/capacity", "/tax")),
    _cap("insurance_servicing", "Insurance Servicing Capacity", "operations.capacity", "insurance_case",
         "work_queue.compose_queue", "open servicing work vs declared capacity", "weekly",
         ("/operations/capacity", "/work?domain=insurance")),
    _cap("investment_operations", "Investment Operations Capacity", "operations.capacity",
         "operational_task", "operations.capacity.capacity_overview",
         "committed_minutes / declared_capacity (operations.capacity)", "weekly",
         ("/operations/capacity", "/operations")),
    _cap("compliance_reviewers", "Compliance Reviewer Capacity", "operations.capacity",
         "compliance_review", "compliance_intelligence.supervisory_dashboard",
         "open reviews vs reviewer capacity (deterministic count)", "weekly",
         ("/operations/capacity", "/compliance/reviews")),
    _cap("administrative_staff", "Administrative Staff Capacity", "operations.capacity",
         "operational_task", "operations.capacity.capacity_overview",
         "committed_minutes / declared_capacity (operations.capacity)", "weekly",
         ("/operations/capacity", "/operations")),
    _cap("onboarding", "Onboarding Capacity", "operations.capacity", "client_onboarding",
         "workflow_automation.workflow_metrics", "open onboarding workflows vs capacity (deterministic)",
         "monthly", ("/operations/capacity", "/workflows")),
    _cap("client_service", "Client Service Capacity", "operations.capacity", "advisor_work",
         "work_queue.compose_queue", "open client-service work vs declared capacity", "weekly",
         ("/operations/capacity", "/work")),
    _cap("seasonal_workload", "Seasonal Workload", "operations.capacity", "tax_engagement",
         "tax_domain.dashboard", "returns due within the planning horizon (deterministic count)", "seasonal",
         ("/operations/capacity", "/tax")),
)

_CAP_BY_KEY = {c.key: c for c in CAPACITY_REGISTRY}


# --- resource registry -------------------------------------------------------

@dataclass(frozen=True)
class ResourceModel:
    key: str
    label: str
    owner: str                 # authoritative service that owns the resource identity/roster
    capabilities: tuple        # capabilities that identify this resource class
    workload_source: str       # authoritative read for this resource's committed workload
    assignment_source: str     # authoritative owner of assignments (never re-implemented here)
    scheduling_source: str     # authoritative owner of scheduling (never re-implemented here)
    utilization_source: str    # authoritative owner of utilization math
    availability_source: str   # authoritative owner of availability
    runtime_gate: str = "capacity.enabled"


def _res(key, label, owner, capabilities, workload_source, assignment_source, scheduling_source,
         utilization_source, availability_source):
    return ResourceModel(key, label, owner, tuple(capabilities), workload_source, assignment_source,
                         scheduling_source, utilization_source, availability_source)


RESOURCE_REGISTRY = (
    _res("advisors", "Advisors", "identity", ("advisor_work.read", "client.read"),
         "work_queue.compose_queue", "work_management.assign_work", "scheduling.availability",
         "operations.capacity.resource_utilization", "scheduling.availability"),
    _res("tax_preparers", "Tax Preparers", "identity", ("tax.read",),
         "tax_domain.dashboard", "record_assignments (tax_return)", "scheduling.availability",
         "operations.capacity.resource_utilization", "scheduling.availability"),
    _res("reviewers", "Compliance Reviewers", "identity", ("compliance.supervise",),
         "compliance_intelligence.supervisory_dashboard", "compliance.reviews", "scheduling.availability",
         "operations.capacity.resource_utilization", "scheduling.availability"),
    _res("operations", "Operations Staff", "identity", ("operations.view",),
         "operations.capacity.capacity_overview", "operations.tasks", "scheduling.availability",
         "operations.capacity.resource_utilization", "scheduling.availability"),
    _res("compliance", "Compliance Staff", "identity", ("compliance.review.read",),
         "work_queue.compose_queue", "compliance.reviews", "scheduling.availability",
         "operations.capacity.resource_utilization", "scheduling.availability"),
    _res("administrative_staff", "Administrative Staff", "identity", ("work.read",),
         "work_queue.compose_queue", "work_management.assign_work", "scheduling.availability",
         "operations.capacity.resource_utilization", "scheduling.availability"),
)

_RES_BY_KEY = {r.key: r for r in RESOURCE_REGISTRY}


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str                 # authoritative owning service
    source: str                # the authoritative read the value is composed from
    measure: str               # utilization | workload | backlog | aging | staffing | forecast | sla
    unit: str
    viz: str
    permission: str            # capability required to see the panel value (else restricted)
    deep_link: str             # the authoritative surface to drill into
    explainability: str        # what the panel shows + where it comes from
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    refresh, lifecycle)


PANEL_REGISTRY = (
    _p("firm_capacity_utilization", "operations.capacity", "operations.capacity.capacity_overview",
       "utilization", "percent", "gauge", "capacity.read", "/operations/capacity",
       "Firm resource utilization (committed vs declared capacity), from Operations Capacity — the "
       "authoritative capacity owner. No second capacity engine."),
    _p("department_capacity", "operations.capacity", "operations.capacity.capacity_overview",
       "utilization", "percent", "chart", "capacity.read", "/operations/capacity",
       "Per-department utilization, grouped from the Operations Capacity overview."),
    _p("over_capacity_resources", "operations.capacity", "operations.capacity.capacity_overview",
       "staffing", "count", "list", "capacity.read", "/operations/capacity",
       "Resources over their declared capacity, from Operations Capacity (deterministic)."),
    _p("capacity_horizon", "operations.capacity", "operations.capacity.list_capacity_plans",
       "forecast", "count", "card", "capacity.read", "/operations/capacity",
       "Registered capacity plans by planning horizon, from Operations Capacity."),
    _p("staffing_recommendations", "practice_management", "practice_management.compose",
       "staffing", "count", "list", "capacity.read", "/operations/capacity",
       "Deterministic, read-only staffing signals composed from over-capacity resources, advisor overload, "
       "and unassigned backlog — advisory only; never assigns, rebalances, or modifies staffing."),
    _p("advisor_workload_distribution", "work_queue", "work_queue.summary", "workload", "count", "chart",
       "work.read", "/work",
       "Open work distribution by domain + my overdue, from the Unified Work Queue (book-scoped)."),
    _p("advisor_open_pipeline", "opportunity", "opportunity.intelligence.pipeline_intelligence", "workload",
       "count", "leaderboard", "analytics.view", "/opportunities",
       "Open opportunities per advisor + capacity warnings, from pipeline intelligence (book-scoped)."),
    _p("advisor_overload", "analytics", "analytics.intelligence.firm_intelligence", "staffing", "count",
       "list", "analytics.view", "/analytics",
       "Advisor-overload observations, from the Analytics firm-intelligence layer (deterministic)."),
    _p("workload_by_domain", "work_queue", "work_queue.summary", "workload", "count", "chart", "work.read",
       "/work", "Total visible work distribution by domain, from the Unified Work Queue (book-scoped)."),
    _p("tax_workload", "tax_domain", "tax_domain.dashboard", "workload", "count", "chart", "work.read",
       "/tax", "Tax engagement workload + returns due, from the tax domain (book-scoped)."),
    _p("open_backlog", "work_queue", "work_queue.compose_queue", "backlog", "count", "card", "work.read",
       "/work", "Open + overdue work backlog, from the Unified Work Queue (book-scoped)."),
    _p("unassigned_backlog", "work_queue", "work_queue.summary", "backlog", "count", "card", "work.read",
       "/work?assignee=unassigned", "Unassigned team work awaiting assignment, from the Unified Work Queue."),
    _p("sla_backlog", "work_queue", "work_queue.summary", "sla", "count", "card", "work.read",
       "/work?sla=breached", "SLA breaches + high-priority work, from the Unified Work Queue (book-scoped)."),
    _p("workflow_open_escalations", "workflow_automation", "workflow_automation.workflow_metrics", "aging",
       "count", "card", "work.read", "/workflows", "Open workflow escalations (firm-level)."),
    _p("workflow_pending_approvals", "workflow_automation", "workflow_automation.workflow_metrics", "aging",
       "count", "card", "work.read", "/workflows", "Pending workflow approvals (firm-level)."),
    _p("workflow_by_status", "workflow_automation", "workflow_automation.workflow_metrics", "aging", "count",
       "chart", "work.read", "/workflows", "Workflow instances by status (firm-level)."),
    _p("seasonal_tax_forecast", "tax_domain", "tax_domain.dashboard", "forecast", "count", "card",
       "work.read", "/tax", "Tax returns due within the planning horizon + overdue, from the tax domain."),
    _p("sla_performance", "work_queue", "work_queue.summary", "sla", "percent", "gauge", "work.read", "/work",
       "Service-level performance (breaches vs total visible work), from the Unified Work Queue."),
    _p("workflow_escalation_rate", "workflow_automation", "workflow_automation.workflow_metrics", "sla",
       "count", "card", "work.read", "/workflows",
       "Workflow escalation + approval load as a service-level indicator (firm-level)."),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # practice | operations | advisor | executive | compliance
    runtime_gate: str
    panels: tuple              # tuple of panel keys
    required_capabilities: tuple
    navigation: str
    refresh_policy: str
    governing_services: tuple
    lifecycle: str = "active"


def _d(key, owner, audience, gate, panels, caps, navigation, governing, *, refresh="on_view",
       lifecycle="active"):
    return DashboardDef(key, owner, audience, gate, tuple(panels), tuple(caps), navigation, refresh,
                        tuple(governing), lifecycle)


PRACTICE_DASHBOARDS = (
    _d("advisor_utilization", "practice_management", "practice", "capacity.enabled",
       ("firm_capacity_utilization", "advisor_workload_distribution", "advisor_open_pipeline"),
       ("capacity.read",), "/practice?dashboard=advisor_utilization",
       ("operations.capacity", "work_queue", "opportunity")),
    _d("department_utilization", "practice_management", "practice", "capacity.enabled",
       ("department_capacity", "over_capacity_resources"),
       ("capacity.read",), "/practice?dashboard=department_utilization", ("operations.capacity",)),
    _d("staffing", "practice_management", "practice", "staffing.enabled",
       ("over_capacity_resources", "advisor_overload", "staffing_recommendations"),
       ("capacity.read",), "/practice?dashboard=staffing",
       ("operations.capacity", "analytics", "work_queue")),
    _d("workload", "practice_management", "operations", "practice_management.enabled",
       ("workload_by_domain", "tax_workload", "open_backlog"),
       ("capacity.read",), "/practice?dashboard=workload", ("work_queue", "tax_domain")),
    _d("backlog", "practice_management", "operations", "practice_management.enabled",
       ("open_backlog", "unassigned_backlog", "sla_backlog"),
       ("capacity.read",), "/practice?dashboard=backlog", ("work_queue",)),
    _d("workflow_aging", "practice_management", "operations", "practice_management.enabled",
       ("workflow_open_escalations", "workflow_pending_approvals", "workflow_by_status"),
       ("capacity.read",), "/practice?dashboard=workflow_aging", ("workflow_automation",)),
    _d("seasonal_forecast", "practice_management", "practice", "capacity.enabled",
       ("seasonal_tax_forecast", "capacity_horizon"),
       ("capacity.read",), "/practice?dashboard=seasonal_forecast", ("tax_domain", "operations.capacity")),
    _d("service_level", "practice_management", "operations", "practice_management.enabled",
       ("sla_performance", "workflow_escalation_rate"),
       ("capacity.read",), "/practice?dashboard=service_level", ("work_queue", "workflow_automation")),
)

_DASH_BY_KEY = {d.key: d for d in PRACTICE_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def capacity_model(key) -> CapacityModel | None:
    return _CAP_BY_KEY.get(key)


def resource_model(key) -> ResourceModel | None:
    return _RES_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def capacity_registered(key) -> bool:
    return key in _CAP_BY_KEY


def resource_registered(key) -> bool:
    return key in _RES_BY_KEY


def coverage() -> dict:
    return {
        "capacity_models": len(CAPACITY_REGISTRY),
        "resources": len(RESOURCE_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(PRACTICE_DASHBOARDS),
    }
