"""Enterprise Environment Management registries (Phase D.64) — the declarative catalogs of the environment /
platform / deployment-topology / lifecycle / infrastructure-dependency composition layer.

Seven frozen, declarative catalogs; the layer owns NO persistence and defines NO second CMDB,
infrastructure-management platform, cloud-management platform, deployment orchestrator, asset inventory,
configuration database, environment manager, or monitoring platform:

  * ENVIRONMENT_REGISTRY — the environment domains (environment inventory, environment types, regions,
    activation state, runtime readiness, environment health, configuration scope; cloud provisioning is
    NOT_CONFIGURED), each naming its authoritative owner, read surface, prohibited mutation surface, evidence
    source, capabilities, runtime gate, environment scope, deep links, and config status.
  * PLATFORM_REGISTRY — the platform / service domains (service inventory, types, criticality, status,
    ownership, service references; cloud resources / servers / containers / VMs are NOT_CONFIGURED).
  * DEPLOYMENT_TOPOLOGY_REGISTRY — deployment-topology domains (deployment references, versions, migration
    head, environment mapping, release timeline; deployment execution / rollout status are NOT_CONFIGURED).
  * LIFECYCLE_REGISTRY — platform-lifecycle domains (runtime readiness, environment activation, service
    operational status, migration lifecycle; formal lifecycle state, deprecation, retirement records, and
    decommission schedule are NOT_CONFIGURED — no authoritative owner exists).
  * INFRASTRUCTURE_DEPENDENCY_REGISTRY — infrastructure-dependency domains (service dependency graph,
    dependency types, integration dependencies, runtime-configuration dependencies; infrastructure host
    metadata, network topology, and cloud-resource dependencies are NOT_CONFIGURED).
  * PANEL_REGISTRY — every dashboard panel. * ENVIRONMENT_DASHBOARDS — every environment dashboard.

Governance verifies every registry key is unique, every configured entry names an authoritative owner, every
panel names an authoritative owner + source + deep link, every derived value is labeled, and that this layer
never becomes a second CMDB / infrastructure platform / cloud-management platform / deployment orchestrator /
asset inventory / configuration database / environment manager / monitoring platform. Where no authoritative
owner exists (cloud resources, servers, containers, VMs, formal lifecycle state, retirement records,
decommission schedule, host / network topology, live deployment execution), the entry is declared
`not_configured` and reported honestly — never a fabricated environment, deployment, infrastructure, topology,
lifecycle state, environment health, platform ownership, or retirement status. **Environment metadata is not
live infrastructure, a deployment reference is not a deployment, and an active flag is not a lifecycle
guarantee.**
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"
CONFIGURED = "configured"


@dataclass(frozen=True)
class DomainEntry:
    key: str
    label: str
    owner: str                 # authoritative owner (or "not_configured")
    read_surface: str          # the authoritative read
    mutation_surface: str      # the prohibited mutation surface (never called)
    evidence_source: str       # where the evidence lives
    capabilities: tuple
    runtime_gate: str
    environment_scope: str     # all_environments | per_environment | firm | not_scoped
    deep_links: tuple
    config_status: str = CONFIGURED


def _e(key, label, owner, read_surface, mutation_surface, evidence_source, deep_links, *,
       capabilities=("observability.view",), runtime_gate="environment_management.enabled",
       environment_scope="firm", config_status=CONFIGURED):
    return DomainEntry(key, label, owner, read_surface, mutation_surface, evidence_source, tuple(capabilities),
                       runtime_gate, environment_scope, tuple(deep_links), config_status)


_OBS = ("/observability",)
_NC = NOT_CONFIGURED


# --- environment registry ----------------------------------------------------

ENVIRONMENT_REGISTRY = (
    _e("environment_inventory", "Environment Inventory", "observability.catalog",
       "observability.catalog.list_environment_profiles", "create_environment_profile",
       "observability_environment_profiles", _OBS, environment_scope="all_environments"),
    _e("environment_types", "Environment Types", "observability.catalog",
       "observability_tables.ENVIRONMENTS", "create_environment_profile", "environment_profiles.environment",
       _OBS, environment_scope="all_environments"),
    _e("environment_regions", "Environment Regions", "observability.catalog",
       "observability.catalog.list_environment_profiles", "create_environment_profile",
       "environment_profiles.region", _OBS, environment_scope="all_environments"),
    _e("environment_activation", "Environment Activation State", "observability.catalog",
       "observability.catalog.list_environment_profiles", "create_environment_profile",
       "environment_profiles.active", _OBS, environment_scope="all_environments"),
    _e("environment_runtime_readiness", "Environment Runtime Readiness", "observability.health",
       "observability.health.list_runtime_snapshots", "capture_runtime_snapshot",
       "observability_runtime_snapshots", _OBS, environment_scope="per_environment"),
    _e("environment_health", "Environment Health Summary", "observability.service",
       "observability.service.overview_metrics", "set_service_status", "services + health checks", _OBS),
    _e("environment_configuration_scope", "Environment Configuration Scope", "runtime",
       "runtime.consumption.adoption_stats", "set_flag", "runtime engine", ("/runtime",),
       runtime_gate="platform_lifecycle.enabled"),
    _e("cloud_environment_provisioning", "Cloud Environment Provisioning", _NC, "n/a", "n/a", "n/a", _OBS,
       config_status=_NC, environment_scope="not_scoped"),
)


# --- platform registry -------------------------------------------------------

PLATFORM_REGISTRY = (
    _e("platform_inventory", "Platform / Service Inventory", "observability.catalog",
       "observability.catalog.list_services", "create_service", "observability_services", _OBS),
    _e("platform_types", "Platform Types", "observability.catalog", "observability_tables.SERVICE_TYPES",
       "create_service", "services.service_type", _OBS),
    _e("platform_criticality", "Platform Criticality", "observability.catalog",
       "observability.catalog.list_services", "create_service", "services.criticality", _OBS),
    _e("platform_status", "Platform Operational Status", "observability.catalog",
       "observability.catalog.list_services", "set_service_status", "services.status", _OBS),
    _e("platform_ownership", "Platform Ownership", "observability.catalog",
       "observability.catalog.list_services", "create_service", "services.owner_user_id", _OBS),
    _e("platform_references", "Platform Domain References", "observability.catalog",
       "observability.catalog.list_services", "create_service", "services.reference_type", _OBS),
    _e("cloud_resources", "Cloud Resources", _NC, "n/a", "n/a", "n/a", _OBS, config_status=_NC),
    _e("servers_hosts", "Servers / Hosts", _NC, "n/a", "n/a", "n/a", _OBS, config_status=_NC),
    _e("containers_vms", "Containers / Virtual Machines", _NC, "n/a", "n/a", "n/a", _OBS, config_status=_NC),
)


# --- deployment topology registry --------------------------------------------

DEPLOYMENT_TOPOLOGY_REGISTRY = (
    _e("deployment_references", "Deployment References", "observability.catalog",
       "observability.catalog.list_deployment_references", "create_deployment_reference",
       "observability_deployment_references", _OBS, runtime_gate="deployment_topology.enabled"),
    _e("deployment_versions", "Deployment Versions", "observability.catalog",
       "observability.catalog.list_deployment_references", "create_deployment_reference",
       "deployment_references.version", _OBS, runtime_gate="deployment_topology.enabled"),
    _e("deployment_migration_head", "Deployment Migration Head", "observability.catalog",
       "observability.catalog.list_deployment_references", "create_deployment_reference",
       "deployment_references.migration_head", _OBS, runtime_gate="deployment_topology.enabled"),
    _e("deployment_environment_mapping", "Deployment Environment Mapping", "observability.catalog",
       "observability.catalog.list_deployment_references", "create_deployment_reference",
       "deployment_references.environment_profile_id", _OBS, runtime_gate="deployment_topology.enabled",
       environment_scope="per_environment"),
    _e("deployment_release_timeline", "Deployment Release Timeline", "observability.catalog",
       "observability.catalog.list_deployment_references", "create_deployment_reference",
       "deployment_references.released_at", _OBS, runtime_gate="deployment_topology.enabled"),
    _e("deployment_execution_status", "Deployment Execution Status", _NC, "n/a", "n/a", "n/a", _OBS,
       config_status=_NC, runtime_gate="deployment_topology.enabled"),
    _e("deployment_rollout_status", "Deployment Rollout Status", _NC, "n/a", "n/a", "n/a", _OBS,
       config_status=_NC, runtime_gate="deployment_topology.enabled"),
)


# --- lifecycle registry ------------------------------------------------------

LIFECYCLE_REGISTRY = (
    _e("runtime_readiness_state", "Runtime Readiness State", "observability.health",
       "observability.health.list_runtime_snapshots", "capture_runtime_snapshot",
       "runtime_snapshots.summary", _OBS, runtime_gate="platform_lifecycle.enabled",
       environment_scope="per_environment"),
    _e("environment_activation_lifecycle", "Environment Activation Lifecycle", "observability.catalog",
       "observability.catalog.list_environment_profiles", "create_environment_profile",
       "environment_profiles.active", _OBS, runtime_gate="platform_lifecycle.enabled",
       environment_scope="all_environments"),
    _e("service_operational_lifecycle", "Service Operational Lifecycle", "observability.catalog",
       "observability.catalog.list_services", "set_service_status", "services.status", _OBS,
       runtime_gate="platform_lifecycle.enabled"),
    _e("migration_lifecycle_state", "Migration Lifecycle State", "observability.health",
       "observability.health._expected_head", "capture_runtime_snapshot", "alembic scripts + snapshots", _OBS,
       runtime_gate="platform_lifecycle.enabled"),
    _e("formal_lifecycle_state", "Formal Lifecycle State", _NC, "n/a", "n/a", "n/a", _OBS, config_status=_NC,
       runtime_gate="platform_lifecycle.enabled"),
    _e("deprecation_records", "Deprecation Records", _NC, "n/a", "n/a", "n/a", _OBS, config_status=_NC,
       runtime_gate="platform_lifecycle.enabled"),
    _e("retirement_records", "Platform Retirement Records", _NC, "n/a", "n/a", "n/a", _OBS, config_status=_NC,
       runtime_gate="platform_lifecycle.enabled"),
    _e("decommission_schedule", "Environment Decommission Schedule", _NC, "n/a", "n/a", "n/a", _OBS,
       config_status=_NC, runtime_gate="platform_lifecycle.enabled"),
)


# --- infrastructure dependency registry --------------------------------------

INFRASTRUCTURE_DEPENDENCY_REGISTRY = (
    _e("service_dependency_graph", "Service Dependency Graph", "observability.catalog",
       "observability.catalog.list_dependencies", "add_dependency", "observability_service_dependencies",
       _OBS, runtime_gate="deployment_topology.enabled"),
    _e("dependency_types", "Dependency Types", "observability.catalog",
       "observability_tables.DEPENDENCY_TYPES", "add_dependency", "service_dependencies.dependency_type",
       _OBS, runtime_gate="deployment_topology.enabled"),
    _e("integration_dependencies", "Integration Dependencies", "integration.service",
       "integration.service.overview_metrics", "create_connector", "integration_connectors",
       ("/integration",), capabilities=("integration.view",), runtime_gate="deployment_topology.enabled"),
    _e("runtime_configuration_dependencies", "Runtime Configuration Dependencies", "runtime",
       "runtime.consumption.adoption_stats", "set_flag", "runtime engine", ("/runtime",),
       runtime_gate="deployment_topology.enabled"),
    _e("infrastructure_host_metadata", "Infrastructure Host Metadata", _NC, "n/a", "n/a", "n/a", _OBS,
       config_status=_NC, runtime_gate="deployment_topology.enabled"),
    _e("network_topology", "Network Topology", _NC, "n/a", "n/a", "n/a", _OBS, config_status=_NC,
       runtime_gate="deployment_topology.enabled"),
    _e("cloud_resource_dependencies", "Cloud Resource Dependencies", _NC, "n/a", "n/a", "n/a", _OBS,
       config_status=_NC, runtime_gate="deployment_topology.enabled"),
)

_CD_BY_KEY = {}
for _reg in (ENVIRONMENT_REGISTRY, PLATFORM_REGISTRY, DEPLOYMENT_TOPOLOGY_REGISTRY, LIFECYCLE_REGISTRY,
             INFRASTRUCTURE_DEPENDENCY_REGISTRY):
    for _entry in _reg:
        _CD_BY_KEY[_entry.key] = _entry


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


_NC_NOTE = "NO authoritative owner exists in the platform today; reported not_configured, never fabricated."

PANEL_REGISTRY = (
    # environment
    _p("environment_inventory", "observability.catalog", "environment_management.registry", "environment",
       "count", "list", "observability.view", "/environment-management",
       "The registered environment domains — each naming its authoritative owner + read + prohibited mutation "
       "surface + evidence + config status. Metadata only. Environment metadata is not live infrastructure.",
       derived=True),
    _p("environment_profiles", "observability.catalog", "observability.catalog.list_environment_profiles",
       "environment", "count", "card", "observability.view", "/observability",
       "Registered environment profiles (production / staging / development / test), from the Observability "
       "catalog. Declared environment metadata — not live infrastructure."),
    _p("environment_type_distribution", "observability.catalog",
       "observability.catalog.list_environment_profiles", "environment", "distribution", "chart",
       "observability.view", "/observability",
       "Environment-type distribution across the registered environment profiles. Counts only."),
    _p("active_environments", "observability.catalog", "observability.catalog.list_environment_profiles",
       "environment", "count", "card", "observability.view", "/observability",
       "Active vs inactive environment profiles (the `active` flag). An active flag is not a lifecycle "
       "guarantee."),
    _p("environment_region_coverage", "observability.catalog",
       "observability.catalog.list_environment_profiles", "environment", "coverage", "card",
       "observability.view", "/observability",
       "Environment profiles with a declared region vs unspecified. Region metadata only — never a host, "
       "address, or connection string."),
    _p("environment_runtime_readiness", "observability.health", "observability.health.list_runtime_snapshots",
       "runtime", "status", "card", "observability.view", "/observability",
       "The latest runtime-snapshot readiness (ready / not_ready) per environment, from the Observability "
       "health owner. A runtime snapshot is a point-in-time probe, not continuous environment health."),
    _p("environment_health_summary", "observability.service", "observability.service.overview_metrics",
       "environment", "status", "card", "observability.view", "/observability",
       "Environment health summary (operational vs degraded services, failed health checks), from the "
       "Observability service overview. Not a per-environment SLA certification."),
    _p("environment_gaps", "environment_management", "environment_management.registry", "environment", "list",
       "list", "observability.view", "/environment-management",
       "Environment / platform / topology / lifecycle areas with no authoritative owner (cloud provisioning, "
       "servers, containers, VMs, retirement records) — reported honestly.", derived=True),
    # platform
    _p("platform_inventory", "observability.catalog", "observability.catalog.list_services", "platform",
       "count", "card", "observability.view", "/observability",
       "The registered platform / service inventory, from the Observability catalog. Logical services, not "
       "infrastructure hosts."),
    _p("platform_type_distribution", "observability.catalog", "observability.catalog.list_services",
       "platform", "distribution", "chart", "observability.view", "/observability",
       "Platform-type distribution (application / database / scheduler / integration / external / queue / …). "
       "Counts only."),
    _p("platform_criticality_distribution", "observability.catalog", "observability.catalog.list_services",
       "platform", "distribution", "chart", "observability.view", "/observability",
       "Platform criticality distribution (low / medium / high / critical). Counts only."),
    _p("platform_status_summary", "observability.catalog", "observability.catalog.list_services", "platform",
       "status", "card", "observability.view", "/observability",
       "Platform operational-status summary (operational / degraded / down / maintenance / unknown). A status "
       "is not a lifecycle state."),
    _p("platform_ownership_coverage", "observability.catalog", "observability.catalog.list_services",
       "platform", "coverage", "gauge", "observability.view", "/observability",
       "Platform-ownership coverage — services with a declared owner vs unowned. Ownership presence only — "
       "never an owner identity / contact.", derived=True),
    _p("cloud_resource_inventory", "not_configured", "environment_management.registry", "platform", "status",
       "card", "observability.view", "/environment-management",
       "Cloud resources / servers / containers / VMs. " + _NC_NOTE, derived=True),
    # deployment topology
    _p("deployment_reference_inventory", "observability.catalog",
       "observability.catalog.list_deployment_references", "deployment", "count", "card", "observability.view",
       "/observability",
       "Registered deployment references (declared deployment metadata), from the Observability catalog. A "
       "deployment reference is not a deployment; deployment EXECUTION has no owner (not_configured)."),
    _p("deployment_version_coverage", "observability.catalog",
       "observability.catalog.list_deployment_references", "deployment", "coverage", "card",
       "observability.view", "/observability",
       "Deployment references carrying a version + migration head vs incomplete. Metadata coverage only."),
    _p("deployment_environment_mapping", "observability.catalog",
       "observability.catalog.list_deployment_references", "topology", "coverage", "gauge",
       "observability.view", "/observability",
       "Deployment references mapped to an environment profile vs unmapped — a DERIVED topology-coverage "
       "summary.", derived=True),
    _p("deployment_migration_alignment", "observability.catalog",
       "environment_management.compose", "deployment", "verification", "gauge", "observability.view",
       "/environment-management",
       "Deployment references whose recorded migration head matches the live Alembic head — a DERIVED "
       "alignment check. A matching head does not prove the deployment ran.", derived=True),
    _p("deployment_execution_status", "not_configured", "environment_management.registry", "deployment",
       "status", "card", "observability.view", "/environment-management",
       "Deployment execution / rollout status. " + _NC_NOTE + " A deployment reference is not a deployment.",
       derived=True),
    # runtime landscape
    _p("runtime_snapshot_coverage", "observability.health", "observability.health.list_runtime_snapshots",
       "runtime", "count", "card", "observability.view", "/observability",
       "Runtime-snapshot coverage (how many point-in-time runtime snapshots exist), from the Observability "
       "health owner. Point-in-time probes, not continuous health."),
    _p("runtime_migration_alignment", "observability.health", "environment_management.compose", "runtime",
       "verification", "gauge", "observability.view", "/environment-management",
       "Latest runtime snapshot's migration-in-sync state vs the live head — a DERIVED alignment check. A "
       "clean migration check does not prove application health.", derived=True),
    _p("runtime_configuration_coverage", "runtime", "runtime.consumption.adoption_stats", "runtime", "count",
       "card", "observability.view", "/runtime",
       "Runtime-configuration coverage (feature-flag adoption), from the Runtime Engine. Counts + status only "
       "— never a sensitive configuration value."),
    _p("runtime_landscape_summary", "environment_management", "environment_management.compose", "runtime",
       "coverage", "gauge", "observability.view", "/environment-management",
       "DERIVED runtime-landscape summary — environments + runtime readiness + configuration coverage. "
       "Operational visibility only, never a certified environment health.", derived=True),
    # infrastructure dependencies
    _p("dependency_graph_coverage", "observability.catalog", "observability.catalog.list_dependencies",
       "dependency", "count", "card", "observability.view", "/observability",
       "Service dependency-graph coverage (declared service-to-service dependencies), from the Observability "
       "catalog. A logical dependency graph, not a network topology."),
    _p("dependency_type_distribution", "observability.catalog", "observability.catalog.list_dependencies",
       "dependency", "distribution", "chart", "observability.view", "/observability",
       "Dependency-type distribution (hard / soft / runtime). Counts only."),
    _p("integration_dependency_coverage", "integration.service", "integration.service.overview_metrics",
       "dependency", "count", "card", "integration.view", "/integration",
       "Integration-dependency coverage (connected external systems / providers), from the Integration "
       "platform. External-system references only — never a credential / endpoint / connection string."),
    _p("infrastructure_topology_availability", "not_configured", "environment_management.registry",
       "dependency", "status", "card", "observability.view", "/environment-management",
       "Infrastructure host metadata / network topology / cloud-resource dependencies. " + _NC_NOTE +
       " Never private topology.", derived=True),
    # lifecycle
    _p("operational_lifecycle_state", "observability.catalog", "environment_management.compose", "lifecycle",
       "distribution", "chart", "observability.view", "/environment-management",
       "DERIVED operational-lifecycle view — service operational status + environment activation as a proxy "
       "for lifecycle. Formal lifecycle state (planned → deprecated → retired) has no owner (not_configured).",
       derived=True),
    _p("lifecycle_readiness", "environment_management", "environment_management.compose", "lifecycle",
       "coverage", "gauge", "observability.view", "/environment-management",
       "DERIVED lifecycle-readiness coverage — runtime readiness + migration alignment + active environments − "
       "not_configured / not_ready areas. OPERATIONAL READINESS ONLY, never a certified lifecycle / "
       "retirement decision.", derived=True),
    _p("formal_lifecycle_state", "not_configured", "environment_management.registry", "lifecycle", "status",
       "card", "observability.view", "/environment-management",
       "Formal platform lifecycle state (planned / active / deprecated / retired). " + _NC_NOTE, derived=True),
    _p("retirement_readiness", "not_configured", "environment_management.registry", "lifecycle", "status",
       "card", "observability.view", "/environment-management",
       "Platform-retirement readiness / retirement records / decommission schedule. " + _NC_NOTE, derived=True),
    # governance + executive
    _p("configured_environment_domains", "environment_management", "environment_management.registry",
       "environment", "coverage", "gauge", "observability.view", "/environment-management",
       "Configured vs not_configured environment / platform / topology / lifecycle / dependency coverage — a "
       "DERIVED coverage summary.", derived=True),
    _p("unconfigured_environment_domains", "environment_management", "environment_management.registry",
       "environment", "list", "list", "observability.view", "/environment-management",
       "The environment / platform / topology / lifecycle / dependency areas with no authoritative owner — "
       "reported honestly, never fabricated.", derived=True),
    _p("environment_governance_status", "environment_management", "environment_management.compose",
       "verification", "count", "card", "observability.view", "/environment-management",
       "Composed governance status across the read-only layers — a DERIVED count of clean vs failing "
       "governance checkers. Operational visibility, never certification.", derived=True),
    _p("executive_platform_posture", "environment_management", "environment_management.compose",
       "verification", "distribution", "gauge", "analytics.executive", "/environment-management",
       "DERIVED executive platform & environment posture — environments + platforms + deployment topology + "
       "lifecycle readiness + configured vs not_configured domains. Operational visibility only, never a "
       "certified environment health, deployment status, or retirement decision.", derived=True),
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


_EM_CAPS = ("observability.view", "analytics.executive")

ENVIRONMENT_DASHBOARDS = (
    _d("environment_overview", "environment_management", "operations", "environment_management.enabled",
       ("environment_profiles", "environment_type_distribution", "active_environments",
        "environment_health_summary", "configured_environment_domains"),
       _EM_CAPS, "/environment-management?dashboard=environment_overview",
       ("observability.catalog", "observability.service")),
    _d("deployment_topology", "environment_management", "operations", "deployment_topology.enabled",
       ("deployment_reference_inventory", "deployment_version_coverage", "deployment_environment_mapping",
        "deployment_migration_alignment", "deployment_execution_status"),
       _EM_CAPS, "/environment-management?dashboard=deployment_topology",
       ("observability.catalog",)),
    _d("platform_lifecycle", "environment_management", "operations", "platform_lifecycle.enabled",
       ("operational_lifecycle_state", "lifecycle_readiness", "formal_lifecycle_state", "retirement_readiness"),
       _EM_CAPS, "/environment-management?dashboard=platform_lifecycle",
       ("observability.catalog", "observability.health", "environment_management")),
    _d("infrastructure_dependencies", "environment_management", "operations", "deployment_topology.enabled",
       ("dependency_graph_coverage", "dependency_type_distribution", "integration_dependency_coverage",
        "infrastructure_topology_availability"),
       _EM_CAPS, "/environment-management?dashboard=infrastructure_dependencies",
       ("observability.catalog", "integration")),
    _d("runtime_landscape", "environment_management", "operations", "environment_management.enabled",
       ("environment_runtime_readiness", "runtime_snapshot_coverage", "runtime_migration_alignment",
        "runtime_configuration_coverage", "runtime_landscape_summary"),
       _EM_CAPS, "/environment-management?dashboard=runtime_landscape",
       ("observability.health", "runtime", "environment_management")),
    _d("environment_governance", "environment_management", "operations", "environment_management.enabled",
       ("environment_inventory", "configured_environment_domains", "unconfigured_environment_domains",
        "environment_governance_status"),
       _EM_CAPS, "/environment-management?dashboard=environment_governance",
       ("environment_management", "observability")),
    _d("executive_platform_landscape", "environment_management", "executive", "environment_management.enabled",
       ("executive_platform_posture", "lifecycle_readiness", "platform_inventory", "environment_profiles"),
       _EM_CAPS, "/environment-management?dashboard=executive_platform_landscape",
       ("environment_management", "observability")),
    _d("lifecycle_readiness", "environment_management", "operations", "platform_lifecycle.enabled",
       ("lifecycle_readiness", "runtime_migration_alignment", "retirement_readiness",
        "environment_governance_status"),
       _EM_CAPS, "/environment-management?dashboard=lifecycle_readiness",
       ("environment_management", "observability.health")),
)

_DASH_BY_KEY = {d.key: d for d in ENVIRONMENT_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def domain(key) -> DomainEntry | None:
    return _CD_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def _all_entries():
    return (*ENVIRONMENT_REGISTRY, *PLATFORM_REGISTRY, *DEPLOYMENT_TOPOLOGY_REGISTRY, *LIFECYCLE_REGISTRY,
            *INFRASTRUCTURE_DEPENDENCY_REGISTRY)


def not_configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == NOT_CONFIGURED)


def configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == CONFIGURED)


def coverage() -> dict:
    return {
        "environment_domains": len(ENVIRONMENT_REGISTRY),
        "platform_domains": len(PLATFORM_REGISTRY),
        "deployment_topology_domains": len(DEPLOYMENT_TOPOLOGY_REGISTRY),
        "lifecycle_domains": len(LIFECYCLE_REGISTRY),
        "infrastructure_dependency_domains": len(INFRASTRUCTURE_DEPENDENCY_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(ENVIRONMENT_DASHBOARDS),
        "configured_domains": len(configured_domains()),
        "not_configured_domains": len(not_configured_domains()),
    }
