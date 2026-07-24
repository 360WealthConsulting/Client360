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
    if ids is None:
        # (D.37) firm-wide → serve from the projection when healthy+fresh, else fall back below.
        from app.services.projections import adoption
        pc = adoption.count("people.summary", principal, firm_level=False)
        if pc is not None:
            return pc
    with engine.connect() as c:
        if ids is None:
            return c.scalar(select(func.count()).select_from(people)) or 0
        return len(ids)


def household_count(principal) -> int:
    ids = book_scope(principal)
    if ids is None:                                    # (D.37) firm-wide → projection with fallback
        from app.services.projections import adoption
        pc = adoption.count("household.summary", principal, firm_level=False)
        if pc is not None:
            return pc
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
    if ids is None:                                    # (D.37) firm-wide → projection with fallback
        from app.services.projections import adoption
        pc = adoption.count("document.status", principal, firm_level=False,
                            status_col="status", status_not_in=("deleted",))
        if pc is not None:
            return pc
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


# --- Workflow Orchestration Engine (Phase D.33 — Analytics consumes orchestration statistics; the
#     orchestration engine never imports Analytics) --------------------------------------------------

def _orch_stats():
    from app.services.orchestration import engine as orchestration_engine
    return orchestration_engine.stats()


def orchestration_launch_count(principal) -> int:
    """In-process count of orchestration instances launched (Phase D.33)."""
    return int(_orch_stats().get("launches") or 0)


def orchestration_completion_count(principal) -> int:
    """In-process count of orchestration instances completed (Phase D.33)."""
    return int(_orch_stats().get("completions") or 0)


def orchestration_failure_count(principal) -> int:
    """In-process count of orchestration failures (Phase D.33)."""
    return int(_orch_stats().get("failures") or 0)


def orchestration_retry_count(principal) -> int:
    """In-process count of orchestration retries (Phase D.33)."""
    return int(_orch_stats().get("retries") or 0)


def orchestration_replay_count(principal) -> int:
    """In-process count of deterministic replays performed (Phase D.33)."""
    return int(_orch_stats().get("replays") or 0)


def orchestration_simulation_count(principal) -> int:
    """In-process count of dry-run simulations performed (Phase D.33)."""
    return int(_orch_stats().get("simulations") or 0)


def orchestration_governance_issue_count(principal) -> int:
    """Count of open orchestration-governance issues (Phase D.33)."""
    from app.services.orchestration import governance as orchestration_governance
    return int(orchestration_governance.validate()["issue_count"])


def orchestration_coverage_pct(principal):
    """Orchestration domain-coverage percentage (Phase D.33)."""
    from app.services.orchestration import registry as orchestration_registry
    return orchestration_registry.coverage()["coverage_pct"]


def orchestration_avg_execution_ms(principal):
    """Average in-process orchestration execution time in milliseconds (Phase D.33)."""
    return _orch_stats().get("avg_duration_ms")


# --- Enterprise Domain Event Model (Phase D.34 — Analytics consumes event-model statistics; the event
#     model never imports Analytics) -----------------------------------------------------------------

def _event_stats():
    from app.services.events.common import stats
    return stats()


def domain_events_published(principal) -> int:
    """In-process count of domain events published through the standardized model (Phase D.34)."""
    return int(_event_stats().get("published") or 0)


def domain_events_delivered(principal) -> int:
    """In-process count of domain events delivered to the observability sink (Phase D.34)."""
    from app.services.events.subscriptions import delivered_count
    return int(delivered_count())


def domain_events_dead_lettered(principal) -> int:
    """Count of dead-lettered events in the transactional outbox (Phase D.34)."""
    from app.services.events import diagnostics
    return int(diagnostics.event_counts().get("dead_lettered") or 0)


def domain_event_contract_count(principal) -> int:
    """Count of active domain-event contracts (Phase D.34)."""
    from app.services.events import registry
    return int(registry.coverage().get("active") or 0)


def domain_event_subscription_count(principal) -> int:
    """Count of active domain-event subscriptions (Phase D.34)."""
    from app.services.events import registry
    return int(registry.coverage().get("active_subscriptions") or 0)


def domain_event_governance_issue_count(principal) -> int:
    """Count of open domain-event governance issues (Phase D.34)."""
    from app.services.events import governance
    return int(governance.validate()["issue_count"])


