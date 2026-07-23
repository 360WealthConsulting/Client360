import os

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine

load_dotenv("app/.env")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing from app/.env")

engine = create_engine(DATABASE_URL)

metadata = MetaData()
metadata.reflect(bind=engine)

source_contacts = metadata.tables["source_contacts"]
people = metadata.tables["people"]
accounts = metadata.tables["accounts"]
households = metadata.tables["households"]
person_source_links = metadata.tables["person_source_links"]
match_review_decisions = metadata.tables["match_review_decisions"]
tasks = metadata.tables["tasks"]
activities = metadata.tables["activities"]
household_relationships = metadata.tables["household_relationships"]
microsoft_accounts = metadata.tables["microsoft_accounts"]

documents = metadata.tables["documents"]
microsoft_drives = metadata.tables["microsoft_drives"]
microsoft_documents = metadata.tables["microsoft_documents"]
microsoft_document_matching_rules = metadata.tables[
    "microsoft_document_matching_rules"
]
relationship_types = metadata.tables["relationship_types"]
relationship_entities = metadata.tables["relationship_entities"]
relationships = metadata.tables["relationships"]
microsoft_accounts = metadata.tables["microsoft_accounts"]
timeline_events = metadata.tables["timeline_events"]
microsoft_unmatched_messages = metadata.tables["microsoft_unmatched_messages"]
microsoft_unmatched_calendar_attendees = metadata.tables[
    "microsoft_unmatched_calendar_attendees"
]
custodians = metadata.tables["custodians"]
account_registrations = metadata.tables["account_registrations"]
securities = metadata.tables["securities"]
portfolio_import_runs = metadata.tables["portfolio_import_runs"]
account_holdings = metadata.tables["account_holdings"]
position_snapshots = metadata.tables["position_snapshots"]
tax_lots = metadata.tables["tax_lots"]
portfolio_transactions = metadata.tables["portfolio_transactions"]
cash_snapshots = metadata.tables["cash_snapshots"]
performance_snapshots = metadata.tables["performance_snapshots"]
billing_snapshots = metadata.tables["billing_snapshots"]
account_beneficiaries = metadata.tables["account_beneficiaries"]
household_portfolio_snapshots = metadata.tables["household_portfolio_snapshots"]
users = metadata.tables["users"]
teams = metadata.tables["teams"]
team_memberships = metadata.tables["team_memberships"]
capabilities = metadata.tables["capabilities"]
roles = metadata.tables["roles"]
role_capabilities = metadata.tables["role_capabilities"]
user_roles = metadata.tables["user_roles"]
record_assignments = metadata.tables["record_assignments"]
user_sessions = metadata.tables["user_sessions"]
audit_events = metadata.tables["audit_events"]
assignment_rules = metadata.tables["assignment_rules"]
workflow_instances = metadata.tables["workflow_instances"]
workflow_steps = metadata.tables["workflow_steps"]
work_assignment_details = metadata.tables["work_assignment_details"]
assignment_events = metadata.tables["assignment_events"]
work_queues = metadata.tables["work_queues"]
work_approvals = metadata.tables["work_approvals"]
workflow_templates = metadata.tables["workflow_templates"]
workflow_template_steps = metadata.tables["workflow_template_steps"]
workflow_step_dependencies = metadata.tables["workflow_step_dependencies"]
workflow_events = metadata.tables["workflow_events"]
automation_triggers = metadata.tables["automation_triggers"]
automation_actions = metadata.tables["automation_actions"]
workflow_escalations = metadata.tables["workflow_escalations"]
portal_accounts = metadata.tables["portal_accounts"]
portal_access_grants = metadata.tables["portal_access_grants"]
portal_invitations = metadata.tables["portal_invitations"]
portal_auth_tokens = metadata.tables["portal_auth_tokens"]
portal_devices = metadata.tables["portal_devices"]
portal_sessions = metadata.tables["portal_sessions"]
portal_threads = metadata.tables["portal_threads"]
portal_thread_participants = metadata.tables["portal_thread_participants"]
portal_messages = metadata.tables["portal_messages"]
portal_message_receipts = metadata.tables["portal_message_receipts"]
portal_message_attachments = metadata.tables["portal_message_attachments"]
portal_document_requests = metadata.tables["portal_document_requests"]
document_versions = metadata.tables["document_versions"]
portal_notifications = metadata.tables["portal_notifications"]
signature_requests = metadata.tables["signature_requests"]
tax_firms = metadata.tables["tax_firms"]
tax_offices = metadata.tables["tax_offices"]
tax_office_memberships = metadata.tables["tax_office_memberships"]
tax_years = metadata.tables["tax_years"]
filing_jurisdictions = metadata.tables["filing_jurisdictions"]
tax_return_types = metadata.tables["tax_return_types"]
tax_filing_statuses = metadata.tables["tax_filing_statuses"]
tax_seasons = metadata.tables["tax_seasons"]
tax_calendars = metadata.tables["tax_calendars"]
tax_deadline_rules = metadata.tables["tax_deadline_rules"]
tax_engagements = metadata.tables["tax_engagements"]
tax_engagement_returns = metadata.tables["tax_engagement_returns"]
tax_deadlines = metadata.tables["tax_deadlines"]
tax_workflow_links = metadata.tables["tax_workflow_links"]
engagement_letter_templates = metadata.tables["engagement_letter_templates"]
tax_engagement_letters = metadata.tables["tax_engagement_letters"]
tax_organizer_templates = metadata.tables["tax_organizer_templates"]
tax_organizers = metadata.tables["tax_organizers"]
tax_questionnaire_templates = metadata.tables["tax_questionnaire_templates"]
tax_questionnaire_questions = metadata.tables["tax_questionnaire_questions"]
tax_questionnaires = metadata.tables["tax_questionnaires"]
tax_questionnaire_answers = metadata.tables["tax_questionnaire_answers"]
tax_checklist_templates = metadata.tables["tax_checklist_templates"]
tax_checklist_template_items = metadata.tables["tax_checklist_template_items"]
tax_checklist_items = metadata.tables["tax_checklist_items"]
tax_missing_items = metadata.tables["tax_missing_items"]
tax_return_lifecycle_events = metadata.tables["tax_return_lifecycle_events"]
tax_return_reviews = metadata.tables["tax_return_reviews"]
tax_review_corrections = metadata.tables["tax_review_corrections"]
tax_client_approvals = metadata.tables["tax_client_approvals"]
tax_filing_events = metadata.tables["tax_filing_events"]
tax_document_links = metadata.tables["tax_document_links"]
tax_document_classifications = metadata.tables["tax_document_classifications"]
tax_document_match_evidence = metadata.tables["tax_document_match_evidence"]
tax_document_review_events = metadata.tables["tax_document_review_events"]

