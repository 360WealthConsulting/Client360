"""Enterprise Environment Management panel composition (Phase D.64).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second CMDB /
inventory / metric, and never any credential / secret / token / environment variable / connection string /
private key / deployment payload / protected infrastructure detail / private topology / sensitive configuration
value. Environment / platform / deployment / dependency panels compose the Observability catalog
(environment profiles, deployment references, service inventory, the service dependency graph), the
Observability health owner (runtime snapshots, the live migration head), the Observability service overview,
the Runtime engine (configuration coverage), and the Integration platform (integration dependencies). Cloud
resources / servers / containers / VMs / formal lifecycle state / retirement records / decommission schedule /
host & network topology / live deployment execution have no authoritative owner and are emitted
``available=False`` with ``config_status='not_configured'`` — honest, never a fabricated environment,
deployment, infrastructure, topology, lifecycle state, environment health, platform ownership, or retirement
status. Every compose is fail-closed and self-restricts. This layer NEVER creates an environment, deploys code,
provisions infrastructure, modifies topology, changes lifecycle state, executes a cloud operation, writes
configuration, or deletes an environment. A derived value describes OPERATIONAL VISIBILITY / READINESS, never a
certified environment health, deployment status, provisioning outcome, or retirement decision — **environment
metadata is not live infrastructure, a deployment reference is not a deployment, an active flag is not a
lifecycle guarantee, and a runtime snapshot is not continuous environment health.**
"""
from __future__ import annotations

from collections import Counter

from . import registry, stats
from .model import PanelResult


def _restricted(pdef):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=None,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, restricted=True,
                       available=False, derived=pdef.derived)


def _result(pdef, value, *, available=True, config_status="configured"):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=value,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, available=available,
                       derived=pdef.derived, config_status=config_status)


def _not_configured(pdef, note):
    return _result(pdef, {"status": registry.NOT_CONFIGURED, "note": note}, available=False,
                   config_status=registry.NOT_CONFIGURED)


# --- read helpers (read-only, guarded) -----------------------------------------------------------------

def _envs():
    from app.services.observability import catalog
    return catalog.list_environment_profiles()


def _services():
    from app.services.observability import catalog
    return catalog.list_services()


def _deploys():
    from app.services.observability import catalog
    return catalog.list_deployment_references()


def _deps():
    from app.services.observability import catalog
    return catalog.list_dependencies()


def _snapshots():
    from app.services.observability import health
    return health.list_runtime_snapshots(limit=50)


def _live_head():
    from app.services.observability.health import _expected_head
    return _expected_head()


# --- environment panels --------------------------------------------------------------------------------

def _environment_inventory(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"environment_domains": len(registry.ENVIRONMENT_REGISTRY),
                              "platform_domains": len(registry.PLATFORM_REGISTRY),
                              "deployment_topology_domains": len(registry.DEPLOYMENT_TOPOLOGY_REGISTRY),
                              "lifecycle_domains": len(registry.LIFECYCLE_REGISTRY),
                              "infrastructure_dependency_domains": len(registry.INFRASTRUCTURE_DEPENDENCY_REGISTRY),
                              "not_configured": len(nc),
                              "environment_metadata_is_not_live_infrastructure": True})
    except Exception:
        return _result(pdef, None, available=False)


def _environment_profiles(principal, pdef):
    try:
        envs = _envs()
        return _result(pdef, {"count": len(envs), "environment_metadata_not_live_infrastructure": True})
    except Exception:
        return _result(pdef, None, available=False)


def _environment_type_distribution(principal, pdef):
    try:
        envs = _envs()
        dist = Counter(e.get("environment") for e in envs)
        return _result(pdef, {"distribution": dict(dist), "count": len(envs)})
    except Exception:
        return _result(pdef, None, available=False)


def _active_environments(principal, pdef):
    try:
        envs = _envs()
        active = sum(1 for e in envs if e.get("active"))
        return _result(pdef, {"active": active, "inactive": len(envs) - active, "total": len(envs),
                              "active_flag_is_not_a_lifecycle_guarantee": True})
    except Exception:
        return _result(pdef, None, available=False)