def domain_event_coverage_pct(principal):
    """Domain-event domain-coverage percentage (Phase D.34)."""
    from app.services.events import registry
    return registry.coverage()["coverage_pct"]


def domain_event_replay_count(principal) -> int:
    """In-process count of event replays performed (Phase D.34)."""
    return int(_event_stats().get("replays") or 0)


def domain_event_publish_failure_count(principal) -> int:
    """In-process count of publish failures (unregistered / contract-violating events) (Phase D.34)."""
    return int(_event_stats().get("publish_failures") or 0)


# --- Domain Event Producer Adoption (Phase D.35) -------------------------------------------------

def domain_event_producer_adoption_pct(principal):
    """Producer-adoption coverage — the share of the D.35 target contracts with an actual publishing
    site (Phase D.35)."""
    from app.services.events import registry
    return registry.producer_adoption()["adoption_pct"]


def domain_event_active_producer_count(principal) -> int:
    """Count of active producers (registered producers with a publishing site) (Phase D.35)."""
    from app.services.events import registry
    return int(registry.producer_adoption()["active_producers"])


def domain_event_stale_producer_count(principal) -> int:
    """Count of inactive/stale producers (registered producers with no publishing site) (Phase D.35)."""
    from app.services.events import registry
    return int(registry.producer_adoption()["stale_producers"])


def domain_events_awaiting_delivery(principal) -> int:
    """Count of domain events awaiting delivery in the outbox (status pending) (Phase D.35)."""
    from app.services.events import diagnostics
    return int(diagnostics.event_counts().get("by_status", {}).get("pending") or 0)


def domain_event_adopted_domain_count(principal) -> int:
    """Count of business domains that publish typed domain events (Phase D.35)."""
    from app.database.event_seed import D35_DOMAINS
    return len(D35_DOMAINS)


# --- Read Models & Projection Engine (Phase D.36 — Analytics consumes projection statistics; the
#     projection engine never imports Analytics) -----------------------------------------------------

def _proj_cov():
    from app.services.projections import registry
    return registry.coverage()


def _proj_stats():
    from app.services.projections import engine
    return engine.stats()


def projection_count(principal) -> int:
    """Count of active read-model projections (Phase D.36)."""
    return int(_proj_cov().get("active") or 0)


def healthy_projection_count(principal) -> int:
    """Count of healthy projections (Phase D.36)."""
    return int(_proj_cov().get("healthy") or 0)


def lagging_projection_count(principal) -> int:
    """Count of lagging projections (behind the outbox) (Phase D.36)."""
    return int(_proj_cov().get("lagging") or 0)


def projection_events_processed(principal) -> int:
    """In-process count of events applied to projections (Phase D.36)."""
    return int(_proj_stats().get("events_processed") or 0)


def projection_avg_latency_ms(principal):
    """Average in-process per-event projection latency in milliseconds (Phase D.36)."""
    return _proj_stats().get("avg_process_ms")


def largest_projection_size(principal) -> int:
    """Row count of the largest read model (Phase D.36)."""
    from app.services.projections import diagnostics
    return int(diagnostics.largest_projection().get("size") or 0)


def projection_rebuild_count(principal) -> int:
    """In-process count of projection rebuilds (Phase D.36)."""
    return int(_proj_stats().get("rebuilds") or 0)


def projection_replay_count(principal) -> int:
    """In-process count of projection replays (Phase D.36)."""
    return int(_proj_stats().get("replays") or 0)


def projection_failure_count(principal) -> int:
    """In-process count of failed events during projection processing (Phase D.36)."""
    return int(_proj_stats().get("failed_events") or 0)


def projection_coverage_pct(principal):
    """Projection event-coverage — domain-event contracts consumed by a projection (Phase D.36)."""
    return _proj_cov().get("event_coverage_pct")


# --- Read Surface Adoption (Phase D.37 — projection-backed firm counts, authoritative fallback) -----

def projection_open_opportunity_count(principal) -> int:
    """Firm-level open-opportunity count — from the opportunity.pipeline projection when healthy+fresh,
    else the authoritative table (Phase D.37)."""
    from app.services.projections import adoption
    pc = adoption.count("opportunity.pipeline", principal, firm_level=True,
                        status_col="status", status_in=("open",))
    if pc is not None:
        return pc
    from app.db import opportunities
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(opportunities)
                        .where(opportunities.c.status == "open")) or 0