# Exception Engine (Release 0.9.10 / Sprint 5.5 — ADR-17, platform-wide, tax domain first)
exception_types = metadata.tables["exception_types"]
exceptions = metadata.tables["exceptions"]
exception_events = metadata.tables["exception_events"]

# Employer Operations / Employee Benefits (Release 0.9.11 — ADR-18, Phase 1 schema)
organization_profiles = metadata.tables["organization_profiles"]
relationship_ownership = metadata.tables["relationship_ownership"]
service_lines = metadata.tables["service_lines"]
organization_service_lines = metadata.tables["organization_service_lines"]
organization_service_roles = metadata.tables["organization_service_roles"]
engagements = metadata.tables["engagements"]
service_revenue = metadata.tables["service_revenue"]
benefit_plan_types = metadata.tables["benefit_plan_types"]
benefit_providers = metadata.tables["benefit_providers"]
benefit_plans = metadata.tables["benefit_plans"]
benefit_retirement_plan_details = metadata.tables["benefit_retirement_plan_details"]
benefit_plan_years = metadata.tables["benefit_plan_years"]
benefit_employments = metadata.tables["benefit_employments"]
benefit_enrollments = metadata.tables["benefit_enrollments"]
benefit_retirement_elections = metadata.tables["benefit_retirement_elections"]
benefit_dependents = metadata.tables["benefit_dependents"]
benefit_document_links = metadata.tables["benefit_document_links"]
benefit_provider_connections = metadata.tables["benefit_provider_connections"]
benefit_obligation_templates = metadata.tables["benefit_obligation_templates"]
benefit_obligations = metadata.tables["benefit_obligations"]

