"""Executive Reporting registries (Phase D.48) — the two declarative catalogs of the executive-intelligence
layer:

  * DASHBOARD_REGISTRY — every executive dashboard (owner, audience, runtime gate, widget list, required
    capabilities, navigation, refresh policy, governing services).
  * WIDGET_REGISTRY — every widget (owner, source, aggregation, refresh, permissions, deep link,
    explainability).

The layer is a COMPOSITION over the platform's authoritative operational services + the SINGLE Analytics
Registry (``analytics.metrics`` — the one metrics registry). It defines NO new metrics and NO persistence.
Governance verifies every dashboard + widget is registered and that every widget names an authoritative
owner + source.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


# --- widget registry ---------------------------------------------------------

@dataclass(frozen=True)
class WidgetDef:
    key: str
    owner: str                 # authoritative owning service
    source: str                # the authoritative read the value is composed from
    aggregation: str           # count | sum | rollup | trend | health | distribution
    unit: str
    viz: str
    permission: str            # capability required (analytics.executive for firm revenue/AUM)
    deep_link: str             # the authoritative surface to drill into
    explainability: str        # what the widget shows + where it comes from
    refresh: str = "on_view"
    lifecycle: str = "active"


def _w(key, owner, source, aggregation, unit, viz, permission, deep_link, explainability,
       *, refresh="on_view", lifecycle="active"):
    return WidgetDef(key, owner, source, aggregation, unit, viz, permission, deep_link, explainability,
                     refresh, lifecycle)


WIDGET_REGISTRY = (
    # Firm revenue / AUM KPIs — executive-gated (values inherit the analytics.executive gate via
    # compute_metric, returning restricted for non-executives).
    _w("firm_aum", "portfolio", "analytics.metrics:aum", "sum", "currency", "card", "analytics.executive",
       "/analytics", "Firm assets under management, from the Analytics Registry executive metric."),
    _w("aum_trend", "analytics", "analytics.trends:aum", "trend", "currency", "trendline",
       "analytics.executive", "/analytics", "AUM trend over recent snapshot periods."),
    _w("revenue_kpi", "bizdev", "analytics.metrics:total_bd_revenue", "sum", "currency", "card",
       "analytics.executive", "/analytics", "Total business-development revenue, from the Analytics Registry."),
    _w("client_growth", "people", "analytics.metrics:client_count", "count", "count", "card",
       "analytics.view", "/analytics", "Active client count, from the Analytics Registry."),
    # Operational widgets — broadly visible (analytics.view), book-scoped.
    _w("advisor_workload", "work_queue", "work_queue.summary", "distribution", "count", "chart",
       "analytics.view", "/work", "Open work distribution by domain, from the Unified Work Queue."),
    _w("workflow_status", "workflow_automation", "workflow_automation.workflow_metrics", "rollup", "count",
       "chart", "analytics.view", "/workflows", "Workflow instances by status (firm-level)."),
    _w("workflow_aging", "workflow_automation", "workflow_automation.workflow_metrics", "count", "count",
       "card", "analytics.view", "/workflows", "Open escalations + pending approvals (firm-level)."),
    _w("compliance_workload", "compliance", "analytics.metrics:open_compliance_reviews", "count", "count",
       "card", "analytics.view", "/compliance/reviews",
       "Open compliance reviews, from the Analytics Registry (no supervisory detail exposed)."),
    _w("review_cadence", "portfolio", "portfolio.accounts_due_for_review", "count", "count", "card",
       "analytics.view", "/portfolio", "Accounts overdue for their periodic review (book-scoped)."),
    _w("opportunity_pipeline", "opportunity", "opportunity.reporting:pipeline_report", "sum", "currency",
       "card", "analytics.view", "/opportunities", "Open pipeline value + counts, from the pipeline report."),
    _w("communication_activity", "communications", "communications.service:metrics", "count", "count",
       "card", "analytics.view", "/communications", "Open conversations + message volume (scoped)."),
    _w("tax_workload", "tax_domain", "tax_domain.dashboard", "rollup", "count", "chart", "analytics.view",
       "/tax", "Tax engagement workload + returns due (book-scoped)."),
    _w("operational_health", "recommendations", "recommendations.workspace_recommendations", "health",
       "count", "gauge", "analytics.view", "/recommendations",
       "Operational recommendation load (highest-priority counts), from Operational Intelligence."),
    _w("runtime_health", "runtime", "runtime.consumption:adoption_stats", "health", "percent", "gauge",
       "analytics.view", "/observability",
       "Runtime adoption + observability health (firm-level infrastructure)."),
)

_WIDGET_BY_KEY = {w.key: w for w in WIDGET_REGISTRY}
EXECUTIVE_WIDGETS = tuple(w.key for w in WIDGET_REGISTRY if w.permission == "analytics.executive")


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # executive | operations | advisor | compliance | client_service
    runtime_gate: str
    widgets: tuple             # tuple of widget keys
    required_capabilities: tuple
    navigation: str            # deep-link destination
    refresh_policy: str
    governing_services: tuple
    lifecycle: str = "active"


def _d(key, owner, audience, gate, widgets, caps, navigation, governing, *, refresh="on_view",
       lifecycle="active"):
    return DashboardDef(key, owner, audience, gate, tuple(widgets), tuple(caps), navigation, refresh,
                        tuple(governing), lifecycle)


DASHBOARD_REGISTRY = (
    _d("executive", "executive_intelligence", "executive", "executive_dashboard.enabled",
       ("firm_aum", "aum_trend", "revenue_kpi", "client_growth", "operational_health",
        "compliance_workload", "workflow_status", "communication_activity"),
       ("analytics.executive",), "/executive",
       ("analytics", "portfolio", "bizdev", "workflow_automation", "recommendations", "communications")),
    _d("operations", "executive_intelligence", "operations", "executive_dashboard.enabled",
       ("advisor_workload", "workflow_status", "workflow_aging", "operational_health", "runtime_health"),
       ("analytics.view",), "/executive?dashboard=operations",
       ("work_queue", "workflow_automation", "recommendations", "runtime")),
    _d("advisor", "executive_intelligence", "advisor", "executive_dashboard.enabled",
       ("advisor_workload", "opportunity_pipeline", "review_cadence"),
       ("analytics.view",), "/executive?dashboard=advisor",
       ("work_queue", "opportunity", "portfolio")),
    _d("compliance", "executive_intelligence", "compliance", "executive_dashboard.enabled",
       ("compliance_workload", "review_cadence"),
       ("analytics.view",), "/executive?dashboard=compliance",
       ("compliance", "portfolio")),
    _d("client_service", "executive_intelligence", "client_service", "executive_dashboard.enabled",
       ("client_growth", "communication_activity", "review_cadence"),
       ("analytics.view",), "/executive?dashboard=client_service",
       ("people", "communications", "portfolio")),
    _d("revenue", "executive_intelligence", "executive", "executive_dashboard.enabled",
       ("revenue_kpi", "opportunity_pipeline"),
       ("analytics.executive",), "/executive?dashboard=revenue",
       ("bizdev", "opportunity")),
    _d("pipeline", "executive_intelligence", "advisor", "executive_dashboard.enabled",
       ("opportunity_pipeline",),
       ("analytics.view",), "/executive?dashboard=pipeline", ("opportunity",)),
    _d("workflow", "executive_intelligence", "operations", "executive_dashboard.enabled",
       ("workflow_status", "workflow_aging"),
       ("analytics.view",), "/executive?dashboard=workflow", ("workflow_automation",)),
)

_DASH_BY_KEY = {d.key: d for d in DASHBOARD_REGISTRY}


# --- lookups -----------------------------------------------------------------

def widget(key) -> WidgetDef | None:
    return _WIDGET_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def widget_registered(key) -> bool:
    return key in _WIDGET_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def coverage() -> dict:
    return {
        "dashboards": len(DASHBOARD_REGISTRY),
        "widgets": len(WIDGET_REGISTRY),
        "executive_widgets": len(EXECUTIVE_WIDGETS),
        "analytics_backed": sum(1 for w in WIDGET_REGISTRY if w.source.startswith("analytics.")),
    }