def projection_open_compliance_count(principal) -> int:
    """Firm-level open-compliance-review count — from the compliance.queue projection when healthy+fresh,
    else the authoritative table (Phase D.37)."""
    from app.services.projections import adoption
    pc = adoption.count("compliance.queue", principal, firm_level=True, null_col="decided_at")
    if pc is not None:
        return pc
    from app.db import compliance_reviews
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(compliance_reviews).where(
            compliance_reviews.c.status.notin_(
                ("approved", "approved_with_conditions", "returned", "declined")))) or 0


def projection_tax_return_count(principal) -> int:
    """Firm-level tax-return count — from the tax.pipeline projection when healthy+fresh, else the
    authoritative table (Phase D.37)."""
    from app.services.projections import adoption
    pc = adoption.count("tax.pipeline", principal, firm_level=True)
    if pc is not None:
        return pc
    from app.db import tax_engagement_returns
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(tax_engagement_returns)) or 0


def projection_insurance_case_count(principal) -> int:
    """Firm-level insurance-case count — from the insurance.pipeline projection when healthy+fresh, else
    the authoritative table (Phase D.37)."""
    from app.services.projections import adoption
    pc = adoption.count("insurance.pipeline", principal, firm_level=True)
    if pc is not None:
        return pc
    from app.db import insurance_cases
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(insurance_cases)) or 0


def projection_benefits_enrollment_count(principal) -> int:
    """Firm-level benefits-enrollment count — from the benefits.enrollment projection when healthy+fresh,
    else the authoritative table (Phase D.37)."""
    from app.services.projections import adoption
    pc = adoption.count("benefits.enrollment", principal, firm_level=True)
    if pc is not None:
        return pc
    from app.db import benefit_enrollments
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(benefit_enrollments)) or 0


def projection_open_exception_count(principal) -> int:
    """Firm-level open-exception count — from the exception.dashboard projection when healthy+fresh, else
    the authoritative table (Phase D.37)."""
    from app.services.projections import adoption
    pc = adoption.count("exception.dashboard", principal, firm_level=True,
                        status_col="status", status_not_in=("resolved", "cancelled"))
    if pc is not None:
        return pc
    from app.db import exceptions
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(exceptions)
                        .where(exceptions.c.status.notin_(("resolved", "cancelled")))) or 0


def projection_adoption_pct(principal):
    """Read-surface adoption utilization — projection reads ÷ (reads + fallbacks) (Phase D.37)."""
    from app.services.projections import adoption
    return adoption.usage_stats().get("projection_read_pct")


def projection_fallback_count(principal) -> int:
    """In-process count of adopted-read fallbacks to the authoritative table (Phase D.37)."""
    from app.services.projections import adoption
    return int(adoption.usage_stats().get("fallbacks") or 0)


def projection_backed_read_count(principal) -> int:
    """In-process count of adopted reads served from a projection (Phase D.37)."""
    from app.services.projections import adoption
    return int(adoption.usage_stats().get("reads") or 0)


def adopted_read_surface_count(principal) -> int:
    """Count of read surfaces adopted onto projections (Phase D.37)."""
    from app.services.projections.adoption import ADOPTION_TARGETS
    return len(ADOPTION_TARGETS)


def active_project_count(principal) -> int:
    """Firm-level count of active projects (Phase D.20 — Analytics consumes operational statistics;
    Operations never depends on Analytics). Firm operations are not client-book-scoped.
    (D.37) Served from the operations.projects projection when healthy+fresh, else authoritative."""
    from app.services.projections import adoption
    pc = adoption.count("operations.projects", principal, firm_level=True,
                        status_col="status", status_in=("active",))
    if pc is not None:
        return pc
    from app.db import projects
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(projects)
                        .where(projects.c.status == "active")) or 0


def open_operational_task_count(principal) -> int:
    """Firm-level count of open operational tasks (Phase D.20).
    (D.37) Served from the operations.tasks projection when healthy+fresh, else authoritative."""
    from app.services.projections import adoption
    pc = adoption.count("operations.tasks", principal, firm_level=True,
                        status_col="status", status_not_in=("completed", "cancelled", "archived"))
    if pc is not None:
        return pc
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


# --- Advisor AI Assist (Phase D.42) — low-cardinality operational metrics (in-process counters only,
#     never client-specific / question-text / high-cardinality data) --------------------------------