# Insurance Operations (Release 0.10.0, Phase 0)
insurance_carrier_profiles = metadata.tables["insurance_carrier_profiles"]
insurance_product_families = metadata.tables["insurance_product_families"]
insurance_product_versions = metadata.tables["insurance_product_versions"]
insurance_product_rider_compatibility = metadata.tables["insurance_product_rider_compatibility"]
insurance_cases = metadata.tables["insurance_cases"]
insurance_policies = metadata.tables["insurance_policies"]
insurance_coverages = metadata.tables["insurance_coverages"]
insurance_riders = metadata.tables["insurance_riders"]
insurance_policy_values = metadata.tables["insurance_policy_values"]
insurance_policy_parties = metadata.tables["insurance_policy_parties"]
insurance_policy_producers = metadata.tables["insurance_policy_producers"]
insurance_policy_relationships = metadata.tables["insurance_policy_relationships"]
insurance_requirements = metadata.tables["insurance_requirements"]
insurance_policy_reviews = metadata.tables["insurance_policy_reviews"]
insurance_licenses = metadata.tables["insurance_licenses"]
insurance_ce_records = metadata.tables["insurance_ce_records"]

# Insurance commissions (Release 0.10.0, Phase 5)
insurance_commissions = metadata.tables["insurance_commissions"]
insurance_commission_statements = metadata.tables["insurance_commission_statements"]
insurance_commission_statement_lines = metadata.tables["insurance_commission_statement_lines"]

# Compliance review + decision ledger (Phase D.7)
compliance_reviews = metadata.tables["compliance_reviews"]
compliance_decisions = metadata.tables["compliance_decisions"]
reviewer_authorities = metadata.tables["reviewer_authorities"]

# Reviewer authority administration (Phase D.8)
reviewer_authority_events = metadata.tables["reviewer_authority_events"]

# Advisor work management (Phase D.9)
advisor_work_items = metadata.tables["advisor_work_items"]
advisor_work_events = metadata.tables["advisor_work_events"]

# Annual review workspace (Phase D.11)
annual_review_sessions = metadata.tables["annual_review_sessions"]

# Business owner planning workspace (Phase D.12)
business_planning_profiles = metadata.tables["business_planning_profiles"]

# Opportunity & pipeline domain (Phase D.13)
opportunity_pipelines = metadata.tables["opportunity_pipelines"]
opportunity_stages = metadata.tables["opportunity_stages"]
opportunities = metadata.tables["opportunities"]
opportunity_participants = metadata.tables["opportunity_participants"]
opportunity_events = metadata.tables["opportunity_events"]
opportunity_activities = metadata.tables["opportunity_activities"]
opportunity_work_links = metadata.tables["opportunity_work_links"]

# Campaigns, referral sources & attribution (Phase D.14)
campaigns = metadata.tables["campaigns"]
campaign_events = metadata.tables["campaign_events"]
campaign_activities = metadata.tables["campaign_activities"]
campaign_documents = metadata.tables["campaign_documents"]
referral_sources = metadata.tables["referral_sources"]
referral_source_advisors = metadata.tables["referral_source_advisors"]
referral_source_events = metadata.tables["referral_source_events"]
opportunity_attributions = metadata.tables["opportunity_attributions"]

# Enterprise analytics (Phase D.15)
analytics_targets = metadata.tables["analytics_targets"]
analytics_snapshots = metadata.tables["analytics_snapshots"]
analytics_dashboards = metadata.tables["analytics_dashboards"]
analytics_dashboard_widgets = metadata.tables["analytics_dashboard_widgets"]

# Document platform (Phase D.16). document_versions already exists (client portal) and is
# bound above; D.16 extends it additively.
document_folders = metadata.tables["document_folders"]
document_retention_policies = metadata.tables["document_retention_policies"]
document_relationships = metadata.tables["document_relationships"]
document_events = metadata.tables["document_events"]

