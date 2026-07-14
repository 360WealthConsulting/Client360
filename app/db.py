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
