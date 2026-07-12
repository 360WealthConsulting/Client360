
import os
import re
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, select

load_dotenv("app/.env")

database_url = os.getenv("DATABASE_URL")
if not database_url:
	raise RuntimeError("DATABASE_URL is missing from app/.env")

engine = create_engine(database_url)

metadata = MetaData()
metadata.reflect(bind=engine)

source_contacts = metadata.tables["source_contacts"]


def clean(value):
	if value is None:
		return None

	value = str(value).strip()
	return value or None


def normalize_email(value):
	value = clean(value)
	return value.lower() if value else None


def normalize_phone(value):
	value = clean(value)

	if not value:
		return None

	digits = re.sub(r"\D", "", value)

	if len(digits) == 11 and digits.startswith("1"):
		digits = digits[1:]

	return digits if len(digits) >= 7 else None


with engine.connect() as connection:
	records = connection.execute(
		select(
			source_contacts.c.id,
			source_contacts.c.source_system,
			source_contacts.c.full_name,
			source_contacts.c.first_name,
			source_contacts.c.last_name,
			source_contacts.c.email,
			source_contacts.c.normalized_email,
			source_contacts.c.phone,
			source_contacts.c.normalized_phone,
		)
	).mappings().all()


email_groups = defaultdict(list)
phone_groups = defaultdict(list)

for record in records:
	email = (
		clean(record["normalized_email"])
		or normalize_email(record["email"])
	)

	phone = (
		clean(record["normalized_phone"])
		or normalize_phone(record["phone"])
	)

	if email:
		email_groups[email].append(record)

	if phone:
		phone_groups[phone].append(record)


def cross_source_matches(groups):
	matches = []

	for key, members in groups.items():
		systems = {member["source_system"] for member in members}

		if len(systems) >= 2:
			matches.append((key, members, systems))

	return matches


email_matches = cross_source_matches(email_groups)
phone_matches = cross_source_matches(phone_groups)

email_ids = {
	member["id"]
	for _, members, _ in email_matches
	for member in members
}

phone_ids = {
	member["id"]
	for _, members, _ in phone_matches
	for member in members
}

both_ids = email_ids & phone_ids

print()
print("CLIENT360 EXACT-MATCH AUDIT")
print("=" * 50)
print(f"Source records reviewed: {len(records):,}")
print(f"Cross-source email groups: {len(email_matches):,}")
print(f"Records in email matches: {len(email_ids):,}")
print(f"Cross-source phone groups: {len(phone_matches):,}")
print(f"Records in phone matches: {len(phone_ids):,}")
print(f"Records matching by both: {len(both_ids):,}")
print()
print("Audit complete. No database records were changed.")
