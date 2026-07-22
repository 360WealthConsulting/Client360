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