def ai_assist_request_count(principal) -> int:
    from app.services.ai_assist.common import assist_stats
    return int(assist_stats().get("requests") or 0)


def ai_assist_refusal_count(principal) -> int:
    from app.services.ai_assist.common import assist_stats
    return int(assist_stats().get("refusals") or 0)


def ai_assist_success_rate(principal):
    from app.services.ai_assist.common import assist_stats
    return assist_stats().get("success_rate")


def ai_assist_avg_latency_ms(principal):
    from app.services.ai_assist.common import assist_stats
    return assist_stats().get("avg_latency_ms")


def ai_assist_citation_coverage(principal):
    from app.services.ai_assist.common import assist_stats
    return assist_stats().get("citation_coverage")


def ai_assist_provider_failures(principal) -> int:
    from app.services.ai_assist.common import assist_stats
    return int(assist_stats().get("provider_failures") or 0)


# --- Unified Communications & Engagement (Phase D.44) — low-cardinality in-process counters ---

def engagement_interactions_composed(principal) -> int:
    from app.services.communications.engagement.metrics import interactions_composed_count
    return interactions_composed_count(principal)


def engagement_searches(principal) -> int:
    from app.services.communications.engagement.metrics import engagement_search_count
    return engagement_search_count(principal)


def engagement_adapter_failures(principal) -> int:
    from app.services.communications.engagement.metrics import engagement_adapter_failure_count
    return engagement_adapter_failure_count(principal)


# --- Enterprise Knowledge Graph (Phase D.45) — low-cardinality in-process counters ---

def knowledge_traversals(principal) -> int:
    from app.services.knowledge.metrics import knowledge_traversal_count
    return knowledge_traversal_count(principal)


def knowledge_explanations(principal) -> int:
    from app.services.knowledge.metrics import knowledge_explanation_count
    return knowledge_explanation_count(principal)


def knowledge_searches(principal) -> int:
    from app.services.knowledge.metrics import knowledge_search_count
    return knowledge_search_count(principal)


def knowledge_adapter_failures(principal) -> int:
    from app.services.knowledge.metrics import knowledge_adapter_failure_count
    return knowledge_adapter_failure_count(principal)


# --- Operational Intelligence recommendations (Phase D.46) — low-cardinality in-process counters ---

def recommendations_generated(principal) -> int:
    from app.services.recommendations.metrics import recommendations_generated as _r
    return _r(principal)


def recommendations_suppressed(principal) -> int:
    from app.services.recommendations.metrics import recommendations_suppressed as _r
    return _r(principal)


def recommendation_compositions(principal) -> int:
    from app.services.recommendations.metrics import recommendation_compositions as _r
    return _r(principal)


def recommendation_adapter_failures(principal) -> int:
    from app.services.recommendations.metrics import recommendation_adapter_failures as _r
    return _r(principal)


# --- Compliance Intelligence / supervisory operations (Phase D.47) — low-cardinality in-process counters ---

def supervisory_reviews_composed(principal) -> int:
    from app.services.compliance_intelligence.metrics import supervisory_reviews_composed as _r
    return _r(principal)


def supervisory_exceptions_composed(principal) -> int:
    from app.services.compliance_intelligence.metrics import supervisory_exceptions_composed as _r
    return _r(principal)


def supervisory_dashboards(principal) -> int:
    from app.services.compliance_intelligence.metrics import supervisory_dashboards as _r
    return _r(principal)


def supervisory_authorization_failures(principal) -> int:
    from app.services.compliance_intelligence.metrics import (
        supervisory_authorization_failures as _r,
    )
    return _r(principal)


# --- Executive Reporting / firm intelligence (Phase D.48) — low-cardinality in-process counters ---

def executive_dashboards_composed(principal) -> int:
    from app.services.executive_intelligence.metrics import executive_dashboards_composed as _r
    return _r(principal)


def executive_widgets_composed(principal) -> int:
    from app.services.executive_intelligence.metrics import executive_widgets_composed as _r
    return _r(principal)


def executive_widget_failures(principal) -> int:
    from app.services.executive_intelligence.metrics import executive_widget_failures as _r
    return _r(principal)


def executive_authorization_failures(principal) -> int:
    from app.services.executive_intelligence.metrics import executive_authorization_failures as _r
    return _r(principal)