# Communications & Client Engagement platform (Phase D.18). Authoritative for communication
# metadata only; anchors to people/households/organizations are references, not ownership.
communication_templates = metadata.tables["communication_templates"]
communication_conversations = metadata.tables["communication_conversations"]
communication_threads = metadata.tables["communication_threads"]
communication_messages = metadata.tables["communication_messages"]
communication_recipients = metadata.tables["communication_recipients"]
communication_deliveries = metadata.tables["communication_deliveries"]
communication_attachments = metadata.tables["communication_attachments"]
communication_events = metadata.tables["communication_events"]

# Scheduling & Meeting Management platform (Phase D.19). Authoritative for scheduling metadata
# only; anchors and cross-domain links (person/household/organization, opportunity, annual review,
# conversation, workflow, advisor work, document, Microsoft 365 event) are references, not ownership.
meeting_templates = metadata.tables["meeting_templates"]
scheduling_resources = metadata.tables["scheduling_resources"]
meetings = metadata.tables["meetings"]
meeting_attendees = metadata.tables["meeting_attendees"]
meeting_resource_bookings = metadata.tables["meeting_resource_bookings"]
meeting_reminders = metadata.tables["meeting_reminders"]
meeting_followups = metadata.tables["meeting_followups"]
scheduling_events = metadata.tables["scheduling_events"]

# Enterprise Operations platform (Phase D.20). Authoritative for firm operational metadata only;
# every client/business link is an optional reference, never ownership. Advisor Work remains the
# authoritative client-work domain.
project_templates = metadata.tables["project_templates"]
operational_resources = metadata.tables["operational_resources"]
projects = metadata.tables["projects"]
project_phases = metadata.tables["project_phases"]
project_milestones = metadata.tables["project_milestones"]
operational_tasks = metadata.tables["operational_tasks"]
operational_task_dependencies = metadata.tables["operational_task_dependencies"]
operational_checklist_items = metadata.tables["operational_checklist_items"]
capacity_plans = metadata.tables["capacity_plans"]
operational_issues = metadata.tables["operational_issues"]
operational_comments = metadata.tables["operational_comments"]
operations_events = metadata.tables["operations_events"]

# Enterprise Reporting platform (Phase D.21). A composition layer: owns reporting metadata only
# (definitions/config); KPI values are composed from Analytics at render time, never persisted.
report_templates = metadata.tables["report_templates"]
reporting_kpi_groups = metadata.tables["reporting_kpi_groups"]
reporting_scorecards = metadata.tables["reporting_scorecards"]
report_definitions = metadata.tables["report_definitions"]
reporting_dashboards = metadata.tables["reporting_dashboards"]
reporting_widgets = metadata.tables["reporting_widgets"]
reporting_saved_views = metadata.tables["reporting_saved_views"]
reporting_export_profiles = metadata.tables["reporting_export_profiles"]
report_schedules = metadata.tables["report_schedules"]
reports = metadata.tables["reports"]
reporting_events = metadata.tables["reporting_events"]

# Enterprise Automation platform (Phase D.22). Authoritative orchestration domain: owns execution
# metadata only (jobs, schedules, runs, queues, policies, windows, workers, locks); dispatches to
# existing services via the job_type map. Never owns business records.
automation_retry_policies = metadata.tables["automation_retry_policies"]
automation_failure_policies = metadata.tables["automation_failure_policies"]
automation_queues = metadata.tables["automation_queues"]
automation_windows = metadata.tables["automation_windows"]
automation_job_templates = metadata.tables["automation_job_templates"]
automation_jobs = metadata.tables["automation_jobs"]
automation_schedules = metadata.tables["automation_schedules"]
automation_workers = metadata.tables["automation_workers"]
automation_worker_heartbeats = metadata.tables["automation_worker_heartbeats"]
automation_execution_locks = metadata.tables["automation_execution_locks"]
automation_runs = metadata.tables["automation_runs"]
automation_events = metadata.tables["automation_events"]

