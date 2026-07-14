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
