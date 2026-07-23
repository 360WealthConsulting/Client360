"""Analytics source-reading layer (Phase D.15).

The single place Analytics reads source data. It (a) composes existing principal-scoped domain
reports and (b) runs bounded, scope-filtered COUNT/SUM aggregates using the shared
``accessible_person_ids`` primitive (None = firm-wide, set = restricted, empty = zero). It
re-implements no business logic and never writes. Firm-wide (unrestricted) reads are only
reached by principals with ``record.read_all`` (executive); an advisor's numbers are their book.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.db import (
    annual_review_sessions,
    business_planning_profiles,
    campaigns,
    engine,
    households,
    people,
    referral_sources,
    relationship_entities,
    tasks,
    timeline_events,
)
from app.security.authorization import accessible_person_ids

ZERO = Decimal("0")


def book_scope(principal):
    """Resolve the principal's accessible person-id scope: None (firm-wide, read_all),
    a set (restricted), or an empty set (nothing)."""
    with engine.connect() as c:
        return accessible_person_ids(c, principal)


def _person_household_ids(c, ids):
    if not ids:
        return set()
    return set(c.scalars(select(people.c.household_id).where(
        people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))


# --- bounded scoped counts / sums --------------------------------------------

def client_count(principal) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        if ids is None:
            return c.scalar(select(func.count()).select_from(people)) or 0
        return len(ids)


def household_count(principal) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        if ids is None:
            return c.scalar(select(func.count()).select_from(households)) or 0
        return len(_person_household_ids(c, ids))


def organization_count(principal) -> int:
    """Firm business entities (organizations). Firm asset — full count (executive metric)."""
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(relationship_entities)
                        .where(relationship_entities.c.entity_type == "business")) or 0


def open_task_count(principal) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = select(func.count()).select_from(tasks).where(tasks.c.status != "complete")
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(tasks.c.person_id.in_(tuple(ids)))
        return c.scalar(stmt) or 0


def timeline_activity_count(principal, *, since=None) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = select(func.count()).select_from(timeline_events)
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(timeline_events.c.person_id.in_(tuple(ids)))
        if since is not None:
            stmt = stmt.where(timeline_events.c.event_time >= since)
        return c.scalar(stmt) or 0


def annual_review_count(principal, *, completed_only=False) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = select(func.count()).select_from(annual_review_sessions)
        if completed_only:
            stmt = stmt.where(annual_review_sessions.c.status == "completed")
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(annual_review_sessions.c.person_id.in_(tuple(ids)))
        return c.scalar(stmt) or 0


def business_plan_count(principal) -> int:
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(business_planning_profiles)) or 0


def document_count(principal) -> int:
    """Book-scoped active document count (Phase D.16 — Analytics consumes document statistics;
    Documents never depend on Analytics). Excludes soft-deleted."""
    from app.db import documents
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = select(func.count()).select_from(documents).where(documents.c.status != "deleted")
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(documents.c.person_id.in_(tuple(ids)))
        return c.scalar(stmt) or 0


def book_aum(principal) -> Decimal:
    from app.services.portfolio import book_aum as portfolio_book_aum
    return portfolio_book_aum(book_scope(principal))


def active_workflow_count(principal) -> int:
    """Book-scoped count of active workflow instances (Phase D.17 — Analytics consumes workflow
    statistics; Workflow never depends on Analytics)."""
    from app.db import workflow_instances
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = select(func.count()).select_from(workflow_instances).where(workflow_instances.c.status == "active")
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(workflow_instances.c.person_id.in_(tuple(ids)))
        return c.scalar(stmt) or 0


def active_campaign_count(principal) -> int:
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(campaigns)
                        .where(campaigns.c.status == "active")) or 0


def active_referral_source_count(principal) -> int:
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(referral_sources)
                        .where(referral_sources.c.status == "active")) or 0


def active_conversation_count(principal) -> int:
    """Book-scoped count of open communication conversations (Phase D.18 — Analytics consumes
    communication statistics; Communications never depends on Analytics)."""
    from app.db import communication_conversations
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = (select(func.count()).select_from(communication_conversations)
                .where(communication_conversations.c.status == "open"))
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(communication_conversations.c.person_id.in_(tuple(ids)))
        return c.scalar(stmt) or 0


def integration_sync_failure_count(principal) -> int:
    """Firm-level count of failed/partial integration sync runs (Phase D.24 — Analytics consumes
    integration statistics; Integration never depends on Analytics)."""
    from app.db import integration_sync_runs
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(integration_sync_runs)
                        .where(integration_sync_runs.c.status.in_(("failed", "partial")))) or 0


def integration_connector_error_count(principal) -> int:
    """Firm-level count of connectors in an error state (Phase D.24)."""
    from app.db import integration_connectors
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(integration_connectors)
                        .where(integration_connectors.c.status == "error")) or 0


def governance_open_finding_count(principal) -> int:
    """Firm-level count of open data-quality findings (Phase D.23 — Analytics consumes governance
    statistics; Governance never depends on Analytics)."""
    from app.db import governance_quality_findings
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(governance_quality_findings)
                        .where(governance_quality_findings.c.status.in_(("open", "acknowledged")))) or 0


def governance_active_legal_hold_count(principal) -> int:
    """Firm-level count of active legal holds (Phase D.23)."""
    from app.db import governance_legal_holds
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(governance_legal_holds)
                        .where(governance_legal_holds.c.status == "active")) or 0


def security_open_finding_count(principal) -> int:
    """Firm-level count of open security findings (Phase D.25 — Analytics consumes security
    statistics; Security never depends on Analytics)."""
    from app.db import security_findings
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(security_findings)
                        .where(security_findings.c.status == "open")) or 0


def security_open_incident_count(principal) -> int:
    """Firm-level count of unresolved security incidents (Phase D.25)."""
    from app.db import security_incidents
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(security_incidents)
                        .where(security_incidents.c.status.notin_(("resolved", "closed")))) or 0


def security_overdue_rotation_count(principal) -> int:
    """Firm-level count of active secret references past their rotation date (Phase D.25)."""
    from datetime import UTC, datetime

    from app.db import security_secret_references
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(security_secret_references).where(
            security_secret_references.c.status == "active",
            security_secret_references.c.next_rotation_at.is_not(None),
            security_secret_references.c.next_rotation_at <= datetime.now(UTC))) or 0


def security_expired_certificate_count(principal) -> int:
    """Firm-level count of expired/revoked certificate references (Phase D.25)."""
    from app.db import security_certificate_references
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(security_certificate_references)
                        .where(security_certificate_references.c.status.in_(("expired", "revoked")))) or 0


def security_mfa_enabled_user_count(principal) -> int:
    """Firm-level count of active users with MFA enabled (Phase D.25 — MFA coverage). Reads the
    existing ``users.mfa_enabled`` flag maintained by authentication; Security adds no auth state."""
    from app.db import users
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(users).where(
            users.c.status == "active", users.c.mfa_enabled.is_(True))) or 0


def observability_failed_health_check_count(principal) -> int:
    """Firm-level count of health checks whose last status is unhealthy/degraded (Phase D.26 —
    Analytics consumes observability statistics; Observability never depends on Analytics)."""
    from app.db import observability_health_checks
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(observability_health_checks)
                        .where(observability_health_checks.c.last_status.in_(("unhealthy", "degraded")))) or 0


def observability_open_alert_count(principal) -> int:
    """Firm-level count of open operational alerts (Phase D.26)."""
    from app.db import observability_alerts
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(observability_alerts)
                        .where(observability_alerts.c.status == "open")) or 0


def observability_operational_service_count(principal) -> int:
    """Firm-level count of services currently operational (Phase D.26 — service availability)."""
    from app.db import observability_services
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(observability_services)
                        .where(observability_services.c.status == "operational")) or 0


def observability_diagnostic_failure_count(principal) -> int:
    """Firm-level count of diagnostic results that failed/errored (Phase D.26)."""
    from app.db import observability_diagnostic_results
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(observability_diagnostic_results)
                        .where(observability_diagnostic_results.c.status.in_(("fail", "error")))) or 0


def observability_open_reliability_incident_count(principal) -> int:
    """Firm-level count of unresolved reliability incidents (Phase D.26)."""
    from app.db import observability_reliability_incidents
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(observability_reliability_incidents)
                        .where(observability_reliability_incidents.c.status.notin_(("resolved", "closed")))) or 0


def configuration_enabled_feature_flag_count(principal) -> int:
    """Firm-level count of enabled feature flags (Phase D.27 — Analytics consumes configuration
    statistics; Configuration never depends on Analytics)."""
    from app.db import configuration_feature_flags
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(configuration_feature_flags)
                        .where(configuration_feature_flags.c.enabled.is_(True))) or 0


def configuration_drift_override_count(principal) -> int:
    """Firm-level count of active environment overrides (Phase D.27 — configuration drift)."""
    from app.db import configuration_environment_overrides
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(configuration_environment_overrides)
                        .where(configuration_environment_overrides.c.active.is_(True))) or 0


def configuration_active_edition_count(principal) -> int:
    """Firm-level count of active editions (Phase D.27 — edition distribution)."""
    from app.db import configuration_editions
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(configuration_editions)
                        .where(configuration_editions.c.status == "active")) or 0


def configuration_pending_change_count(principal) -> int:
    """Firm-level count of pending (proposed) configuration changes (Phase D.27 — pending reviews)."""
    from app.db import configuration_changes
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(configuration_changes)
                        .where(configuration_changes.c.status == "proposed")) or 0


def runtime_active_snapshot_count(principal) -> int:
    """Firm-level count of runtime effective-configuration snapshots (Phase D.28 — Analytics consumes
    runtime statistics; the runtime engine never depends on Analytics)."""
    from app.db import runtime_config_snapshots
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(runtime_config_snapshots)) or 0


def runtime_cache_hit_ratio(principal):
    """In-process runtime cache hit ratio as a percentage (Phase D.28 — cache efficiency). Returns
    None when no lookups have occurred yet (metric compute runs in the web-app process, so the
    in-process counter is readable)."""
    from app.services.runtime.cache import RUNTIME_CACHE
    ratio = RUNTIME_CACHE.stats().get("hit_ratio")
    return round(ratio * 100, 1) if ratio is not None else None


def runtime_configuration_resolution_count(principal) -> int:
    """In-process count of configuration resolutions performed by the runtime engine (Phase D.28)."""
    from app.services.runtime.cache import RUNTIME_CACHE
    return int(RUNTIME_CACHE.stats().get("evaluations") or 0)


def runtime_edition_utilization(principal) -> int:
    """Firm-level count of active edition assignments (Phase D.28 — edition utilization view)."""
    from app.db import configuration_edition_assignments
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(configuration_edition_assignments)
                        .where(configuration_edition_assignments.c.status == "active")) or 0


def runtime_active_feature_count(principal) -> int:
    """Count of features enabled in the current runtime snapshot (Phase D.28 — feature utilization)."""
    from app.db import runtime_config_snapshots
    with engine.connect() as c:
        row = c.execute(select(runtime_config_snapshots.c.active_features)
                        .order_by(runtime_config_snapshots.c.version.desc()).limit(1)).first()
    if not row or not row[0]:
        return 0
    return sum(1 for v in row[0].values() if isinstance(v, dict) and v.get("enabled"))


def runtime_active_worker_count(principal) -> int:
    """Firm-level count of active runtime workers in the cluster (Phase D.29 — Analytics consumes
    coordination statistics; the runtime engine never depends on Analytics)."""
    from app.db import runtime_workers
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(runtime_workers)
                        .where(runtime_workers.c.status == "active")) or 0


def runtime_stale_worker_count(principal) -> int:
    """Firm-level count of stale/stopped runtime workers (Phase D.29)."""
    from app.db import runtime_workers
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(runtime_workers)
                        .where(runtime_workers.c.status.in_(("stale", "stopped")))) or 0


def runtime_cluster_convergence_pct(principal):
    """Cluster convergence as a percentage: active workers at the current generation version
    (Phase D.29). Returns None when there are no active workers."""
    from app.db import runtime_generations, runtime_workers
    with engine.connect() as c:
        version = c.scalar(select(func.max(runtime_generations.c.version)).where(
            runtime_generations.c.status == "active"))
        active = c.scalar(select(func.count()).select_from(runtime_workers)
                          .where(runtime_workers.c.status == "active")) or 0
        if active == 0 or version is None:
            return None
        converged = c.scalar(select(func.count()).select_from(runtime_workers).where(
            runtime_workers.c.status == "active", runtime_workers.c.runtime_version >= version)) or 0
    return round((converged / active) * 100, 1)


def runtime_generation_count(principal) -> int:
    """Firm-level count of runtime generations (version history depth) (Phase D.29)."""
    from app.db import runtime_generations
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(runtime_generations)) or 0


def runtime_feature_consumption_count(principal) -> int:
    """In-process count of runtime feature-consumption lookups (Phase D.30 — Analytics consumes
    runtime-adoption statistics). Metric compute runs in the web-app process, so the in-process
    consumption counter is readable."""
    from app.services.runtime import consumption
    return int(consumption.adoption_stats().get("feature_lookups") or 0)


def runtime_config_lookup_count(principal) -> int:
    """In-process count of runtime configuration lookups (Phase D.30)."""
    from app.services.runtime import consumption
    return int(consumption.adoption_stats().get("config_lookups") or 0)


def runtime_legacy_fallback_count(principal) -> int:
    """In-process count of legacy-default fallbacks (behavior served the pre-migration default because
    no runtime definition existed) (Phase D.30)."""
    from app.services.runtime import consumption
    return int(consumption.adoption_stats().get("legacy_fallbacks") or 0)


def runtime_behavior_adoption_pct(principal):
    """Behavioral-migration adoption percentage from the durable registry (Phase D.30). Migrated +
    retired behaviors over the migratable set (deterministic behaviors are excluded)."""
    from app.services.runtime import behavior
    return behavior.coverage()["adoption_pct"]


def runtime_migrated_behavior_count(principal) -> int:
    """Firm-level count of behaviors migrated to the runtime engine (Phase D.30)."""
    from app.db import runtime_behaviors
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(runtime_behaviors)
                        .where(runtime_behaviors.c.status.in_(("migrated", "retired")))) or 0


def runtime_retired_behavior_count(principal) -> int:
    """Firm-level count of behaviors whose legacy fallback has been retired (Phase D.31 — Analytics
    consumes runtime-authority statistics; the runtime engine never depends on Analytics)."""
    from app.db import runtime_behaviors
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(runtime_behaviors)
                        .where(runtime_behaviors.c.status == "retired")) or 0


def runtime_authority_pct(principal):
    """Runtime authority percentage — behaviors for which the engine is authoritative over the
    migratable set (Phase D.31)."""
    from app.services.runtime import behavior
    return behavior.coverage()["authority_pct"]


def runtime_definition_coverage_pct(principal):
    """Governance definition coverage — authoritative behaviors whose complete runtime definition is
    present (Phase D.31)."""
    from app.services.runtime import governance
    return governance.validate()["coverage"]["coverage_pct"]


def runtime_governance_issue_count(principal) -> int:
    """Count of open runtime-metadata governance issues (Phase D.31)."""
    from app.services.runtime import governance
    return int(governance.validate()["issue_count"])


def runtime_compatibility_shim_count(principal) -> int:
    """Firm-level count of documented compatibility shims (migrated behaviors that keep a legacy
    default because their key space is unbounded) (Phase D.31)."""
    from app.db import runtime_behaviors
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(runtime_behaviors)
                        .where(runtime_behaviors.c.compatibility_shim.is_(True))) or 0


def runtime_compatibility_fallback_count(principal) -> int:
    """In-process count of compatibility-shim fallbacks actually served (an authoritative/shim behavior
    served its legacy default because no runtime definition was found) (Phase D.31)."""
    from app.services.runtime import consumption
    return int(consumption.adoption_stats().get("compatibility_fallbacks") or 0)


# --- Runtime Policy Engine (Phase D.32 — Analytics consumes policy statistics; the policy engine
#     never imports Analytics) ------------------------------------------------------------------

def policy_evaluation_count(principal) -> int:
    """In-process count of centralized policy evaluations (routine successful evaluations are counted
    here, never individually logged) (Phase D.32)."""
    from app.services.policy import engine as policy_engine
    return int(policy_engine.evaluation_stats().get("evaluations") or 0)


def policy_cache_hit_count(principal) -> int:
    """In-process count of policy decisions served from the deterministic policy cache (Phase D.32)."""
    from app.services.policy import engine as policy_engine
    return int(policy_engine.evaluation_stats().get("cache_hits") or 0)


def policy_governance_issue_count(principal) -> int:
    """Count of open policy-registry governance issues (Phase D.32)."""
    from app.services.policy import governance as policy_governance
    return int(policy_governance.validate()["issue_count"])


def policy_coverage_pct(principal):
    """Decision-area coverage — the share of the ten declarative decision areas registered as a policy
    (Phase D.32)."""
    from app.services.policy import registry as policy_registry
    return policy_registry.coverage()["coverage_pct"]


def policy_adoption_pct(principal):
    """Policy adoption — active (engine-evaluated) policies over the migratable set (in-domain policies
    are documented exceptions, excluded) (Phase D.32)."""
    from app.services.policy import registry as policy_registry
    return policy_registry.coverage()["adoption_pct"]


def policy_execution_latency_us(principal):
    """Average in-process policy-execution latency in microseconds (Phase D.32)."""
    from app.services.policy import engine as policy_engine
    return policy_engine.evaluation_stats().get("avg_latency_us")


def active_project_count(principal) -> int:
    """Firm-level count of active projects (Phase D.20 — Analytics consumes operational statistics;
    Operations never depends on Analytics). Firm operations are not client-book-scoped."""
    from app.db import projects
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(projects)
                        .where(projects.c.status == "active")) or 0


def open_operational_task_count(principal) -> int:
    """Firm-level count of open operational tasks (Phase D.20)."""
    from app.db import operational_tasks
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(operational_tasks)
                        .where(operational_tasks.c.status.notin_(
                            ("completed", "cancelled", "archived")))) or 0


def upcoming_meeting_count(principal) -> int:
    """Book-scoped count of upcoming (scheduled/confirmed, future-dated) meetings (Phase D.19 —
    Analytics consumes scheduling statistics; Scheduling never depends on Analytics)."""
    from datetime import UTC, datetime

    from app.db import meetings
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = (select(func.count()).select_from(meetings).where(
            meetings.c.status.in_(("scheduled", "confirmed")),
            meetings.c.starts_at.is_not(None), meetings.c.starts_at >= datetime.now(UTC)))
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(meetings.c.person_id.in_(tuple(ids)))
        return c.scalar(stmt) or 0


# --- composed domain reports (principal-scoped) ------------------------------

def pipeline_report(principal, *, today=None):
    from app.services.opportunity import reporting
    return reporting.pipeline_report(principal, today=today)


def forecast_report(principal):
    from app.services.opportunity import reporting
    return reporting.forecast_report(principal)


def bizdev_summary(principal):
    from app.services.bizdev import intelligence
    return intelligence.executive_summary(principal)


def campaign_report(principal):
    from app.services.campaign import reporting
    return reporting.campaign_report(principal)


def referral_report(principal):
    from app.services.referral import reporting
    return reporting.referral_report(principal)


def insurance_dashboard(principal):
    from app.services import insurance_reporting
    return insurance_reporting.operations_dashboard(principal)


def tax_dashboard(principal):
    from app.services import tax_domain
    return tax_domain.dashboard(principal)


def open_work_total(principal) -> int:
    from app.services import advisor_work
    return advisor_work.list_work(principal, page=1, page_size=1)["total"]


def open_compliance_total(principal) -> int:
    from app.services.compliance import reviews
    return reviews.list_reviews(principal, page=1, page_size=1)["total"]


def advisor_open_opportunities(principal):
    """Open opportunities grouped by primary advisor (for advisor-production dimensions)."""
    from app.services.opportunity import service as opp_svc
    rows = opp_svc.all_in_scope(principal, statuses=("open",))
    counts: dict[int, int] = {}
    for o in rows:
        counts[o["primary_advisor_id"]] = counts.get(o["primary_advisor_id"], 0) + 1
    return counts
