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
microsoft_accounts = metadata.tables["microsoft_accounts"]
timeline_events = metadata.tables["timeline_events"]
microsoft_unmatched_messages = metadata.tables["microsoft_unmatched_messages"]
microsoft_unmatched_calendar_attendees = metadata.tables[
    "microsoft_unmatched_calendar_attendees"
]
