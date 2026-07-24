"""Business Continuity registries (Phase D.55) — the declarative catalogs of the business-continuity layer.

Four frozen, declarative catalogs; the layer owns NO persistence and defines NO new backup platform,
monitoring system, disaster-recovery engine, scheduler, notification system, or incident manager:

  * RESILIENCE_REGISTRY — every resilience domain (backup, restore, disaster recovery, high availability,
    infrastructure, monitoring, runtime, maintenance, notifications). Each names its authoritative owner,
    health owner (the runtime health proxy), monitoring owner, runtime gate, and deep links. Backup / restore
    / DR have NO authoritative owner in the platform today — they are declared here as metadata-only domains
    whose live health is proxied by the Observability domain (the D.50/OCR precedent).
  * RECOVERY_REGISTRY — every recovery asset (database, file storage, documents, configuration, analytics,
    identity, communications, integrations). Each names its owner, backup owner, restore owner, RPO, RTO, and
    runtime gate.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * CONTINUITY_DASHBOARDS — every continuity dashboard (owner, audience, runtime gate, panel list, required
    capabilities, navigation, refresh, governing services).

Governance verifies every resilience domain + recovery asset is registered, every panel names an
authoritative owner + source + deep link, and that this layer never becomes a second backup / monitoring / DR
/ scheduler / notification / incident system.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


# --- resilience registry -----------------------------------------------------

@dataclass(frozen=True)
class ResilienceDomain:
    key: str
    label: str
    authoritative_owner: str   # the authoritative owner (or "not_configured" for declarative-only domains)
    health_owner: str          # the authoritative live-health proxy owner (never re-implemented)
    monitoring_owner: str      # the authoritative monitoring owner
    runtime_gate: str
    deep_links: tuple


def _res(key, label, authoritative_owner, deep_links, *, health_owner="observability",
         monitoring_owner="observability", runtime_gate="resilience.enabled"):
    return ResilienceDomain(key, label, authoritative_owner, health_owner, monitoring_owner, runtime_gate,
                            tuple(deep_links))


RESILIENCE_REGISTRY = (
    _res("backup", "Backup", "not_configured", ("/business-continuity", "/observability")),
    _res("restore", "Restore", "not_configured", ("/business-continuity", "/observability")),
    _res("disaster_recovery", "Disaster Recovery", "not_configured",
         ("/business-continuity", "/observability")),
    _res("high_availability", "High Availability", "runtime.coordination",
         ("/observability", "/runtime"), health_owner="runtime.coordination"),
    _res("infrastructure", "Infrastructure", "observability.catalog", ("/observability",),
         health_owner="observability.catalog"),
    _res("monitoring", "Monitoring", "observability", ("/observability",)),
    _res("runtime", "Runtime", "runtime.service", ("/runtime", "/observability"),
         health_owner="runtime.service"),
    _res("maintenance", "Maintenance", "observability.alerts", ("/observability",),
         health_owner="observability.alerts"),
    _res("notifications", "Notifications", "communications", ("/communications",),
         health_owner="communications"),
)

_RES_BY_KEY = {r.key: r for r in RESILIENCE_REGISTRY}


# --- recovery registry -------------------------------------------------------

@dataclass(frozen=True)
class RecoveryAsset:
    key: str
    label: str
    owner: str                 # the authoritative owner of the asset (never re-implemented)
    backup_owner: str          # the authoritative backup owner (or "not_configured")
    restore_owner: str         # the authoritative restore owner (or "not_configured")
    rpo: str                   # recovery point objective (declarative target)
    rto: str                   # recovery time objective (declarative target)
    runtime_gate: str = "recovery.enabled"


def _rec(key, label, owner, rpo, rto, *, backup_owner="not_configured", restore_owner="not_configured"):
    return RecoveryAsset(key, label, owner, backup_owner, restore_owner, rpo, rto)


RECOVERY_REGISTRY = (
    _rec("database", "Database", "postgres", "1 hour", "4 hours"),
    _rec("file_storage", "File Storage", "document_platform", "24 hours", "8 hours"),
    _rec("documents", "Documents", "document_platform", "24 hours", "8 hours"),
    _rec("configuration", "Configuration", "configuration", "24 hours", "2 hours"),
    _rec("analytics", "Analytics", "analytics", "24 hours", "8 hours"),
    _rec("identity", "Identity", "identity", "1 hour", "2 hours"),
    _rec("communications", "Communications", "communications", "24 hours", "8 hours"),
    _rec("integrations", "Integrations", "integration", "24 hours", "8 hours"),
)

_REC_BY_KEY = {a.key: a for a in RECOVERY_REGISTRY}


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str                 # authoritative owning service
    source: str                # the authoritative read the value is composed from
    measure: str
    unit: str
    viz: str
    permission: str            # capability required to see the panel value (else restricted)
    deep_link: str             # the authoritative resilience-owner surface to drill into
    explainability: str
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    refresh, lifecycle)


PANEL_REGISTRY = (
    # backup status (declarative — no authoritative backup owner exists; reported honestly)
    _p("last_successful_backup", "business_continuity", "business_continuity.registry", "backup", "status",
       "card", "observability.view", "/business-continuity",
       "Last successful backup status. No authoritative backup owner is configured in the platform; this "
       "panel reports the declared backup domain status honestly. No second backup platform."),
    _p("failed_backups", "business_continuity", "business_continuity.registry", "backup", "status", "card",
       "observability.view", "/business-continuity",
       "Failed-backup status. No authoritative backup owner is configured; reported as not-configured "
       "rather than fabricated."),
    _p("backup_coverage", "business_continuity", "business_continuity.registry", "backup", "count", "chart",
       "observability.view", "/business-continuity",
       "Recovery assets with a declared backup owner, from the recovery registry."),
    # recovery readiness
    _p("recovery_assets", "business_continuity", "business_continuity.registry", "recovery", "count", "list",
       "observability.view", "/business-continuity",
       "The registered recovery-asset catalog — each naming its owner + backup/restore owner + RPO/RTO."),
    _p("rpo_targets", "business_continuity", "business_continuity.registry", "recovery", "count", "chart",
       "observability.view", "/business-continuity",
       "Declared recovery-point objectives (RPO) by asset, from the recovery registry."),
    _p("rto_targets", "business_continuity", "business_continuity.registry", "recovery", "count", "chart",
       "observability.view", "/business-continuity",
       "Declared recovery-time objectives (RTO) by asset, from the recovery registry."),
    # restore validation
    _p("restore_test_status", "business_continuity", "business_continuity.registry", "restore", "status",
       "card", "observability.view", "/business-continuity",
       "Restore-test validation status. No authoritative restore owner is configured; reported honestly."),
    _p("recovery_documentation", "business_continuity", "business_continuity.registry", "restore", "count",
       "card", "observability.view", "/business-continuity",
       "Recovery documentation coverage (resilience domains + recovery assets declared)."),
    _p("resilience_domains", "business_continuity", "business_continuity.registry", "restore", "count", "list",
       "observability.view", "/business-continuity",
       "The registered resilience-domain catalog — each naming its authoritative / health / monitoring "
       "owner."),
    # infrastructure health
    _p("infrastructure_availability", "observability.catalog", "observability.catalog.metrics",
       "infrastructure", "count", "gauge", "observability.view", "/observability",
       "Infrastructure availability (operational vs degraded services), from the Observability service "
       "catalog. No second monitoring system."),
    _p("health_checks", "observability.health", "observability.health.metrics", "infrastructure", "count",
       "card", "observability.view", "/observability",
       "Failed health checks + diagnostic failures, from the Observability health domain."),
    _p("service_incidents", "observability.incidents", "observability.incidents.metrics", "infrastructure",
       "count", "card", "observability.view", "/observability",
       "Open reliability incidents + findings, from the Observability incidents domain. No second incident "
       "manager."),
    # runtime resilience
    _p("runtime_health", "runtime.service", "runtime.service.overview_metrics", "runtime", "count", "card",
       "observability.view", "/runtime",
       "Runtime readiness (validation ok + issue count + evaluations), from the Runtime engine."),
    _p("cluster_health", "runtime.coordination", "runtime.coordination.cluster_state", "runtime", "count",
       "card", "observability.view", "/runtime",
       "Runtime cluster health (active / stale workers, converged), from the Runtime coordination owner."),
    _p("runtime_adoption", "runtime", "runtime.consumption.adoption_stats", "runtime", "percent", "gauge",
       "observability.view", "/observability",
       "Runtime adoption + fallback rate, from the Runtime consumption counters."),
    # maintenance
    _p("scheduled_maintenance", "observability.alerts", "observability.alerts.list_maintenance_windows",
       "maintenance", "count", "card", "observability.view", "/observability",
       "Scheduled / active maintenance windows, from the Observability alerts domain."),
    _p("open_alerts", "observability.alerts", "observability.alerts.metrics", "maintenance", "count", "card",
       "observability.view", "/observability",
       "Open alerts, from the Observability alerts domain."),
    _p("scheduled_jobs", "automation", "automation.service.metrics", "maintenance", "count", "chart",
       "observability.view", "/automation",
       "Scheduled-job health (jobs / running / failed), from the Automation scheduler. No second scheduler."),
    # notifications
    _p("notification_health", "communications", "communications.service.metrics", "notifications", "count",
       "card", "observability.view", "/communications",
       "Notification / messaging health (sent / messages), from Communications. No second notification "
       "system."),
    _p("notification_activity", "communications", "communications.service.metrics", "notifications", "count",
       "card", "observability.view", "/communications",
       "Open conversations, from Communications."),
    # operational readiness
    _p("resilience_score", "business_continuity", "business_continuity.compose", "readiness", "percent",
       "gauge", "observability.view", "/business-continuity",
       "Deterministic operational-readiness indicator (operational services vs degraded + failed checks + "
       "open incidents) — composed from the Observability overview. Advisory only; never mutates."),
    _p("observability_overview", "observability", "observability.service.overview_metrics", "readiness",
       "count", "card", "observability.view", "/observability",
       "Firm operational overview (services / health / alerts / incidents), from the Observability domain."),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # resilience | operations | executive
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


CONTINUITY_DASHBOARDS = (
    _d("backup_status", "business_continuity", "resilience", "resilience.enabled",
       ("last_successful_backup", "failed_backups", "backup_coverage"),
       ("observability.view",), "/business-continuity?dashboard=backup_status", ("business_continuity",)),
    _d("recovery_readiness", "business_continuity", "resilience", "recovery.enabled",
       ("recovery_assets", "rpo_targets", "rto_targets"),
       ("observability.view",), "/business-continuity?dashboard=recovery_readiness", ("business_continuity",)),
    _d("restore_validation", "business_continuity", "resilience", "recovery.enabled",
       ("restore_test_status", "recovery_documentation", "resilience_domains"),
       ("observability.view",), "/business-continuity?dashboard=restore_validation", ("business_continuity",)),
    _d("infrastructure_health", "business_continuity", "operations", "resilience.enabled",
       ("infrastructure_availability", "health_checks", "service_incidents"),
       ("observability.view",), "/business-continuity?dashboard=infrastructure_health", ("observability",)),
    _d("runtime_resilience", "business_continuity", "operations", "resilience.enabled",
       ("runtime_health", "cluster_health", "runtime_adoption"),
       ("observability.view",), "/business-continuity?dashboard=runtime_resilience", ("runtime",)),
    _d("maintenance", "business_continuity", "operations", "resilience.enabled",
       ("scheduled_maintenance", "open_alerts", "scheduled_jobs"),
       ("observability.view",), "/business-continuity?dashboard=maintenance", ("observability", "automation")),
    _d("notifications", "business_continuity", "operations", "resilience.enabled",
       ("notification_health", "notification_activity", "open_alerts"),
       ("observability.view",), "/business-continuity?dashboard=notifications",
       ("communications", "observability")),
    _d("operational_readiness", "business_continuity", "executive", "continuity.enabled",
       ("resilience_score", "observability_overview", "infrastructure_availability"),
       ("observability.view",), "/business-continuity?dashboard=operational_readiness",
       ("observability", "runtime")),
)

_DASH_BY_KEY = {d.key: d for d in CONTINUITY_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def resilience_domain(key) -> ResilienceDomain | None:
    return _RES_BY_KEY.get(key)


def recovery_asset(key) -> RecoveryAsset | None:
    return _REC_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def resilience_registered(key) -> bool:
    return key in _RES_BY_KEY


def recovery_registered(key) -> bool:
    return key in _REC_BY_KEY


def coverage() -> dict:
    return {
        "resilience_domains": len(RESILIENCE_REGISTRY),
        "recovery_assets": len(RECOVERY_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(CONTINUITY_DASHBOARDS),
    }