def _environment_region_coverage(principal, pdef):
    try:
        envs = _envs()
        with_region = sum(1 for e in envs if e.get("region"))
        pct = round(with_region / len(envs) * 100, 1) if envs else 0.0
        return _result(pdef, {"with_region": with_region, "total": len(envs), "coverage_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _environment_runtime_readiness(principal, pdef):
    try:
        snaps = _snapshots()
        latest_by_env = {}
        for s in snaps:  # snapshots are ordered newest-first
            key = s.get("environment_profile_id")
            if key not in latest_by_env:
                latest_by_env[key] = s.get("summary")
        ready = sum(1 for v in latest_by_env.values() if v == "ready")
        return _result(pdef, {"environments_with_snapshot": len(latest_by_env), "ready": ready,
                              "snapshot_is_point_in_time_not_continuous_health": True})
    except Exception:
        return _result(pdef, None, available=False)


def _environment_health_summary(principal, pdef):
    try:
        from app.services.observability import service
        m = service.overview_metrics(principal)
        return _result(pdef, {"operational_services": m.get("operational_services"),
                              "degraded_services": m.get("degraded_services"),
                              "failed_health_checks": m.get("failed_health_checks"),
                              "not_a_per_environment_sla": True})
    except Exception:
        return _result(pdef, None, available=False)


def _environment_gaps(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


# --- platform panels -----------------------------------------------------------------------------------

def _platform_inventory(principal, pdef):
    try:
        svcs = _services()
        return _result(pdef, {"count": len(svcs), "logical_services_not_hosts": True})
    except Exception:
        return _result(pdef, None, available=False)


def _platform_type_distribution(principal, pdef):
    try:
        svcs = _services()
        return _result(pdef, {"distribution": dict(Counter(s.get("service_type") for s in svcs)),
                              "count": len(svcs)})
    except Exception:
        return _result(pdef, None, available=False)


def _platform_criticality_distribution(principal, pdef):
    try:
        svcs = _services()
        return _result(pdef, {"distribution": dict(Counter(s.get("criticality") for s in svcs)),
                              "count": len(svcs)})
    except Exception:
        return _result(pdef, None, available=False)


def _platform_status_summary(principal, pdef):
    try:
        svcs = _services()
        return _result(pdef, {"distribution": dict(Counter(s.get("status") for s in svcs)),
                              "count": len(svcs), "status_is_not_a_lifecycle_state": True})
    except Exception:
        return _result(pdef, None, available=False)


def _platform_ownership_coverage(principal, pdef):
    try:
        svcs = _services()
        owned = sum(1 for s in svcs if s.get("owner_user_id"))
        pct = round(owned / len(svcs) * 100, 1) if svcs else 0.0
        return _result(pdef, {"owned": owned, "total": len(svcs), "coverage_percent": pct,
                              "ownership_presence_only_no_identity": True})
    except Exception:
        return _result(pdef, None, available=False)


def _cloud_resource_inventory(principal, pdef):
    return _not_configured(pdef, "no authoritative cloud / server / container / VM owner exists in the platform")


# --- deployment topology panels ------------------------------------------------------------------------

def _deployment_reference_inventory(principal, pdef):
    try:
        deploys = _deploys()
        return _result(pdef, {"count": len(deploys),
                              "deployment_reference_is_not_a_deployment": True,
                              "deployment_execution_status": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


def _deployment_version_coverage(principal, pdef):
    try:
        deploys = _deploys()
        complete = sum(1 for d in deploys if d.get("version") and d.get("migration_head"))
        pct = round(complete / len(deploys) * 100, 1) if deploys else 0.0
        return _result(pdef, {"complete": complete, "total": len(deploys), "coverage_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _deployment_environment_mapping(principal, pdef):
    try:
        deploys = _deploys()
        mapped = sum(1 for d in deploys if d.get("environment_profile_id"))
        pct = round(mapped / len(deploys) * 100, 1) if deploys else 0.0
        return _result(pdef, {"mapped": mapped, "total": len(deploys), "coverage_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _deployment_migration_alignment(principal, pdef):
    try:
        deploys = _deploys()
        head = _live_head()
        with_head = [d for d in deploys if d.get("migration_head")]
        aligned = sum(1 for d in with_head if d.get("migration_head") == head)
        return _result(pdef, {"aligned": aligned, "with_recorded_head": len(with_head),
                              "live_head_present": bool(head),
                              "matching_head_does_not_prove_deployment_ran": True})
    except Exception:
        return _result(pdef, None, available=False)


def _deployment_execution_status(principal, pdef):
    return _not_configured(pdef, "no deployment-execution / rollout owner exists; a deployment reference is "
                                 "not a deployment")


# --- runtime landscape panels --------------------------------------------------------------------------

def _runtime_snapshot_coverage(principal, pdef):
    try:
        snaps = _snapshots()
        return _result(pdef, {"count": len(snaps), "point_in_time_not_continuous": True})
    except Exception:
        return _result(pdef, None, available=False)


def _runtime_migration_alignment(principal, pdef):
    try:
        snaps = _snapshots()
        latest = snaps[0] if snaps else None
        return _result(pdef, {"has_snapshot": bool(latest),
                              "latest_in_sync": bool(latest and latest.get("migration_in_sync")),
                              "clean_migration_is_not_app_health": True})
    except Exception:
        return _result(pdef, None, available=False)


def _runtime_configuration_coverage(principal, pdef):
    try:
        from app.services.runtime import consumption
        adoption = consumption.adoption_stats() if hasattr(consumption, "adoption_stats") else {}
        return _result(pdef, {"runtime_adoption_available": bool(adoption),
                              "configuration_values_never_exposed": True})
    except Exception:
        return _result(pdef, None, available=False)


def _runtime_landscape_summary(principal, pdef):
    try:
        envs = _envs()
        snaps = _snapshots()
        return _result(pdef, {"derived": True, "environments": len(envs), "runtime_snapshots": len(snaps),
                              "operational_visibility_not_certification": True,
                              "runtime_snapshot_is_not_continuous_health": True})
    except Exception:
        return _result(pdef, None, available=False)


# --- infrastructure dependency panels ------------------------------------------------------------------

def _dependency_graph_coverage(principal, pdef):
    try:
        deps = _deps()
        return _result(pdef, {"count": len(deps), "logical_graph_not_network_topology": True})
    except Exception:
        return _result(pdef, None, available=False)


def _dependency_type_distribution(principal, pdef):
    try:
        deps = _deps()
        return _result(pdef, {"distribution": dict(Counter(d.get("dependency_type") for d in deps)),
                              "count": len(deps)})
    except Exception:
        return _result(pdef, None, available=False)


def _integration_dependency_coverage(principal, pdef):
    try:
        from app.services.integration import service as integ
        m = integ.overview_metrics(principal)
        return _result(pdef, {"providers": m.get("providers"),
                              "connected_connectors": m.get("connected_connectors"),
                              "external_references_only_no_credentials": True})
    except Exception:
        return _result(pdef, None, available=False)


def _infrastructure_topology_availability(principal, pdef):
    return _not_configured(pdef, "no host-metadata / network-topology / cloud-resource owner exists; never "
                                 "private topology")


# --- lifecycle panels ----------------------------------------------------------------------------------

def _operational_lifecycle_state(principal, pdef):
    try:
        svcs = _services()
        envs = _envs()
        return _result(pdef, {"derived": True,
                              "service_status_distribution": dict(Counter(s.get("status") for s in svcs)),
                              "active_environments": sum(1 for e in envs if e.get("active")),
                              "formal_lifecycle_state": registry.NOT_CONFIGURED,
                              "status_is_a_proxy_not_a_formal_lifecycle": True})
    except Exception:
        return _result(pdef, None, available=False)


def _lifecycle_readiness(principal, pdef):
    """DERIVED operational lifecycle-readiness — deterministic, authoritative inputs, labeled derived.
    OPERATIONAL READINESS ONLY, never a certified lifecycle / retirement decision."""
    try:
        signals = {}
        try:
            snaps = _snapshots()
            signals["has_runtime_snapshot"] = bool(snaps)
            signals["latest_in_sync"] = bool(snaps and snaps[0].get("migration_in_sync"))
        except Exception:
            pass
        try:
            envs = _envs()
            signals["active_environments"] = sum(1 for e in envs if e.get("active"))
        except Exception:
            pass
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"derived": True, "operational_visibility_not_certification": True,
                              "self_signals": signals, "not_configured_domains": len(nc),
                              "active_flag_is_not_a_lifecycle_guarantee": True,
                              "deployment_reference_is_not_a_deployment": True})
    except Exception:
        return _result(pdef, None, available=False)


def _formal_lifecycle_state(principal, pdef):
    return _not_configured(pdef, "no formal lifecycle-state owner (planned / active / deprecated / retired) "
                                 "exists in the platform")


def _retirement_readiness(principal, pdef):
    return _not_configured(pdef, "no platform-retirement / decommission-schedule owner exists in the platform")


# --- governance + executive ----------------------------------------------------------------------------

def _configured_environment_domains(principal, pdef):
    try:
        total = len(registry._all_entries())
        cfg = len(registry.configured_domains())
        pct = round(cfg / total * 100, 1) if total else 0.0
        return _result(pdef, {"configured": cfg, "total": total, "coverage_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _unconfigured_environment_domains(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


def _environment_governance_status(principal, pdef):
    try:
        checked = clean = 0
        validators = (
            ("operational_resilience", "validate_operational_resilience"),
            ("change_management", "validate_change_management"),
            ("observability", None),
        )
        for mod, fn in validators:
            if fn is None:
                continue
            try:
                g = getattr(__import__(f"app.services.{mod}.governance", fromlist=[fn]), fn)()
                checked += 1
                if g.get("ok"):
                    clean += 1
            except Exception:
                pass
        return _result(pdef, {"checked": checked, "clean": clean,
                              "operational_visibility_not_certification": True})
    except Exception:
        return _result(pdef, None, available=False)


def _executive_platform_posture(principal, pdef):
    try:
        envs = _envs()
        svcs = _services()
        deploys = _deploys()
        configured = len(registry.configured_domains())
        not_configured = list(registry.not_configured_domains())
        return _result(pdef, {"derived": True, "operational_visibility_not_certification": True,
                              "environments": len(envs), "platforms": len(svcs),
                              "deployment_references": len(deploys),
                              "configured_domains": configured, "not_configured_domains": len(not_configured),
                              "environment_metadata_is_not_live_infrastructure": True,
                              "deployment_reference_is_not_a_deployment": True})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "environment_inventory": _environment_inventory,
    "environment_profiles": _environment_profiles,
    "environment_type_distribution": _environment_type_distribution,
    "active_environments": _active_environments,
    "environment_region_coverage": _environment_region_coverage,
    "environment_runtime_readiness": _environment_runtime_readiness,
    "environment_health_summary": _environment_health_summary,
    "environment_gaps": _environment_gaps,
    "platform_inventory": _platform_inventory,
    "platform_type_distribution": _platform_type_distribution,
    "platform_criticality_distribution": _platform_criticality_distribution,
    "platform_status_summary": _platform_status_summary,
    "platform_ownership_coverage": _platform_ownership_coverage,
    "cloud_resource_inventory": _cloud_resource_inventory,
    "deployment_reference_inventory": _deployment_reference_inventory,
    "deployment_version_coverage": _deployment_version_coverage,
    "deployment_environment_mapping": _deployment_environment_mapping,
    "deployment_migration_alignment": _deployment_migration_alignment,
    "deployment_execution_status": _deployment_execution_status,
    "runtime_snapshot_coverage": _runtime_snapshot_coverage,
    "runtime_migration_alignment": _runtime_migration_alignment,
    "runtime_configuration_coverage": _runtime_configuration_coverage,
    "runtime_landscape_summary": _runtime_landscape_summary,
    "dependency_graph_coverage": _dependency_graph_coverage,
    "dependency_type_distribution": _dependency_type_distribution,
    "integration_dependency_coverage": _integration_dependency_coverage,
    "infrastructure_topology_availability": _infrastructure_topology_availability,
    "operational_lifecycle_state": _operational_lifecycle_state,
    "lifecycle_readiness": _lifecycle_readiness,
    "formal_lifecycle_state": _formal_lifecycle_state,
    "retirement_readiness": _retirement_readiness,
    "configured_environment_domains": _configured_environment_domains,
    "unconfigured_environment_domains": _unconfigured_environment_domains,
    "environment_governance_status": _environment_governance_status,
    "executive_platform_posture": _executive_platform_posture,
}


def compute_panel(principal, key):
    """Compose one panel by key. Read-only, fail-closed, self-restricting. Returns a PanelResult, or None if
    the panel is not registered / not explainable."""
    pdef = registry.panel(key)
    fn = _COMPUTE.get(key)
    if pdef is None or fn is None:
        return None
    try:
        entitled = principal.can(pdef.permission)
    except Exception:
        entitled = False
    if not entitled:
        stats.note("restricted_panels")
        return _restricted(pdef)
    try:
        result = fn(principal, pdef)
    except Exception:
        stats.note("aggregation_failures", panel=key)
        return None
    if result is None or not result.is_explainable:
        stats.note("missing_explainability", panel=key)
        return None
    stats.note("panels_composed")
    return result
