"""Enterprise Change Management registries (Phase D.63) — the declarative catalogs of the change / release /
configuration / evidence composition layer.

Six frozen, declarative catalogs; the layer owns NO persistence and defines NO second ITSM platform,
change-management system, deployment platform, CI/CD system, Git repository, CMDB, feature-flag engine,
release-approval system, incident platform, maintenance scheduler, or architecture-decision system:

  * CHANGE_DOMAIN_REGISTRY — the change domains (application code, database schema, runtime configuration,
    feature flags, security config, integrations, infrastructure, documentation, compliance rules, workflow
    definitions, automation rules, vendor config, client-facing behavior, reporting/analytics, data-governance
    rules), each naming its authoritative owner, source repo/service, read surface, prohibited mutation
    surface, evidence source, capabilities, runtime gate, deep links, and config status.
  * RELEASE_REGISTRY — release-evidence entries (release line, migration head, route/ADR/section/dashboard
    counts, documentation status — SELF-VERIFIABLE; branch, pull request, merge commit, version tag, CI
    status, deployment status, rollback artifact, production-verification — NOT_CONFIGURED, no live
    git/CI/deployment owner). References release evidence only; never creates a release or alters Git state.
  * CONFIGURATION_REGISTRY — configuration domains (runtime flags, policy settings, route registration,
    capability inventory, integration/observability/security/automation config, document classifications,
    analytics registration, migration state, environment metadata, maintenance config), each with a
    sensitivity classification. Exposes counts / status / drift / verification metadata only — never a
    sensitive configuration value.
  * CHANGE_EVIDENCE_REGISTRY — change-evidence entries (CI build / E2E / documentation-advisory / architecture
    guard / regression / code-quality — produced by the CI pipeline; migration-head / route-count / ADR
    verification — self-verified live; governance / security-scan / maintenance / incident-correlation —
    composed; deployment verification / runtime smoke test / rollback test / production sign-off / PR approval
    / release notes / post-change review — NOT_CONFIGURED). References evidence only; never generates or
    certifies evidence.
  * PANEL_REGISTRY — every dashboard panel. * CHANGE_DASHBOARDS — every change dashboard.

Governance verifies every registry key is unique, every configured entry names an authoritative owner, every
panel names an authoritative owner + source + deep link, every derived value is labeled, and that this layer
never becomes a second ITSM / change / deployment / CI/CD / repository / CMDB / feature-flag / approval system.
Where no authoritative owner exists (deployment, rollback, production verification, change calendar, live git /
PR, live CI status), the entry is declared `not_configured` and reported honestly — never a fabricated change
request, deployment status, release approval, rollback readiness, configuration state, production verification,
environment health, or change success. **A green build is not production certification, a merged pull request
is not deployment, and an absent incident is not change success.**
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"
CONFIGURED = "configured"


# --- change domain registry --------------------------------------------------

@dataclass(frozen=True)
class ChangeDomain:
    key: str
    label: str
    owner: str                 # authoritative owner (or "not_configured")
    source: str                # source repository or service
    read_surface: str          # the authoritative read
    mutation_surface: str      # the prohibited mutation surface (never called)
    evidence_source: str       # where change evidence lives
    capabilities: tuple
    runtime_gate: str
    deep_links: tuple
    config_status: str = CONFIGURED


def _cd(key, label, owner, source, read_surface, mutation_surface, evidence_source, deep_links, *,
        capabilities=("observability.view",), runtime_gate="change_management.enabled",
        config_status=CONFIGURED):
    return ChangeDomain(key, label, owner, source, read_surface, mutation_surface, evidence_source,
                        tuple(capabilities), runtime_gate, tuple(deep_links), config_status)


CHANGE_DOMAIN_REGISTRY = (
    _cd("application_code", "Application Code", "continuous_integration", "360WealthConsulting/Client360",
        "architecture_manifest + app.routes", "git push / merge", "ci_build + architecture_guards",
        ("/change-management",)),
    _cd("database_schema", "Database Schema", "observability.health", "migrations/",
        "observability.health.list_runtime_snapshots", "alembic upgrade", "migration_head_verification",
        ("/observability", "/change-management")),
    _cd("runtime_configuration", "Runtime Configuration", "runtime", "runtime.consumption",
        "runtime.consumption.adoption_stats", "set_flag", "runtime_gate_coverage", ("/runtime",)),
    _cd("feature_flags", "Feature Flags", "runtime", "runtime.consumption",
        "runtime.consumption.feature_enabled", "set_flag", "runtime_gate_coverage", ("/runtime",)),
    _cd("security_configuration", "Security Configuration", "security_operations", "security",
        "security_operations.security_summary", "security.manage", "security_scan",
        ("/security-operations",), capabilities=("security.view",)),
    _cd("integrations", "Integrations", "integration.service", "integration",
        "integration.service.overview_metrics", "create_connector", "integration_configuration",
        ("/integration",), capabilities=("integration.view",)),
    _cd("infrastructure", "Infrastructure", "observability.catalog", "observability",
        "observability.catalog.list_environment_profiles", "create_environment_profile",
        "environment_metadata", ("/observability",)),
    _cd("documentation", "Documentation", "knowledge_management", "docs/",
        "knowledge_management.knowledge_summary", "create_document", "documentation_status",
        ("/knowledge-management", "/documents"), capabilities=("documents.view",)),
    _cd("compliance_rules", "Compliance Rules", "compliance_rule_catalog", "compliance",
        "compliance.rule_catalog.list_rules", "record_decision", "governance_result", ("/supervision",),
        capabilities=("compliance.supervise",)),
    _cd("workflow_definitions", "Workflow Definitions", "automation_orchestration", "workflow_automation",
        "automation_orchestration.automation_summary", "create_template", "governance_result",
        ("/automation",), capabilities=("automation.view",)),
    _cd("automation_rules", "Automation Rules", "automation_orchestration", "automation",
        "automation_orchestration.automation_summary", "create_job", "governance_result", ("/automation",),
        capabilities=("automation.view",)),
    _cd("vendor_configuration", "Vendor Configuration", "vendor_management", "integration.connectors",
        "vendor_management.vendor_summary", "create_provider", "governance_result", ("/vendor-management",),
        capabilities=("integration.view",)),
    _cd("client_facing_behavior", "Client-Facing Behavior", "integration_hub", "integration_hub",
        "integration_hub.integration_summary", "run_sync", "governance_result", ("/integration-hub",),
        capabilities=("integration.view",)),
    _cd("reporting_analytics", "Reporting & Analytics", "analytics.metrics", "analytics",
        "analytics.metrics.list_metrics", "n/a", "analytics_registration", ("/executive",),
        capabilities=("analytics.executive",)),
    _cd("data_governance_rules", "Data-Governance Rules", "data_governance", "governance",
        "data_governance.governance_summary", "create_retention_assignment", "governance_result",
        ("/data-governance",), capabilities=("governance.view",)),
)

_CD_BY_KEY = {c.key: c for c in CHANGE_DOMAIN_REGISTRY}


# --- release registry --------------------------------------------------------

@dataclass(frozen=True)
class ReleaseEntry:
    key: str
    label: str
    owner: str
    read_surface: str
    evidence_source: str
    expected_verification: str
    capabilities: tuple
    runtime_gate: str
    deep_links: tuple
    config_status: str = CONFIGURED


def _re(key, label, owner, read_surface, evidence_source, expected_verification, deep_links, *,
        capabilities=("observability.view",), runtime_gate="release_governance.enabled",
        config_status=CONFIGURED):
    return ReleaseEntry(key, label, owner, read_surface, evidence_source, expected_verification,
                        tuple(capabilities), runtime_gate, tuple(deep_links), config_status)


RELEASE_REGISTRY = (
    _re("release_line", "Release Line", "architecture_manifest", "platform_architecture_manifest.yaml",
        "manifest.meta", "release/0.13.0", ("/change-management",)),
    _re("migration_head", "Migration Head", "observability.health", "observability.health._expected_head",
        "migration_head_verification", "single alembic head", ("/observability",)),
    _re("route_count", "Route Count", "architecture_manifest", "app.routes + manifest.meta.route_count",
        "route_count_verification", "live == manifest", ("/change-management",)),
    _re("adr_count", "ADR Count", "architecture_manifest", "docs/adr + manifest",
        "adr_count_verification", "sequential ADRs", ("/change-management",)),
    _re("client360_section_count", "Client 360 Section Count", "client360",
        "client360.registry.SECTIONS", "client360_section_count_verification", "guarded count",
        ("/change-management",)),
    _re("executive_dashboard_count", "Executive Dashboard Count", "executive_intelligence",
        "executive_intelligence.registry.DASHBOARD_REGISTRY", "executive_dashboard_count_verification",
        "guarded count", ("/executive",)),
    _re("documentation_status", "Documentation Status", "knowledge_management",
        "knowledge_management.knowledge_summary", "documentation_status", "docs advisory",
        ("/knowledge-management",), capabilities=("documents.view",)),
    _re("branch", "Branch (live git)", NOT_CONFIGURED, "n/a", "n/a", "n/a", ("/change-management",),
        config_status=NOT_CONFIGURED),
    _re("pull_request", "Pull Request (live git)", NOT_CONFIGURED, "n/a", "n/a", "n/a",
        ("/change-management",), config_status=NOT_CONFIGURED),
    _re("merge_commit", "Merge Commit (live git)", NOT_CONFIGURED, "n/a", "n/a", "n/a",
        ("/change-management",), config_status=NOT_CONFIGURED),
    _re("version_tag", "Version Tag (live git)", NOT_CONFIGURED, "n/a", "n/a", "n/a",
        ("/change-management",), config_status=NOT_CONFIGURED),
    _re("ci_status", "CI Status (live)", NOT_CONFIGURED, "n/a", "ci_build_result (per-commit, not live-read)",
        "n/a", ("/change-management",), config_status=NOT_CONFIGURED),
    _re("deployment_status", "Deployment Status", NOT_CONFIGURED, "n/a", "n/a", "n/a",
        ("/change-management",), config_status=NOT_CONFIGURED),
    _re("rollback_artifact", "Rollback Artifact", NOT_CONFIGURED, "n/a", "n/a", "n/a",
        ("/change-management",), config_status=NOT_CONFIGURED),
    _re("production_verification_status", "Production-Verification Status", NOT_CONFIGURED, "n/a", "n/a", "n/a",
        ("/change-management",), config_status=NOT_CONFIGURED),
)

_RE_BY_KEY = {r.key: r for r in RELEASE_REGISTRY}


# --- configuration registry --------------------------------------------------

@dataclass(frozen=True)
class ConfigurationEntry:
    key: str
    label: str
    owner: str
    source: str
    scope: str                 # firm | environment | record
    sensitivity: str           # operational | sensitive
    verification_owner: str
    capabilities: tuple
    runtime_gate: str
    deep_links: tuple
    config_status: str = CONFIGURED


def _cfg(key, label, owner, source, verification_owner, deep_links, *, scope="firm", sensitivity="operational",
         capabilities=("observability.view",), runtime_gate="configuration_intelligence.enabled",
         config_status=CONFIGURED):
    return ConfigurationEntry(key, label, owner, source, scope, sensitivity, verification_owner,
                              tuple(capabilities), runtime_gate, tuple(deep_links), config_status)


CONFIGURATION_REGISTRY = (
    _cfg("runtime_flags", "Runtime Flags", "runtime", "runtime.consumption", "runtime", ("/runtime",)),
    _cfg("policy_settings", "Policy Settings", "policy", "policy.evaluate", "policy", ("/runtime",)),
    _cfg("route_registration", "Route Registration", "architecture_manifest", "app.routes",
         "route_count_verification", ("/change-management",)),
    _cfg("capability_inventory", "Capability Inventory", "identity", "capabilities", "identity",
         ("/admin",), sensitivity="sensitive"),
    _cfg("integration_configuration", "Integration Configuration", "integration.service", "integration",
         "integration.service", ("/integration",), sensitivity="sensitive",
         capabilities=("integration.view",)),
    _cfg("observability_configuration", "Observability Configuration", "observability.catalog",
         "observability", "observability.catalog", ("/observability",)),
    _cfg("security_configuration", "Security Configuration", "security_operations", "security",
         "security_operations", ("/security-operations",), sensitivity="sensitive",
         capabilities=("security.view",)),
    _cfg("automation_configuration", "Automation Configuration", "automation_orchestration", "automation",
         "automation_orchestration", ("/automation",), capabilities=("automation.view",)),
    _cfg("document_classifications", "Document Classifications", "document_platform", "document_platform",
         "document_platform", ("/documents",), capabilities=("documents.view",)),
    _cfg("analytics_registration", "Analytics Registration", "analytics.metrics", "analytics",
         "analytics.metrics", ("/executive",), capabilities=("analytics.executive",)),
    _cfg("migration_state", "Migration State", "observability.health", "alembic_version",
         "migration_head_verification", ("/observability",)),
    _cfg("environment_metadata", "Environment Metadata", "observability.catalog", "observability",
         "observability.catalog", ("/observability",), scope="environment", sensitivity="sensitive"),
    _cfg("maintenance_configuration", "Maintenance Configuration", "observability.alerts", "observability",
         "observability.alerts", ("/observability",)),
)

_CFG_BY_KEY = {c.key: c for c in CONFIGURATION_REGISTRY}


# --- change evidence registry ------------------------------------------------

@dataclass(frozen=True)
class ChangeEvidence:
    key: str
    label: str
    owner: str
    source: str
    verification_owner: str
    freshness: str             # per_commit | continuous | live | not_tracked
    capabilities: tuple
    runtime_gate: str
    deep_link: str
    config_status: str = CONFIGURED


def _ev(key, label, owner, source, verification_owner, deep_link, *, freshness="per_commit",
        capabilities=("observability.view",), runtime_gate="deployment_evidence.enabled",
        config_status=CONFIGURED):
    return ChangeEvidence(key, label, owner, source, verification_owner, freshness, tuple(capabilities),
                          runtime_gate, deep_link, config_status)


CHANGE_EVIDENCE_REGISTRY = (
    _ev("ci_build_result", "CI Build Result", "continuous_integration", "github_actions:build",
        "continuous_integration", "/change-management?dashboard=ci_verification"),
    _ev("e2e_result", "E2E Result", "continuous_integration", "github_actions:e2e", "continuous_integration",
        "/change-management?dashboard=ci_verification"),
    _ev("documentation_advisory_result", "Documentation-Advisory Result", "continuous_integration",
        "github_actions:documentation-advisory", "continuous_integration",
        "/change-management?dashboard=ci_verification"),
    _ev("architecture_guard_result", "Architecture-Guard Result", "continuous_integration",
        "tests.test_platform_architecture", "continuous_integration",
        "/change-management?dashboard=ci_verification"),
    _ev("governance_result", "Governance Result", "change_management", "change_management.compose",
        "change_management", "/change-management", freshness="live"),
    _ev("migration_head_verification", "Migration-Head Verification", "observability.health",
        "observability.health._expected_head", "observability.health", "/observability", freshness="live"),
    _ev("route_count_verification", "Route-Count Verification", "architecture_manifest",
        "app.routes + manifest", "change_management", "/change-management", freshness="live"),
    _ev("adr_verification", "ADR Verification", "architecture_manifest", "docs/adr + manifest",
        "change_management", "/change-management", freshness="live"),
    _ev("regression_result", "Regression Result", "continuous_integration", "github_actions:pytest",
        "continuous_integration", "/change-management?dashboard=ci_verification"),
    _ev("code_quality_result", "Code-Quality Result", "continuous_integration", "github_actions:ruff",
        "continuous_integration", "/change-management?dashboard=ci_verification"),
    _ev("security_scan", "Security Scan", "security_operations", "security_operations.security_summary",
        "security_operations", "/security-operations", freshness="continuous", capabilities=("security.view",)),
    _ev("maintenance_window_record", "Maintenance-Window Record", "observability.alerts",
        "observability.alerts.metrics", "observability.alerts", "/observability", freshness="continuous"),
    _ev("incident_correlation", "Incident Correlation", "observability.incidents",
        "observability.incidents.metrics", "observability.incidents", "/observability", freshness="continuous"),
    _ev("pull_request_approval", "Pull-Request Approval (live git)", NOT_CONFIGURED, "n/a", "n/a",
        "/change-management", freshness="not_tracked", config_status=NOT_CONFIGURED),
    _ev("deployment_verification", "Deployment Verification", NOT_CONFIGURED, "n/a", "n/a",
        "/change-management", freshness="not_tracked", config_status=NOT_CONFIGURED),
    _ev("runtime_smoke_test", "Runtime Smoke Test", NOT_CONFIGURED, "n/a", "n/a", "/change-management",
        freshness="not_tracked", config_status=NOT_CONFIGURED),
    _ev("rollback_test", "Rollback Test", NOT_CONFIGURED, "n/a", "n/a", "/change-management",
        freshness="not_tracked", config_status=NOT_CONFIGURED),
    _ev("production_signoff", "Production Sign-Off", NOT_CONFIGURED, "n/a", "n/a", "/change-management",
        freshness="not_tracked", config_status=NOT_CONFIGURED),
    _ev("release_notes", "Release Notes (live git)", NOT_CONFIGURED, "n/a", "n/a", "/change-management",
        freshness="not_tracked", config_status=NOT_CONFIGURED),
    _ev("post_change_review", "Post-Change Review", NOT_CONFIGURED, "n/a", "n/a", "/change-management",
        freshness="not_tracked", config_status=NOT_CONFIGURED),
)

_EV_BY_KEY = {e.key: e for e in CHANGE_EVIDENCE_REGISTRY}


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
    # change-domain catalog (DERIVED)
    _p("change_domain_inventory", "change_management", "change_management.registry", "change", "count", "list",
       "observability.view", "/change-management",
       "The registered change-domain catalog — each naming its authoritative owner + source + prohibited "
       "mutation surface + evidence source + config status. Metadata only.", derived=True),
    _p("configured_change_domains", "change_management", "change_management.registry", "change", "coverage",
       "gauge", "observability.view", "/change-management",
       "Configured vs not_configured change-domain coverage — a DERIVED coverage summary.", derived=True),
    _p("unconfigured_change_domains", "change_management", "change_management.registry", "change", "list",
       "list", "observability.view", "/change-management",
       "Change / release / configuration / evidence areas with no authoritative owner (deployment, rollback, "
       "production verification, change calendar, live git / PR / CI) — reported honestly.", derived=True),
    # release / self-verification
    _p("current_release_line", "architecture_manifest", "change_management.manifest", "release", "status",
       "card", "observability.view", "/change-management",
       "The declared release line + migration head + capability count, from the architecture manifest "
       "(release evidence). The layer never alters Git state; branch is a live-git concept (not_configured)."),
    _p("open_pull_requests", "not_configured", "change_management.registry", "release", "status", "card",
       "observability.view", "/change-management",
       "Open pull requests (live git). " + _NC_NOTE, derived=True),
    _p("merge_status", "not_configured", "change_management.registry", "release", "status", "card",
       "observability.view", "/change-management",
       "Merge status (live git). " + _NC_NOTE + " Merged is not deployed.", derived=True),
    _p("merge_commit", "not_configured", "change_management.registry", "release", "hash", "card",
       "observability.view", "/change-management",
       "Merge commit hash (live git). " + _NC_NOTE, derived=True),
    _p("release_version", "not_configured", "change_management.registry", "release", "status", "card",
       "observability.view", "/change-management",
       "Release version tag (live git). " + _NC_NOTE + " A version tag does not prove rollout.", derived=True),
    # CI evidence (produced per-commit by the CI pipeline; not live-read)
    _p("ci_build_status", "continuous_integration", "change_management.registry", "ci", "status", "card",
       "observability.view", "/change-management?dashboard=ci_verification",
       "CI build evidence (produced per-commit by the CI pipeline; not live-read from the app). A green build "
       "does not certify production readiness."),
    _p("e2e_status", "continuous_integration", "change_management.registry", "ci", "status", "card",
       "observability.view", "/change-management?dashboard=ci_verification",
       "E2E evidence (produced per-commit by the CI pipeline; not live-read). Not a production certification."),
    _p("documentation_status", "knowledge_management", "knowledge_management.knowledge_summary", "ci",
       "coverage", "card", "documents.view", "/knowledge-management",
       "Documentation status (completeness), from the D.62 Knowledge Management layer + the CI "
       "documentation-advisory evidence."),
    _p("architecture_guard_status", "continuous_integration", "change_management.registry", "verification",
       "status", "card", "observability.view", "/change-management?dashboard=ci_verification",
       "Architecture-guard evidence (the CI architecture-guard suite; produced per-commit). Not a production "
       "certification."),
    _p("governance_status", "change_management", "change_management.compose", "verification", "count", "card",
       "observability.view", "/change-management",
       "Composed governance status across the D.55–D.62 read-only layers — a DERIVED count of clean vs failing "
       "governance checkers. Operational readiness, never approval.", derived=True),
    _p("regression_status", "continuous_integration", "change_management.registry", "ci", "status", "card",
       "observability.view", "/change-management?dashboard=ci_verification",
       "Regression evidence (the full pytest suite; produced per-commit by CI; not live-read). Not a "
       "production certification."),
    _p("code_quality_status", "continuous_integration", "change_management.registry", "ci", "status", "card",
       "observability.view", "/change-management?dashboard=ci_verification",
       "Code-quality evidence (the Ruff gate; produced per-commit by CI; not live-read)."),
    # migration + counts (self-verified live)
    _p("migration_head_status", "observability.health", "observability.health._expected_head", "migration",
       "verification", "card", "observability.view", "/observability",
       "Migration-head status — the live Alembic script head vs the manifest-declared head (a live "
       "self-verification). A clean migration check does not prove application health."),
    _p("migration_head_count", "observability.health", "observability.health._expected_head", "migration",
       "count", "card", "observability.view", "/observability",
       "Migration-head count — a single Alembic head is expected (multiple heads = drift). Live "
       "self-verification."),
    _p("route_count_verification", "architecture_manifest", "change_management.compose", "verification",
       "verification", "gauge", "observability.view", "/change-management",
       "Route-count verification — the live `len(app.routes)` vs the manifest-declared route_count. A DERIVED "
       "drift check.", derived=True),
    _p("adr_count_verification", "architecture_manifest", "change_management.compose", "verification",
       "verification", "gauge", "observability.view", "/change-management",
       "ADR-count verification — the live count of `docs/adr/ADR-*.md` vs the expected sequential count. A "
       "DERIVED drift check.", derived=True),
    _p("client360_section_count_verification", "client360", "change_management.compose", "verification",
       "count", "card", "observability.view", "/change-management",
       "Client 360 section-count verification — the live `len(SECTIONS)`. A DERIVED self-verification.",
       derived=True),
    _p("executive_dashboard_count_verification", "executive_intelligence", "change_management.compose",
       "verification", "count", "card", "observability.view", "/executive",
       "Executive-dashboard count verification — the live `len(DASHBOARD_REGISTRY)`. A DERIVED "
       "self-verification.", derived=True),
    # configuration
    _p("runtime_gate_coverage", "runtime", "runtime.consumption.adoption_stats", "configuration", "count",
       "card", "observability.view", "/runtime",
       "Runtime-gate coverage (feature-flag adoption), from the Runtime Engine. Counts + status only — never a "
       "sensitive configuration value."),
    _p("policy_engine_coverage", "policy", "change_management.compose", "configuration", "status", "card",
       "observability.view", "/runtime",
       "Policy-Engine coverage — the Policy Engine is present and consulted (a governance indicator). Status "
       "only — never a policy payload.", derived=True),
    _p("configuration_inventory", "change_management", "change_management.registry", "configuration", "count",
       "list", "observability.view", "/change-management?dashboard=configuration_governance",
       "The registered configuration catalog — each naming its owner + source + sensitivity + verification "
       "owner. Metadata only — never a sensitive configuration value.", derived=True),
    _p("configuration_drift_availability", "change_management", "change_management.compose", "configuration",
       "verification", "card", "observability.view", "/change-management?dashboard=configuration_governance",
       "Configuration-drift availability — which configuration domains have a live verification owner (route "
       "count, migration head) vs reference-only. A DERIVED coverage summary; drift is manifest-vs-live.",
       derived=True),
    # deployment / rollback / production (mostly not_configured)
    _p("deployment_evidence", "observability.catalog", "observability.catalog.list_deployment_references",
       "deployment", "count", "card", "observability.view", "/observability",
       "Deployment references (declared deployment metadata), from the Observability service catalog. "
       "Deployment EXECUTION / live status has no authoritative owner (not_configured). Merged is not "
       "deployed."),
    _p("production_verification_evidence", "not_configured", "change_management.registry", "deployment",
       "status", "card", "observability.view", "/change-management?dashboard=deployment_evidence",
       "Production-verification evidence. " + _NC_NOTE + " Green CI is not production certification.",
       derived=True),
    _p("rollback_evidence", "not_configured", "change_management.registry", "rollback", "status", "card",
       "observability.view", "/change-management?dashboard=rollback_readiness",
       "Rollback evidence / readiness. " + _NC_NOTE, derived=True),
    # operational (composed)
    _p("maintenance_window_status", "observability.alerts", "observability.alerts.metrics", "change", "count",
       "card", "observability.view", "/observability",
       "Active planned-maintenance windows, from the Observability alerts owner. The layer never schedules "
       "maintenance."),
    _p("related_operational_incidents", "observability.incidents", "observability.incidents.metrics", "change",
       "count", "card", "observability.view", "/observability",
       "Change-related operational incidents (open reliability incidents / findings), from the Observability "
       "incidents owner. An absent incident does not prove a successful change."),
    _p("related_security_findings", "security.incidents", "security.incidents.metrics", "change", "count",
       "card", "security.view", "/security/incidents",
       "Change-related security findings (open incidents / findings), from the Security incidents domain."),
    _p("change_related_exceptions", "compliance_intelligence", "compliance_intelligence.supervisory_dashboard",
       "change", "count", "card", "compliance.supervise", "/supervision",
       "Change-related compliance exceptions (open exceptions), from Compliance Intelligence. No second "
       "exception system."),
    _p("post_change_review_availability", "not_configured", "change_management.registry", "verification",
       "status", "card", "observability.view", "/change-management",
       "Post-change review availability. " + _NC_NOTE, derived=True),
    # derived readiness + executive
    _p("derived_change_readiness_coverage", "change_management", "change_management.compose", "verification",
       "coverage", "gauge", "observability.view", "/change-management",
       "DERIVED operational change-readiness coverage — self-verified counts (migration head + route/ADR/"
       "section/dashboard verification) + composed governance − not_configured / failed / stale areas. "
       "OPERATIONAL READINESS ONLY — never approval, certification, deployment success, or production safety; "
       "a green build is not production, merged is not deployed, an absent incident is not success.",
       derived=True),
    _p("executive_change_posture", "change_management", "change_management.compose", "verification",
       "distribution", "gauge", "analytics.executive", "/change-management",
       "DERIVED executive change posture — configured vs not_configured domains + self-verification status + "
       "composed governance across the authoritative owners. Operational readiness only, never a deployment or "
       "production certification.", derived=True),
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


_CM_CAPS = ("observability.view", "analytics.executive")

CHANGE_DASHBOARDS = (
    _d("change_overview", "change_management", "operations", "change_management.enabled",
       ("change_domain_inventory", "configured_change_domains", "derived_change_readiness_coverage"),
       _CM_CAPS, "/change-management?dashboard=change_overview",
       ("change_management", "observability")),
    _d("release_readiness", "change_management", "operations", "release_governance.enabled",
       ("current_release_line", "migration_head_status", "route_count_verification", "adr_count_verification"),
       _CM_CAPS, "/change-management?dashboard=release_readiness",
       ("architecture_manifest", "observability.health")),
    _d("ci_verification", "change_management", "operations", "change_management.enabled",
       ("ci_build_status", "e2e_status", "regression_status", "code_quality_status",
        "architecture_guard_status"),
       _CM_CAPS, "/change-management?dashboard=ci_verification",
       ("continuous_integration",)),
    _d("configuration_governance", "change_management", "operations", "configuration_intelligence.enabled",
       ("configuration_inventory", "runtime_gate_coverage", "policy_engine_coverage",
        "configuration_drift_availability"),
       _CM_CAPS, "/change-management?dashboard=configuration_governance",
       ("runtime", "policy", "change_management")),
    _d("migration_readiness", "change_management", "operations", "release_governance.enabled",
       ("migration_head_status", "migration_head_count", "governance_status"),
       _CM_CAPS, "/change-management?dashboard=migration_readiness",
       ("observability.health", "change_management")),
    _d("deployment_evidence", "change_management", "operations", "deployment_evidence.enabled",
       ("deployment_evidence", "production_verification_evidence", "maintenance_window_status"),
       _CM_CAPS, "/change-management?dashboard=deployment_evidence",
       ("observability", "change_management")),
    _d("rollback_readiness", "change_management", "operations", "deployment_evidence.enabled",
       ("rollback_evidence", "related_operational_incidents", "post_change_review_availability"),
       _CM_CAPS, "/change-management?dashboard=rollback_readiness",
       ("change_management", "observability")),
    _d("executive_change_posture", "change_management", "executive", "change_management.enabled",
       ("executive_change_posture", "derived_change_readiness_coverage", "governance_status"),
       _CM_CAPS, "/change-management?dashboard=executive_change_posture",
       ("change_management", "observability")),
)

_DASH_BY_KEY = {d.key: d for d in CHANGE_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def change_domain(key) -> ChangeDomain | None:
    return _CD_BY_KEY.get(key)


def release_entry(key) -> ReleaseEntry | None:
    return _RE_BY_KEY.get(key)


def configuration_entry(key) -> ConfigurationEntry | None:
    return _CFG_BY_KEY.get(key)


def change_evidence(key) -> ChangeEvidence | None:
    return _EV_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def _all_entries():
    return (*CHANGE_DOMAIN_REGISTRY, *RELEASE_REGISTRY, *CONFIGURATION_REGISTRY, *CHANGE_EVIDENCE_REGISTRY)


def not_configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == NOT_CONFIGURED)


def configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == CONFIGURED)


def coverage() -> dict:
    return {
        "change_domains": len(CHANGE_DOMAIN_REGISTRY),
        "release_entries": len(RELEASE_REGISTRY),
        "configuration_entries": len(CONFIGURATION_REGISTRY),
        "change_evidence": len(CHANGE_EVIDENCE_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(CHANGE_DASHBOARDS),
        "configured_domains": len(configured_domains()),
        "not_configured_domains": len(not_configured_domains()),
    }
