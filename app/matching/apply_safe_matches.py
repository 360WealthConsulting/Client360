import argparse
import os
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, insert, select

load_dotenv("app/.env")

database_url = os.getenv("DATABASE_URL")
if not database_url:
	raise RuntimeError("DATABASE_URL is missing from app/.env")

engine = create_engine(database_url)

metadata = MetaData()
metadata.reflect(bind=engine)

source_contacts = metadata.tables["source_contacts"]
people = metadata.tables["people"]
person_source_links = metadata.tables["person_source_links"]


def clean(value):
	if value is None:
		return None

	value = str(value).strip()
	return value or None


def choose_best_value(members, field_name):
	preferred_order = {
		"Wealthbox": 1,
		"Schwab Profile": 2,
		"Dave Ramsey": 3,
	}

	ordered_members = sorted(
		members,
		key=lambda member: preferred_order.get(
			member["source_system"],
			99,
		),
	)

	for member in ordered_members:
		value = clean(member[field_name])

		if value:
			return value

	return None


def load_records(connection):
	return connection.execute(
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
			source_contacts.c.city,
			source_contacts.c.state,
		)
	).mappings().all()


def build_safe_groups(records):
	groups = defaultdict(list)

	for record in records:
		email = clean(record["normalized_email"])
		phone = clean(record["normalized_phone"])

		if email and phone:
			groups[(email, phone)].append(record)

	safe_groups = []

	for key, members in groups.items():
		systems = {
			member["source_system"]
			for member in members
		}

		if len(systems) < 2:
			continue

		if len(members) != len(systems):
			continue

		names = {
			clean(member["full_name"])
			or " ".join(
				part
				for part in [
					clean(member["first_name"]),
					clean(member["last_name"]),
				]
				if part
			)
			for member in members
		}

		names = {
			name.lower()
			for name in names
			if name
		}

		if not names:
			continue

		safe_groups.append((key, members))

	return safe_groups


def apply_groups(connection, groups):
	people_created = 0
	links_created = 0

	for _, members in groups:
		first_name = choose_best_value(members, "first_name")
		last_name = choose_best_value(members, "last_name")
		full_name = choose_best_value(members, "full_name")
		email = choose_best_value(members, "email")
		phone = choose_best_value(members, "phone")
		city = choose_best_value(members, "city")
		state = choose_best_value(members, "state")

		person_id = connection.execute(
			insert(people)
			.values(
				first_name=first_name,
				last_name=last_name,
				full_name=full_name,
				email=email,
				phone=phone,
				city=city,
				state=state,
			)
			.returning(people.c.id)
		).scalar_one()

		people_created += 1

		for member in members:
			connection.execute(
				insert(person_source_links).values(
					person_id=person_id,
					source_contact_id=member["id"],
					source_system=member["source_system"],
					match_method="exact_email_phone",
					confidence_score=100,
				)
			)

			links_created += 1

	return people_created, links_created


def main():
	parser = argparse.ArgumentParser()

	parser.add_argument(
		"--apply",
		action="store_true",
		help="Actually write people and links to the database.",
	)

	args = parser.parse_args()

	with engine.connect() as connection:
		records = load_records(connection)
		groups = build_safe_groups(records)

	print()
	print("CLIENT360 SAFE MATCH APPLY")
	print("=" * 50)
	print(f"Safe groups found: {len(groups):,}")

	if not args.apply:
		print("Dry run only. No records were changed.")
		print("Run again with --apply to write records.")
		return

	with engine.begin() as connection:
		people_created, links_created = apply_groups(
			connection,
			groups,
		)

	print(f"People created: {people_created:,}")
	print(f"Source links created: {links_created:,}")


if __name__ == "__main__":
	main()