# Data Governance platform (Phase D.23). Authoritative governance domain: owns governance metadata
# only (findings, duplicates, merge decisions, retention assignments, legal holds, deletion reviews,
# cases). References canonical records; never owns them. Reuses matching/merge/retention infra.
governance_data_domains = metadata.tables["governance_data_domains"]
governance_data_elements = metadata.tables["governance_data_elements"]
governance_lineage = metadata.tables["governance_lineage"]
governance_quality_rules = metadata.tables["governance_quality_rules"]
governance_quality_checks = metadata.tables["governance_quality_checks"]
governance_quality_findings = metadata.tables["governance_quality_findings"]
governance_duplicate_candidates = metadata.tables["governance_duplicate_candidates"]
governance_survivorship_rules = metadata.tables["governance_survivorship_rules"]
governance_merge_decisions = metadata.tables["governance_merge_decisions"]
governance_retention_assignments = metadata.tables["governance_retention_assignments"]
governance_legal_holds = metadata.tables["governance_legal_holds"]
governance_deletion_requests = metadata.tables["governance_deletion_requests"]
governance_cases = metadata.tables["governance_cases"]
governance_events = metadata.tables["governance_events"]

# Enterprise Integration platform (Phase D.24). Authoritative integration domain: owns integration
# metadata only (providers, connectors, credential references, sync profiles/runs/conflicts, webhook
# endpoints/subscriptions/deliveries, API clients/usage, event definitions/subscriptions, data
# profiles). References canonical records; never owns them. Reuses importers/OAuth/outbox/crypto.
integration_providers = metadata.tables["integration_providers"]
integration_credential_references = metadata.tables["integration_credential_references"]
integration_connectors = metadata.tables["integration_connectors"]
integration_sync_profiles = metadata.tables["integration_sync_profiles"]
integration_sync_runs = metadata.tables["integration_sync_runs"]
integration_sync_conflicts = metadata.tables["integration_sync_conflicts"]
integration_webhook_endpoints = metadata.tables["integration_webhook_endpoints"]
integration_webhook_subscriptions = metadata.tables["integration_webhook_subscriptions"]
integration_webhook_deliveries = metadata.tables["integration_webhook_deliveries"]
integration_api_clients = metadata.tables["integration_api_clients"]
integration_api_usage = metadata.tables["integration_api_usage"]
integration_event_definitions = metadata.tables["integration_event_definitions"]
integration_event_subscriptions = metadata.tables["integration_event_subscriptions"]
integration_data_profiles = metadata.tables["integration_data_profiles"]
integration_events = metadata.tables["integration_events"]

# Enterprise Security platform (Phase D.25). Authoritative security domain: owns security metadata
# only (policies, configurations, identity/authentication/federation providers, secret references,
# certificate references, exceptions, incidents, findings). References users/roles/capabilities/
# auth/M365/Integration/Governance/Compliance; never a source of truth for business entities. Reuses
# the existing authentication, RBAC, record-scope, Fernet crypto, and audit hash-chain — never
# replaces login/OAuth and never stores a plaintext secret.
security_policies = metadata.tables["security_policies"]
security_configurations = metadata.tables["security_configurations"]
security_identity_providers = metadata.tables["security_identity_providers"]
security_secret_references = metadata.tables["security_secret_references"]
security_certificate_references = metadata.tables["security_certificate_references"]
security_exceptions = metadata.tables["security_exceptions"]
security_incidents = metadata.tables["security_incidents"]
security_findings = metadata.tables["security_findings"]
security_events = metadata.tables["security_events"]

# Enterprise Observability platform (Phase D.26). Authoritative platform-operations domain: owns
# observability metadata only (services + dependencies, health checks/snapshots, diagnostics,
# telemetry sources/metrics, alert rules/alerts/suppressions, runtime snapshots, environment/
# deployment references, maintenance windows, reliability incidents/findings). References
# Automation/Integration/Security/Analytics/Timeline/Audit; never a source of truth for operational
# or business entities. Reuses the existing health endpoints, scheduler snapshot, logging, and the
# notification ledger — never replaces runtime health/logging/exception handling.
observability_environment_profiles = metadata.tables["observability_environment_profiles"]
observability_deployment_references = metadata.tables["observability_deployment_references"]
observability_services = metadata.tables["observability_services"]
observability_service_dependencies = metadata.tables["observability_service_dependencies"]
observability_health_checks = metadata.tables["observability_health_checks"]
observability_health_snapshots = metadata.tables["observability_health_snapshots"]
observability_diagnostic_checks = metadata.tables["observability_diagnostic_checks"]
observability_diagnostic_results = metadata.tables["observability_diagnostic_results"]
observability_telemetry_sources = metadata.tables["observability_telemetry_sources"]
observability_telemetry_metrics = metadata.tables["observability_telemetry_metrics"]
observability_maintenance_windows = metadata.tables["observability_maintenance_windows"]
observability_alert_rules = metadata.tables["observability_alert_rules"]
observability_alert_suppressions = metadata.tables["observability_alert_suppressions"]
observability_alerts = metadata.tables["observability_alerts"]
observability_runtime_snapshots = metadata.tables["observability_runtime_snapshots"]
observability_reliability_incidents = metadata.tables["observability_reliability_incidents"]
observability_reliability_findings = metadata.tables["observability_reliability_findings"]
observability_events = metadata.tables["observability_events"]

