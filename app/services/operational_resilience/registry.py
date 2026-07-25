"""Enterprise Operational Resilience registries (Phase D.60) — the declarative catalogs of the operational
resilience / incident / continuity composition layer.

Seven frozen, declarative catalogs; the layer owns NO persistence and defines NO second incident-management
platform, ticketing system, monitoring platform, help desk, disaster-recovery platform, change-management
platform, CMDB, scheduler, or alerting engine:

  * OPERATIONAL_SERVICE_REGISTRY — service classes whose health is composed (from the Observability service
    catalog, the Integration Platform, Vendor Management, Automation Orchestration, Security incidents).
  * INCIDENT_CATEGORY_REGISTRY — incident/alert categories, each naming its authoritative owner (Observability
    incidents / alerts / health, Security incidents, the Integration Platform, Automation Orchestration). No
    vendor-incident owner exists → declared not_configured.
  * CONTINUITY_CAPABILITY_REGISTRY — continuity capabilities composed from Business Continuity + Observability.
    Backup / restore / disaster recovery have no authoritative owner in the platform (the D.55 precedent) →
    declared not_configured, never fabricated.
  * RECOVERY_OBJECTIVE_REGISTRY — recovery objectives (RPO / RTO / recovery assets from Business Continuity).
    Recovery testing / failover have no authoritative owner → declared not_configured.
  * OPERATIONAL_DEPENDENCY_REGISTRY — service / integration / vendor / external dependencies (Observability
    catalog dependency graph + the Integration Platform + Vendor Management).
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * RESILIENCE_DASHBOARDS — every resilience dashboard.

Governance verifies every registry key is unique, every configured entry names an authoritative owner, every
panel names an authoritative owner + source + deep link, every derived value is labeled, and that this layer
never becomes a second incident / monitoring / DR / scheduler / CMDB / alerting system. Where no authoritative
owner exists (disaster recovery, backup, restore, recovery testing, failover, vendor incidents), the entry is
declared `not_configured` and reported honestly — never a fabricated operational status. Operational posture is
never a certification that production is healthy or continuity assured.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"
CONFIGURED = "configured"


# --- operational service registry --------------------------------------------

@dataclass(frozen=True)
class OperationalService:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _svc(key, label, owner, deep_links, *, capabilities=("observability.view",),
         runtime_gate="operational_resilience.enabled", config_status=CONFIGURED):
    return OperationalService(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                              config_status)


OPERATIONAL_SERVICE_REGISTRY = (
    _svc("core_services", "Core Services", "observability.catalog", ("/observability",)),
    _svc("infrastructure_services", "Infrastructure Services", "observability.catalog", ("/observability",)),
    _svc("integration_services", "Integration Services", "integration.service", ("/integration",),
         capabilities=("integration.view",)),
    _svc("vendor_services", "Vendor Services", "vendor_management", ("/vendor-management",),
         capabilities=("integration.view",)),
    _svc("automation_services", "Automation Services", "automation_orchestration", ("/automation",),
         capabilities=("automation.view",)),
    _svc("security_services", "Security Services", "security.incidents", ("/security-operations",),
         capabilities=("security.view",)),
)

_SVC_BY_KEY = {s.key: s for s in OPERATIONAL_SERVICE_REGISTRY}


# --- incident category registry ----------------------------------------------

@dataclass(frozen=True)
class IncidentCategory:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _inc(key, label, owner, deep_links, *, capabilities=("observability.view",),
         runtime_gate="incident_intelligence.enabled", config_status=CONFIGURED):
    return IncidentCategory(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                            config_status)


INCIDENT_CATEGORY_REGISTRY = (
    _inc("reliability_incidents", "Reliability Incidents", "observability.incidents", ("/observability",)),
    _inc("security_incidents", "Security Incidents", "security.incidents", ("/security/incidents",),
         capabilities=("security.view",)),
    _inc("service_alerts", "Service Alerts", "observability.alerts", ("/observability",)),
    _inc("health_check_failures", "Health-Check Failures", "observability.health", ("/observability",)),
    _inc("integration_failures", "Integration Failures", "integration.sync", ("/integration",),
         capabilities=("integration.view",)),
    _inc("workflow_escalations", "Workflow Escalations", "automation_orchestration", ("/automation",),
         capabilities=("automation.view",)),
    _inc("vendor_incidents", "Vendor Incidents", NOT_CONFIGURED, ("/vendor-management",),
         capabilities=("integration.view",), config_status=NOT_CONFIGURED),
)

_INC_BY_KEY = {i.key: i for i in INCIDENT_CATEGORY_REGISTRY}


# --- continuity capability registry ------------------------------------------

@dataclass(frozen=True)
class ContinuityCapability:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _cont(key, label, owner, deep_links, *, capabilities=("observability.view",),
          runtime_gate="continuity_intelligence.enabled", config_status=CONFIGURED):
    return ContinuityCapability(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                                config_status)


CONTINUITY_CAPABILITY_REGISTRY = (
    _cont("resilience_posture", "Resilience Posture", "business_continuity", ("/business-continuity",)),
    _cont("infrastructure_availability", "Infrastructure Availability", "business_continuity",
          ("/business-continuity", "/observability")),
    _cont("monitoring", "Monitoring", "observability", ("/observability",)),
    _cont("maintenance", "Maintenance Windows", "observability.alerts", ("/observability",)),
    _cont("backup", "Backup", NOT_CONFIGURED, ("/business-continuity",), config_status=NOT_CONFIGURED),
    _cont("restore", "Restore", NOT_CONFIGURED, ("/business-continuity",), config_status=NOT_CONFIGURED),
    _cont("disaster_recovery", "Disaster Recovery", NOT_CONFIGURED, ("/business-continuity",),
          config_status=NOT_CONFIGURED),
)

_CONT_BY_KEY = {c.key: c for c in CONTINUITY_CAPABILITY_REGISTRY}


# --- recovery objective registry ---------------------------------------------

@dataclass(frozen=True)
class RecoveryObjective:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _rec(key, label, owner, deep_links, *, capabilities=("observability.view",),
         runtime_gate="continuity_intelligence.enabled", config_status=CONFIGURED):
    return RecoveryObjective(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                             config_status)


RECOVERY_OBJECTIVE_REGISTRY = (
    _rec("recovery_assets", "Recovery Assets", "business_continuity", ("/business-continuity",)),
    _rec("rpo_targets", "RPO Targets", "business_continuity", ("/business-continuity",)),
    _rec("rto_targets", "RTO Targets", "business_continuity", ("/business-continuity",)),
    _rec("recovery_testing", "Recovery Testing", NOT_CONFIGURED, ("/business-continuity",),
         config_status=NOT_CONFIGURED),
    _rec("failover", "Failover", NOT_CONFIGURED, ("/business-continuity",), config_status=NOT_CONFIGURED),
)

_REC_BY_KEY = {r.key: r for r in RECOVERY_OBJECTIVE_REGISTRY}


# --- operational dependency registry -----------------------------------------

@dataclass(frozen=True)
class OperationalDependency:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _dep(key, label, owner, deep_links, *, capabilities=("observability.view",),
         runtime_gate="operational_resilience.enabled", config_status=CONFIGURED):
    return OperationalDependency(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                                 config_status)


OPERATIONAL_DEPENDENCY_REGISTRY = (
    _dep("service_dependencies", "Service Dependencies", "observability.catalog", ("/observability",)),
    _dep("integration_dependencies", "Integration Dependencies", "integration.service", ("/integration",),
         capabilities=("integration.view",)),
    _dep("vendor_dependencies", "Vendor Dependencies", "vendor_management", ("/vendor-management",),
         capabilities=("integration.view",)),
    _dep("external_dependencies", "External Dependencies", "integration_hub", ("/integration-hub",),
         capabilities=("integration.view",)),
)

_DEP_BY_KEY = {d.key: d for d in OPERATIONAL_DEPENDENCY_REGISTRY}


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
    # service health
    _p("service_health", "observability.catalog", "observability.catalog.metrics", "service", "count", "gauge",
       "observability.view", "/observability",
       "Operational service health (operational vs total services), from the Observability service catalog "
       "(the service inventory of record). No second CMDB or monitoring platform."),
    _p("degraded_services", "observability.catalog", "observability.catalog.metrics", "service", "count",
       "card", "observability.view", "/observability",
       "Degraded / down services, from the Observability service catalog. No second monitoring platform."),
    _p("failed_health_checks", "observability.health", "observability.health.metrics", "service", "count",
       "card", "observability.view", "/observability",
       "Failed health checks + diagnostic failures, from the Observability health owner."),
    # incidents / alerts
    _p("reliability_incidents", "observability.incidents", "observability.incidents.metrics", "incident",
       "count", "card", "observability.view", "/observability",
       "Open reliability incidents + findings, from the Observability incidents owner. No second incident "
       "manager."),
    _p("security_incidents", "security.incidents", "security.incidents.metrics", "incident", "count", "card",
       "security.view", "/security/incidents",
       "Open security incidents / findings / pending exceptions, from the Security incidents domain. No "
       "second incident-management platform."),
    _p("open_alerts", "observability.alerts", "observability.alerts.metrics", "alert", "count", "card",
       "observability.view", "/observability",
       "Open operational alerts, from the Observability alerts owner. The layer never generates or "
       "acknowledges an alert. No second alerting engine."),
    _p("active_maintenance_windows", "observability.alerts", "observability.alerts.metrics", "maintenance",
       "count", "card", "observability.view", "/observability",
       "Active planned-maintenance windows, from the Observability alerts owner. The layer never schedules "
       "maintenance. No second scheduler."),
    _p("integration_failures", "integration", "integration.service.overview_metrics", "incident", "count",
       "card", "integration.view", "/integration",
       "Integration failures (providers / connected connectors / sync failures), from the Integration "
       "Platform. No second integration platform."),
    _p("synchronization_failures", "integration", "integration.sync.metrics", "incident", "count", "card",
       "integration.view", "/integration",
       "Synchronization failures / connector errors / unresolved conflicts, from the Integration Platform "
       "sync engine."),
    _p("workflow_escalations", "automation_orchestration", "automation_orchestration.automation_summary",
       "incident", "count", "card", "automation.view", "/automation",
       "Workflow escalations + failed runs, from the D.51 Automation Orchestration layer. No second "
       "workflow engine."),
    # vendor operational status
    _p("vendor_operational_status", "vendor_management", "vendor_management.vendor_summary", "service", "count",
       "card", "integration.view", "/vendor-management",
       "Vendor operational status (governance score + dependencies + expiring certificates), from the D.56 "
       "Vendor Management layer. Vendor incidents have no dedicated owner (not_configured)."),
    # continuity / recovery (compose D.55)
    _p("resilience_posture", "business_continuity", "business_continuity.continuity_summary", "continuity",
       "percent", "gauge", "observability.view", "/business-continuity",
       "Firm resilience posture (resilience score), from the D.55 Business Continuity layer over the "
       "authoritative Observability + Runtime owners."),
    _p("infrastructure_availability", "business_continuity", "business_continuity.continuity_summary",
       "continuity", "percent", "card", "observability.view", "/business-continuity",
       "Infrastructure availability, from the D.55 Business Continuity layer."),
    _p("continuity_coverage", "business_continuity", "business_continuity.continuity_summary", "continuity",
       "coverage", "card", "observability.view", "/business-continuity",
       "Business-continuity coverage (backup coverage + service incidents), from the D.55 Business Continuity "
       "layer. Backup / restore / DR have no authoritative owner and are reported not_configured."),
    _p("recovery_readiness", "operational_resilience", "operational_resilience.registry", "recovery",
       "coverage", "gauge", "observability.view", "/operational-resilience?dashboard=recovery_readiness",
       "Recovery readiness — recovery assets with declared RPO/RTO objectives vs not_configured recovery "
       "testing / failover. A DERIVED coverage summary from the recovery-objective registry.", derived=True),
    _p("rpo_targets", "operational_resilience", "operational_resilience.registry", "recovery", "count", "list",
       "observability.view", "/operational-resilience?dashboard=recovery_readiness",
       "Declared recovery-point objectives (RPO) — from the recovery-objective registry (composed from "
       "Business Continuity). Declarative targets only.", derived=True),
    _p("rto_targets", "operational_resilience", "operational_resilience.registry", "recovery", "count", "list",
       "observability.view", "/operational-resilience?dashboard=recovery_readiness",
       "Declared recovery-time objectives (RTO) — from the recovery-objective registry. Declarative targets "
       "only.", derived=True),
    _p("recovery_test_coverage", "not_configured", "operational_resilience.registry", "recovery", "status",
       "card", "observability.view", "/operational-resilience?dashboard=recovery_readiness",
       "Recovery-test coverage — NO authoritative recovery-testing owner exists in the platform; reported "
       "not_configured, never a fabricated test status.", derived=True),
    # dependency health
    _p("dependency_health", "observability.catalog", "observability.catalog.list_dependencies", "dependency",
       "count", "card", "observability.view", "/observability",
       "Declared service-dependency count, from the Observability service catalog dependency graph."),
    _p("service_dependencies", "operational_resilience", "operational_resilience.registry", "dependency",
       "count", "list", "observability.view", "/operational-resilience?dashboard=dependency_health",
       "The registered operational-dependency catalog — each naming its authoritative owner.", derived=True),
    # registry-derived (DERIVED, catalog)
    _p("operational_service_inventory", "operational_resilience", "operational_resilience.registry", "service",
       "count", "list", "observability.view", "/operational-resilience",
       "The registered operational-service catalog — each naming its authoritative owner + config status.",
       derived=True),
    _p("incident_category_inventory", "operational_resilience", "operational_resilience.registry", "incident",
       "count", "list", "observability.view", "/operational-resilience?dashboard=incident_readiness",
       "The registered incident-category catalog — each naming its authoritative owner. Vendor incidents have "
       "no dedicated owner (not_configured).", derived=True),
    _p("resilience_gaps", "operational_resilience", "operational_resilience.compose", "resilience", "list",
       "list", "observability.view", "/operational-resilience",
       "Resilience gaps — not_configured continuity / recovery / incident capabilities (backup, restore, DR, "
       "recovery testing, failover, vendor incidents). A DERIVED honesty summary, never a fabricated status.",
       derived=True),
    _p("executive_operational_status", "operational_resilience", "operational_resilience.compose",
       "resilience", "distribution", "gauge", "analytics.executive", "/operational-resilience",
       "DERIVED executive operational posture — configured vs not_configured capabilities + open incident / "
       "alert / degraded-service counts across the authoritative owners. Operational posture only, never a "
       "certification that production is healthy or continuity assured; an absent incident is not health.",
       derived=True),
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


_OR_CAPS = ("observability.view", "analytics.executive")

RESILIENCE_DASHBOARDS = (
    _d("operational_resilience", "operational_resilience", "operations", "operational_resilience.enabled",
       ("resilience_posture", "service_health", "resilience_gaps"),
       _OR_CAPS, "/operational-resilience?dashboard=operational_resilience",
       ("business_continuity", "observability")),
    _d("incident_readiness", "operational_resilience", "operations", "incident_intelligence.enabled",
       ("reliability_incidents", "security_incidents", "open_alerts", "incident_category_inventory"),
       _OR_CAPS, "/operational-resilience?dashboard=incident_readiness",
       ("observability", "security")),
    _d("service_health", "operational_resilience", "operations", "operational_resilience.enabled",
       ("service_health", "degraded_services", "failed_health_checks"),
       _OR_CAPS, "/operational-resilience?dashboard=service_health",
       ("observability",)),
    _d("continuity_coverage", "operational_resilience", "operations", "continuity_intelligence.enabled",
       ("continuity_coverage", "infrastructure_availability", "active_maintenance_windows"),
       _OR_CAPS, "/operational-resilience?dashboard=continuity_coverage",
       ("business_continuity", "observability")),
    _d("recovery_readiness", "operational_resilience", "operations", "continuity_intelligence.enabled",
       ("recovery_readiness", "rpo_targets", "rto_targets", "recovery_test_coverage"),
       _OR_CAPS, "/operational-resilience?dashboard=recovery_readiness",
       ("business_continuity",)),
    _d("dependency_health", "operational_resilience", "operations", "operational_resilience.enabled",
       ("dependency_health", "service_dependencies", "integration_failures"),
       _OR_CAPS, "/operational-resilience?dashboard=dependency_health",
       ("observability", "integration")),
    _d("vendor_operational_health", "operational_resilience", "operations", "operational_resilience.enabled",
       ("vendor_operational_status", "synchronization_failures", "workflow_escalations"),
       _OR_CAPS, "/operational-resilience?dashboard=vendor_operational_health",
       ("vendor_management", "integration", "automation_orchestration")),
    _d("executive_operational_status", "operational_resilience", "executive",
       "operational_resilience.enabled",
       ("executive_operational_status", "resilience_posture", "service_health"),
       _OR_CAPS, "/operational-resilience?dashboard=executive_operational_status",
       ("business_continuity", "observability")),
)

_DASH_BY_KEY = {d.key: d for d in RESILIENCE_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def operational_service(key) -> OperationalService | None:
    return _SVC_BY_KEY.get(key)


def incident_category(key) -> IncidentCategory | None:
    return _INC_BY_KEY.get(key)


def continuity_capability(key) -> ContinuityCapability | None:
    return _CONT_BY_KEY.get(key)


def recovery_objective(key) -> RecoveryObjective | None:
    return _REC_BY_KEY.get(key)


def operational_dependency(key) -> OperationalDependency | None:
    return _DEP_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def _all_entries():
    return (*OPERATIONAL_SERVICE_REGISTRY, *INCIDENT_CATEGORY_REGISTRY, *CONTINUITY_CAPABILITY_REGISTRY,
            *RECOVERY_OBJECTIVE_REGISTRY, *OPERATIONAL_DEPENDENCY_REGISTRY)


def not_configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == NOT_CONFIGURED)


def configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == CONFIGURED)


def coverage() -> dict:
    return {
        "operational_services": len(OPERATIONAL_SERVICE_REGISTRY),
        "incident_categories": len(INCIDENT_CATEGORY_REGISTRY),
        "continuity_capabilities": len(CONTINUITY_CAPABILITY_REGISTRY),
        "recovery_objectives": len(RECOVERY_OBJECTIVE_REGISTRY),
        "operational_dependencies": len(OPERATIONAL_DEPENDENCY_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(RESILIENCE_DASHBOARDS),
        "configured_domains": len(configured_domains()),
        "not_configured_domains": len(not_configured_domains()),
    }
