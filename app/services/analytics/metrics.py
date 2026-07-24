"""Analytics metric registry (Phase D.15) — deterministic KPI definitions.

Each metric declares presentation metadata (label/category/unit/viz) and a deterministic compute
function that composes the source-reading layer. Executive metrics (firm-wide / revenue) require
``analytics.executive`` and are withheld (value None, restricted True) otherwise — server-side. No
AI; same inputs always yield the same output.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.analytics import sources


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    category: str          # revenue | pipeline | clients | production | operations | compliance | activity
    unit: str              # currency | percent | count | number
    viz: str               # card | gauge | trendline | leaderboard | ...
    executive: bool
    compute: object        # callable(principal) -> float|int|None


def _f(v):
    return float(v) if isinstance(v, Decimal) else v


def _num(v):
    return _f(v) if v is not None else 0


# --- deterministic compute helpers (guarded domain reads return None) --------

def _pipeline(principal):
    return sources.pipeline_report(principal)


def _safe(fn):
    def wrapped(principal):
        try:
            return fn(principal)
        except Exception:
            return None
    return wrapped


_DEFS = (
    # Revenue / AUM (executive).
    Metric("aum", "AUM", "revenue", "currency", "card", True,
           lambda p: _f(sources.book_aum(p))),
    Metric("forecast_revenue", "Forecast Revenue", "revenue", "currency", "card", True,
           lambda p: sources.forecast_report(p)["weighted_forecast_total"]),
    Metric("campaign_revenue", "Campaign Revenue", "revenue", "currency", "card", True,
           lambda p: sources.bizdev_summary(p)["campaign_revenue"]),
    Metric("referral_revenue", "Referral Revenue", "revenue", "currency", "card", True,
           lambda p: sources.bizdev_summary(p)["referral_revenue"]),
    Metric("total_bd_revenue", "Business Development Revenue", "revenue", "currency", "card", True,
           lambda p: (lambda s: s["campaign_revenue"] + s["referral_revenue"])(sources.bizdev_summary(p))),
    # Pipeline.
    Metric("pipeline_value", "Pipeline Value", "pipeline", "currency", "card", False,
           lambda p: _pipeline(p)["open_value"]),
    Metric("open_opportunities", "Open Opportunities", "pipeline", "count", "card", False,
           lambda p: _pipeline(p)["counts"]["open"]),
    Metric("won_opportunities", "Won Opportunities", "pipeline", "count", "card", False,
           lambda p: _pipeline(p)["counts"]["won"]),
    Metric("pipeline_conversion", "Pipeline Conversion", "pipeline", "percent", "gauge", False,
           lambda p: (lambda w: round(w * 100, 1) if w is not None else None)(_pipeline(p)["win_rate"])),
    # Business development.
    Metric("active_campaigns", "Active Campaigns", "operations", "count", "card", False,
           sources.active_campaign_count),
    Metric("active_referral_sources", "Active Referral Sources", "operations", "count", "card", False,
           sources.active_referral_source_count),
    # Clients / growth.
    Metric("client_count", "Clients", "clients", "count", "card", False, sources.client_count),
    Metric("household_count", "Households", "clients", "count", "card", False, sources.household_count),
    Metric("organization_count", "Organizations", "clients", "count", "card", True,
           sources.organization_count),
    # Advisor production / capacity / work.
    Metric("open_work", "Open Advisor Work", "production", "count", "card", False,
           sources.open_work_total),
    Metric("open_tasks", "Open Tasks", "production", "count", "card", False, sources.open_task_count),
    # Compliance.
    Metric("open_compliance_reviews", "Open Compliance Reviews", "compliance", "count", "card", False,
           sources.open_compliance_total),
    # Reviews / plans.
    Metric("annual_reviews", "Annual Review Sessions", "operations", "count", "card", False,
           sources.annual_review_count),
    Metric("annual_reviews_completed", "Completed Annual Reviews", "operations", "count", "card", False,
           lambda p: sources.annual_review_count(p, completed_only=True)),
    Metric("business_plans", "Business Plans", "operations", "count", "card", True,
           sources.business_plan_count),
    # Activity.
    Metric("timeline_activity", "Timeline Activity", "activity", "count", "card", False,
           sources.timeline_activity_count),
    Metric("document_count", "Documents", "operations", "count", "card", False,
           sources.document_count),
    Metric("active_workflows", "Active Workflows", "operations", "count", "card", False,
           sources.active_workflow_count),
    Metric("active_conversations", "Active Conversations", "activity", "count", "card", False,
           sources.active_conversation_count),
    Metric("upcoming_meetings", "Upcoming Meetings", "activity", "count", "card", False,
           sources.upcoming_meeting_count),
    Metric("active_projects", "Active Projects", "operations", "count", "card", False,
           sources.active_project_count),
    Metric("governance_open_findings", "Open Governance Findings", "compliance", "count", "card", False,
           sources.governance_open_finding_count),
    Metric("governance_legal_holds", "Active Legal Holds", "compliance", "count", "card", False,
           sources.governance_active_legal_hold_count),
    Metric("integration_sync_failures", "Integration Sync Failures", "operations", "count", "card", False,
           sources.integration_sync_failure_count),
    Metric("integration_connector_errors", "Connector Errors", "operations", "count", "card", False,
           sources.integration_connector_error_count),
    # Enterprise Security (Phase D.25 — Analytics consumes security statistics).
    Metric("security_open_findings", "Open Security Findings", "operations", "count", "card", False,
           sources.security_open_finding_count),
    Metric("security_open_incidents", "Open Security Incidents", "operations", "count", "card", False,
           sources.security_open_incident_count),
    Metric("security_overdue_rotations", "Overdue Secret Rotations", "operations", "count", "card", False,
           sources.security_overdue_rotation_count),
    Metric("security_expired_certificates", "Expired Certificates", "operations", "count", "card", False,
           sources.security_expired_certificate_count),
    Metric("security_mfa_enabled_users", "MFA-Enabled Users", "operations", "count", "card", False,
           sources.security_mfa_enabled_user_count),
    # Enterprise Observability (Phase D.26 — Analytics consumes observability statistics).
    Metric("observability_failed_health_checks", "Failed Health Checks", "operations", "count", "card",
           False, sources.observability_failed_health_check_count),
    Metric("observability_open_alerts", "Open Operational Alerts", "operations", "count", "card", False,
           sources.observability_open_alert_count),
    Metric("observability_operational_services", "Operational Services", "operations", "count", "card",
           False, sources.observability_operational_service_count),
    Metric("observability_diagnostic_failures", "Diagnostic Failures", "operations", "count", "card",
           False, sources.observability_diagnostic_failure_count),
    Metric("observability_reliability_incidents", "Open Reliability Incidents", "operations", "count",
           "card", False, sources.observability_open_reliability_incident_count),
    # Enterprise Configuration (Phase D.27 — Analytics consumes configuration statistics).
    Metric("configuration_enabled_feature_flags", "Enabled Feature Flags", "operations", "count", "card",
           False, sources.configuration_enabled_feature_flag_count),
    Metric("configuration_drift_overrides", "Configuration Drift (overrides)", "operations", "count",
           "card", False, sources.configuration_drift_override_count),
    Metric("configuration_active_editions", "Active Editions", "operations", "count", "card", False,
           sources.configuration_active_edition_count),
    Metric("configuration_pending_changes", "Pending Configuration Reviews", "operations", "count",
           "card", False, sources.configuration_pending_change_count),
    # Runtime Configuration Engine (Phase D.28 — Analytics consumes runtime statistics).
    Metric("runtime_active_snapshots", "Runtime Snapshots", "operations", "count", "card", False,
           sources.runtime_active_snapshot_count),
    Metric("runtime_cache_hit_ratio", "Runtime Cache Hit Ratio", "operations", "percent", "card", False,
           sources.runtime_cache_hit_ratio),
    Metric("runtime_configuration_resolutions", "Configuration Resolutions", "operations", "count",
           "card", False, sources.runtime_configuration_resolution_count),
    Metric("runtime_edition_utilization", "Edition Utilization", "operations", "count", "card", False,
           sources.runtime_edition_utilization),
    Metric("runtime_active_features", "Active Runtime Features", "operations", "count", "card", False,
           sources.runtime_active_feature_count),
    # Distributed Runtime Coordination (Phase D.29 — Analytics consumes cluster statistics).
    Metric("runtime_active_workers", "Runtime Workers", "operations", "count", "card", False,
           sources.runtime_active_worker_count),
    Metric("runtime_cluster_convergence", "Cluster Convergence", "operations", "percent", "card", False,
           sources.runtime_cluster_convergence_pct),
    Metric("runtime_stale_workers", "Stale Runtime Workers", "operations", "count", "card", False,
           sources.runtime_stale_worker_count),
    Metric("runtime_generations", "Runtime Generations", "operations", "count", "card", False,
           sources.runtime_generation_count),
    # Runtime Consumption / behavioral adoption (Phase D.30 — Analytics consumes adoption statistics).
    Metric("runtime_feature_consumption", "Runtime Feature Consumption", "operations", "count", "card",
           False, sources.runtime_feature_consumption_count),
    Metric("runtime_config_lookups", "Runtime Config Lookups", "operations", "count", "card", False,
           sources.runtime_config_lookup_count),
    Metric("legacy_fallback_count", "Legacy Fallbacks", "operations", "count", "card", False,
           sources.runtime_legacy_fallback_count),
    Metric("behavior_adoption_percent", "Behavior Adoption", "operations", "percent", "card", False,
           sources.runtime_behavior_adoption_pct),
    Metric("migrated_behavior_count", "Migrated Behaviors", "operations", "count", "card", False,
           sources.runtime_migrated_behavior_count),
    # Runtime authority / governance (Phase D.31 — Analytics consumes authority statistics).
    Metric("retired_behavior_count", "Retired Behaviors", "operations", "count", "card", False,
           sources.runtime_retired_behavior_count),
    Metric("runtime_authority_percent", "Runtime Authority", "operations", "percent", "card", False,
           sources.runtime_authority_pct),
    Metric("runtime_definition_coverage", "Runtime Definition Coverage", "operations", "percent",
           "card", False, sources.runtime_definition_coverage_pct),
    Metric("runtime_governance_issues", "Runtime Governance Issues", "operations", "count", "card",
           False, sources.runtime_governance_issue_count),
    Metric("compatibility_shim_count", "Compatibility Shims", "operations", "count", "card", False,
           sources.runtime_compatibility_shim_count),
    Metric("compatibility_fallbacks", "Compatibility Fallbacks", "operations", "count", "card", False,
           sources.runtime_compatibility_fallback_count),
    # Runtime Policy Engine (Phase D.32 — Analytics consumes policy execution/coverage statistics).
    Metric("policy_evaluations", "Policy Evaluations", "operations", "count", "card", False,
           sources.policy_evaluation_count),
    Metric("policy_cache_hits", "Policy Cache Hits", "operations", "count", "card", False,
           sources.policy_cache_hit_count),
    Metric("policy_governance_issues", "Policy Governance Issues", "operations", "count", "card", False,
           sources.policy_governance_issue_count),
    Metric("policy_coverage", "Policy Coverage", "operations", "percent", "card", False,
           sources.policy_coverage_pct),
    Metric("policy_adoption_percent", "Policy Adoption", "operations", "percent", "card", False,
           sources.policy_adoption_pct),
    Metric("policy_execution_latency", "Policy Execution Latency (µs)", "operations", "number", "card",
           False, sources.policy_execution_latency_us),
    # Workflow Orchestration Engine (Phase D.33 — Analytics consumes orchestration statistics).
    Metric("workflow_launches", "Workflow Launches", "operations", "count", "card", False,
           sources.orchestration_launch_count),
    Metric("workflow_completions", "Workflow Completions", "operations", "count", "card", False,
           sources.orchestration_completion_count),
    Metric("workflow_failures", "Workflow Failures", "operations", "count", "card", False,
           sources.orchestration_failure_count),
    Metric("workflow_retries", "Workflow Retries", "operations", "count", "card", False,
           sources.orchestration_retry_count),
    Metric("workflow_replays", "Workflow Replays", "operations", "count", "card", False,
           sources.orchestration_replay_count),
    Metric("workflow_simulations", "Workflow Simulations", "operations", "count", "card", False,
           sources.orchestration_simulation_count),
    Metric("workflow_governance_issues", "Workflow Governance Issues", "operations", "count", "card",
           False, sources.orchestration_governance_issue_count),
    Metric("orchestration_coverage", "Orchestration Coverage", "operations", "percent", "card", False,
           sources.orchestration_coverage_pct),
    Metric("workflow_avg_execution_ms", "Avg Workflow Execution (ms)", "operations", "number", "card",
           False, sources.orchestration_avg_execution_ms),
    # Enterprise Domain Event Model (Phase D.34 — Analytics consumes event-model statistics).
    Metric("domain_events_published", "Domain Events Published", "operations", "count", "card", False,
           sources.domain_events_published),
    Metric("domain_events_delivered", "Domain Events Delivered", "operations", "count", "card", False,
           sources.domain_events_delivered),
    Metric("domain_events_dead_lettered", "Domain Events Dead-Lettered", "operations", "count", "card",
           False, sources.domain_events_dead_lettered),
    Metric("domain_event_contracts", "Domain Event Contracts", "operations", "count", "card", False,
           sources.domain_event_contract_count),
    Metric("domain_event_subscriptions", "Domain Event Subscriptions", "operations", "count", "card",
           False, sources.domain_event_subscription_count),
    Metric("domain_event_governance_issues", "Domain Event Governance Issues", "operations", "count",
           "card", False, sources.domain_event_governance_issue_count),
    Metric("domain_event_coverage", "Domain Event Coverage", "operations", "percent", "card", False,
           sources.domain_event_coverage_pct),
    Metric("domain_event_replays", "Domain Event Replays", "operations", "count", "card", False,
           sources.domain_event_replay_count),
    Metric("domain_event_publish_failures", "Domain Event Publish Failures", "operations", "count",
           "card", False, sources.domain_event_publish_failure_count),
    # Domain Event Producer Adoption (Phase D.35).
    Metric("domain_event_producer_adoption", "Producer Adoption", "operations", "percent", "card", False,
           sources.domain_event_producer_adoption_pct),
    Metric("domain_event_active_producers", "Active Event Producers", "operations", "count", "card",
           False, sources.domain_event_active_producer_count),
    Metric("domain_event_stale_producers", "Stale Event Producers", "operations", "count", "card", False,
           sources.domain_event_stale_producer_count),
    Metric("domain_events_awaiting_delivery", "Domain Events Awaiting Delivery", "operations", "count",
           "card", False, sources.domain_events_awaiting_delivery),
    Metric("domain_event_adopted_domains", "Adopted Event Domains", "operations", "count", "card", False,
           sources.domain_event_adopted_domain_count),
    # Read Models & Projection Engine (Phase D.36).
    Metric("projection_count", "Read Model Projections", "operations", "count", "card", False,
           sources.projection_count),
    Metric("healthy_projections", "Healthy Projections", "operations", "count", "card", False,
           sources.healthy_projection_count),
    Metric("lagging_projections", "Lagging Projections", "operations", "count", "card", False,
           sources.lagging_projection_count),
    Metric("projection_events_processed", "Projection Events Processed", "operations", "count", "card",
           False, sources.projection_events_processed),
    Metric("projection_avg_latency_ms", "Avg Projection Latency (ms)", "operations", "number", "card",
           False, sources.projection_avg_latency_ms),
    Metric("largest_projection_size", "Largest Projection (rows)", "operations", "count", "card", False,
           sources.largest_projection_size),
    Metric("projection_rebuilds", "Projection Rebuilds", "operations", "count", "card", False,
           sources.projection_rebuild_count),
    Metric("projection_replays", "Projection Replays", "operations", "count", "card", False,
           sources.projection_replay_count),
    Metric("projection_failures", "Projection Failures", "operations", "count", "card", False,
           sources.projection_failure_count),
    Metric("projection_coverage", "Projection Coverage", "operations", "percent", "card", False,
           sources.projection_coverage_pct),
    # Read Surface Adoption (Phase D.37) — projection-backed firm counts + adoption stats.
    Metric("projection_open_opportunity_count", "Open Opportunities", "operations", "count", "card",
           False, sources.projection_open_opportunity_count),
    Metric("projection_open_compliance_count", "Open Compliance Reviews", "operations", "count", "card",
           False, sources.projection_open_compliance_count),
    Metric("projection_tax_return_count", "Tax Returns", "operations", "count", "card", False,
           sources.projection_tax_return_count),
    Metric("projection_insurance_case_count", "Insurance Cases (Projection)", "operations", "count",
           "card", False, sources.projection_insurance_case_count),
    Metric("projection_benefits_enrollment_count", "Benefit Enrollments", "operations", "count", "card",
           False, sources.projection_benefits_enrollment_count),
    Metric("projection_open_exception_count", "Open Exceptions (Projection)", "operations", "count",
           "card", False, sources.projection_open_exception_count),
    Metric("projection_adoption_pct", "Read Surface Adoption", "operations", "percent", "card", False,
           sources.projection_adoption_pct),
    Metric("projection_fallback_count", "Projection Fallbacks", "operations", "count", "card", False,
           sources.projection_fallback_count),
    Metric("projection_backed_read_count", "Projection-Backed Reads", "operations", "count", "card",
           False, sources.projection_backed_read_count),
    Metric("adopted_read_surface_count", "Adopted Read Surfaces", "operations", "count", "card", False,
           sources.adopted_read_surface_count),
    # Advisor AI Assist (Phase D.42) — low-cardinality operational metrics.
    Metric("ai_assist_requests", "AI Assist Requests", "operations", "count", "card", False,
           sources.ai_assist_request_count),
    Metric("ai_assist_refusals", "AI Assist Refusals", "operations", "count", "card", False,
           sources.ai_assist_refusal_count),
    Metric("ai_assist_success_rate", "AI Assist Success Rate", "operations", "percent", "card", False,
           sources.ai_assist_success_rate),
    Metric("ai_assist_avg_latency_ms", "AI Assist Avg Latency (ms)", "operations", "number", "card",
           False, sources.ai_assist_avg_latency_ms),
    Metric("ai_assist_citation_coverage", "AI Assist Citation Coverage", "operations", "percent",
           "card", False, sources.ai_assist_citation_coverage),
    Metric("ai_assist_provider_failures", "AI Assist Provider Failures", "operations", "count", "card",
           False, sources.ai_assist_provider_failures),
    Metric("open_operational_tasks", "Open Operational Tasks", "operations", "count", "card", False,
           sources.open_operational_task_count),
    # Unified Communications & Engagement (Phase D.44) — low-cardinality operational metrics.
    Metric("engagement_interactions_composed", "Engagement Interactions Composed", "operations", "count",
           "card", False, sources.engagement_interactions_composed),
    Metric("engagement_searches", "Engagement Searches", "operations", "count", "card", False,
           sources.engagement_searches),
    Metric("engagement_adapter_failures", "Engagement Adapter Failures", "operations", "count", "card",
           False, sources.engagement_adapter_failures),
    # Enterprise Knowledge Graph (Phase D.45) — low-cardinality operational metrics.
    Metric("knowledge_traversals", "Knowledge Traversals", "operations", "count", "card", False,
           sources.knowledge_traversals),
    Metric("knowledge_explanations", "Knowledge Explanations", "operations", "count", "card", False,
           sources.knowledge_explanations),
    Metric("knowledge_searches", "Knowledge Searches", "operations", "count", "card", False,
           sources.knowledge_searches),
    Metric("knowledge_adapter_failures", "Knowledge Adapter Failures", "operations", "count", "card",
           False, sources.knowledge_adapter_failures),
    # Operational Intelligence recommendations (Phase D.46) — low-cardinality operational metrics.
    Metric("recommendations_generated", "Recommendations Generated", "operations", "count", "card", False,
           sources.recommendations_generated),
    Metric("recommendations_suppressed", "Recommendations Suppressed", "operations", "count", "card", False,
           sources.recommendations_suppressed),
    Metric("recommendation_compositions", "Recommendation Compositions", "operations", "count", "card",
           False, sources.recommendation_compositions),
    Metric("recommendation_adapter_failures", "Recommendation Adapter Failures", "operations", "count",
           "card", False, sources.recommendation_adapter_failures),
    # Compliance Intelligence / supervisory operations (Phase D.47) — low-cardinality operational metrics.
    Metric("supervisory_reviews_composed", "Supervisory Reviews Composed", "compliance", "count", "card",
           False, sources.supervisory_reviews_composed),
    Metric("supervisory_exceptions_composed", "Supervisory Exceptions Composed", "compliance", "count",
           "card", False, sources.supervisory_exceptions_composed),
    Metric("supervisory_dashboards", "Supervisory Dashboards", "compliance", "count", "card", False,
           sources.supervisory_dashboards),
    Metric("supervisory_authorization_failures", "Supervisory Authorization Failures", "compliance", "count",
           "card", False, sources.supervisory_authorization_failures),
    # Executive Reporting / firm intelligence (Phase D.48) — low-cardinality operational metrics.
    Metric("executive_dashboards_composed", "Executive Dashboards Composed", "operations", "count", "card",
           False, sources.executive_dashboards_composed),
    Metric("executive_widgets_composed", "Executive Widgets Composed", "operations", "count", "card", False,
           sources.executive_widgets_composed),
    Metric("executive_widget_failures", "Executive Widget Failures", "operations", "count", "card", False,
           sources.executive_widget_failures),
    Metric("executive_authorization_failures", "Executive Authorization Failures", "operations", "count",
           "card", False, sources.executive_authorization_failures),
    # Tax / insurance (guarded — scoped; return None if unavailable to the principal).
    Metric("tax_engagements", "Tax Engagements", "operations", "count", "card", False,
           _safe(lambda p: sources.tax_dashboard(p)["metrics"]["engagements"])),
    Metric("tax_returns_due", "Tax Returns Due (30d)", "operations", "count", "card", False,
           _safe(lambda p: sources.tax_dashboard(p)["metrics"]["due_30_days"])),
    Metric("insurance_cases", "Insurance Cases", "operations", "count", "card", False,
           _safe(lambda p: sources.insurance_dashboard(p)["sections"]["pipeline"]["case_count"])),
)

METRICS: dict[str, Metric] = {m.key: m for m in _DEFS}


def list_metrics(principal=None):
    """Metric catalog (metadata only). Executive metrics are flagged; the compute step enforces
    the capability."""
    return [{"key": m.key, "label": m.label, "category": m.category, "unit": m.unit,
             "viz": m.viz, "executive": m.executive} for m in _DEFS]


def compute_metric(principal, metric_key: str) -> dict:
    m = METRICS.get(metric_key)
    if m is None:
        return {"key": metric_key, "value": None, "error": "unknown metric"}
    # Executive metrics require the analytics.executive capability (RBAC — never bypassed) AND, when a
    # runtime feature is defined, the runtime engine's enablement (D.30 consumption). Behavior-
    # preserving: with no runtime feature defined, the legacy default (enabled) keeps behavior as-is.
    if m.executive:
        if not principal.can("analytics.executive"):
            return {"key": m.key, "label": m.label, "unit": m.unit, "category": m.category,
                    "viz": m.viz, "value": None, "restricted": True}
        from app.services.runtime import consumption
        if not consumption.feature_enabled("analytics.executive_metrics", default=True, shim=True):
            return {"key": m.key, "label": m.label, "unit": m.unit, "category": m.category,
                    "viz": m.viz, "value": None, "restricted": True, "reason": "runtime_disabled"}
    value = m.compute(principal)
    return {"key": m.key, "label": m.label, "unit": m.unit, "category": m.category,
            "viz": m.viz, "value": (_num(value) if value is not None else None),
            "available": value is not None}


def compute_many(principal, metric_keys) -> list[dict]:
    return [compute_metric(principal, k) for k in metric_keys]