# Enterprise Configuration platform (Phase D.27). Authoritative platform-configuration domain: owns
# configuration governance metadata only (categories/sets/items/versions, environment overrides,
# tenant/org/user preferences, feature groups/flags/rollouts, editions/edition-capabilities/license-
# policies/edition-assignments, platform options, administrative policies, runtime-setting references,
# snapshots, changes). References Security/Observability/Integration/Automation/Analytics/Timeline/
# Audit and the RBAC capabilities; never the source of truth for operational/business entities and
# never replaces the runtime configuration/env loaders.
configuration_categories = metadata.tables["configuration_categories"]
configuration_sets = metadata.tables["configuration_sets"]
configuration_items = metadata.tables["configuration_items"]
configuration_versions = metadata.tables["configuration_versions"]
configuration_environment_overrides = metadata.tables["configuration_environment_overrides"]
configuration_preferences = metadata.tables["configuration_preferences"]
configuration_feature_groups = metadata.tables["configuration_feature_groups"]
configuration_feature_flags = metadata.tables["configuration_feature_flags"]
configuration_feature_rollouts = metadata.tables["configuration_feature_rollouts"]
configuration_editions = metadata.tables["configuration_editions"]
configuration_edition_capabilities = metadata.tables["configuration_edition_capabilities"]
configuration_license_policies = metadata.tables["configuration_license_policies"]
configuration_edition_assignments = metadata.tables["configuration_edition_assignments"]
configuration_platform_options = metadata.tables["configuration_platform_options"]
configuration_administrative_policies = metadata.tables["configuration_administrative_policies"]
configuration_runtime_setting_references = metadata.tables["configuration_runtime_setting_references"]
configuration_snapshots = metadata.tables["configuration_snapshots"]
configuration_changes = metadata.tables["configuration_changes"]
configuration_events = metadata.tables["configuration_events"]

# Enterprise Runtime Configuration Engine (Phase D.28). The runtime EVALUATION layer over the D.27
# configuration metadata. Owns only its immutable effective-config snapshots and an append-only
# lifecycle ledger; it reads the D.27 configuration_* metadata (never writes it) and reuses the
# existing startup/middleware/scheduler/observability/analytics. Both runtime tables are append-only.
runtime_config_snapshots = metadata.tables["runtime_config_snapshots"]
runtime_events = metadata.tables["runtime_events"]

# Distributed Runtime Coordination (Phase D.29). Makes the D.28 runtime engine cluster-safe using the
# transactional outbox as the sole coordination bus. Owns coordination metadata only — a worker
# registry + heartbeat log, a runtime version/generation history with convergence tracking, and an
# append-only coordination ledger. Owns no configuration metadata and performs no evaluation.
runtime_workers = metadata.tables["runtime_workers"]
runtime_worker_heartbeats = metadata.tables["runtime_worker_heartbeats"]
runtime_generations = metadata.tables["runtime_generations"]
runtime_coordination_events = metadata.tables["runtime_coordination_events"]

# Runtime Behavior registry (Phase D.30). Durable catalog of which application behaviors have been
# migrated to consume the runtime engine (adoption tracking). Owns no configuration metadata and
# performs no evaluation — the runtime engine remains the sole evaluator.
runtime_behaviors = metadata.tables["runtime_behaviors"]
