from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import MetaData, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

load_dotenv(r"C:\Client360\app\.env")

from app.db import engine

metadata = MetaData()
metadata.reflect(bind=engine)

people = metadata.tables["people"]
source_contacts = metadata.tables["source_contacts"]
person_source_links = metadata.tables["person_source_links"]
drake_returns = metadata.tables["drake_client_returns"]

SOURCE_SYSTEM = "Drake"


def clean(value):
    if value is None:
        return None
    value = str(value).replace("\x00", "").strip()
    return value or None


def normalize_email(value):
    value = clean(value)
    return value.lower() if value else None


def normalize_phone(value):
    digits = "".join(ch for ch in clean(value) or "" if ch.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]
    return digits or None


def normalize_name(first, last):
    value = " ".join(part for part in (clean(first), clean(last)) if part)
    value = re.sub(r"[^a-z0-9 ]+", "", value.lower())
    return re.sub(r"\s+", " ", value).strip() or None


def column_name(table, *names):
    for name in names:
        if name in table.c:
            return name
    return None


def value_from_raw(raw, *names):
    raw = raw or {}
    for name in names:
        value = clean(raw.get(name))
        if value:
            return value
    return None


def source_contact_values(record):
    values = {}

    available = set(source_contacts.c.keys())

    candidate_values = {
        "source_system": SOURCE_SYSTEM,
        "source_file": f"Drake {record['tax_year']}",
        "source_record_id": record["source_record_id"],
        "external_id": record["source_record_id"],
        "source_hash": hashlib.sha256(
            f"{SOURCE_SYSTEM}|{record['source_record_id']}".encode("utf-8")
        ).hexdigest(),
        "full_name": record["full_name"],
        "first_name": record["first_name"],
        "last_name": record["last_name"],
        "email": record["email"],
        "normalized_email": record["normalized_email"],
        "phone": record["phone"],
        "normalized_phone": record["normalized_phone"],
        "address": record["address"],
        "address_line1": record["address"],
        "city": record["city"],
        "state": record["state"],
        "zip": record["zip"],
        "postal_code": record["zip"],
        "date_of_birth": record["dob"],
        "dob": record["dob"],
        "raw_data": record["raw_data"],
        "active": True,
    }

    for key, value in candidate_values.items():
        if key in available:
            values[key] = value

    return values


def build_drake_contacts(connection):
    rows = connection.execute(
        select(drake_returns).order_by(
            drake_returns.c.tax_year,
            drake_returns.c.id,
        )
    ).mappings().all()

    contacts = []

    for row in rows:
        raw = dict(row.get("raw_data") or {})

        taxpayer_first = clean(row["taxpayer_first_name"])
        taxpayer_last = clean(row["taxpayer_last_name"])

        taxpayer_email = value_from_raw(raw, "Email")
        taxpayer_phone = value_from_raw(
            raw,
            "TP_Cell_Phone",
            "TP_Day_Phone",
            "TP_Eve_Phone",
        )

        contacts.append({
            "source_record_id": f"{row['tax_year']}:{row['id']}:taxpayer",
            "tax_year": row["tax_year"],
            "drake_return_id": row["id"],
            "role": "taxpayer",
            "first_name": taxpayer_first,
            "last_name": taxpayer_last,
            "full_name": " ".join(
                part for part in (taxpayer_first, taxpayer_last) if part
            ),
            "normalized_name": normalize_name(
                taxpayer_first,
                taxpayer_last,
            ),
            "email": taxpayer_email,
            "normalized_email": normalize_email(taxpayer_email),
            "phone": taxpayer_phone,
            "normalized_phone": normalize_phone(taxpayer_phone),
            "dob": row.get("taxpayer_dob"),
            "address": value_from_raw(raw, "Address"),
            "city": value_from_raw(raw, "City"),
            "state": value_from_raw(raw, "State"),
            "zip": value_from_raw(raw, "Zip"),
            "identifier_hash": row.get("taxpayer_identifier_hash"),
            "raw_data": {
                "drake_return_id": row["id"],
                "tax_year": row["tax_year"],
                "role": "taxpayer",
                "return_type": row.get("return_type"),
                "identifier_hash": row.get("taxpayer_identifier_hash"),
            },
        })

        spouse_first = clean(row.get("spouse_first_name"))
        if spouse_first:
            spouse_last = clean(row.get("spouse_last_name")) or taxpayer_last

            contacts.append({
                "source_record_id": f"{row['tax_year']}:{row['id']}:spouse",
                "tax_year": row["tax_year"],
                "drake_return_id": row["id"],
                "role": "spouse",
                "first_name": spouse_first,
                "last_name": spouse_last,
                "full_name": " ".join(
                    part for part in (spouse_first, spouse_last) if part
                ),
                "normalized_name": normalize_name(
                    spouse_first,
                    spouse_last,
                ),
                "email": None,
                "normalized_email": None,
                "phone": None,
                "normalized_phone": None,
                "dob": row.get("spouse_dob"),
                "address": value_from_raw(raw, "Address"),
                "city": value_from_raw(raw, "City"),
                "state": value_from_raw(raw, "State"),
                "zip": value_from_raw(raw, "Zip"),
                "identifier_hash": row.get("spouse_identifier_hash"),
                "raw_data": {
                    "drake_return_id": row["id"],
                    "tax_year": row["tax_year"],
                    "role": "spouse",
                    "return_type": row.get("return_type"),
                    "identifier_hash": row.get("spouse_identifier_hash"),
                },
            })

    return contacts


def load_people(connection):
    rows = connection.execute(select(people)).mappings().all()

    output = []

    dob_column = column_name(
        people,
        "date_of_birth",
        "dob",
        "birth_date",
    )

    for row in rows:
        first = clean(row.get("first_name"))
        last = clean(row.get("last_name"))

        email = clean(
            row.get("normalized_email")
            or row.get("primary_email")
            or row.get("email")
        )

        phone = clean(
            row.get("normalized_phone")
            or row.get("primary_phone")
            or row.get("phone")
        )

        output.append({
            "id": row["id"],
            "normalized_name": normalize_name(first, last),
            "normalized_email": normalize_email(email),
            "normalized_phone": normalize_phone(phone),
            "dob": row.get(dob_column) if dob_column else None,
            "city": clean(row.get("city")),
            "state": clean(row.get("state")),
            "zip": clean(
                row.get("zip")
                or row.get("postal_code")
            ),
        })

    return output


def build_indexes(person_rows):
    indexes = {
        "email": defaultdict(list),
        "phone": defaultdict(list),
        "name_dob": defaultdict(list),
        "name_city_state": defaultdict(list),
    }

    for person in person_rows:
        if person["normalized_email"]:
            indexes["email"][person["normalized_email"]].append(person)

        if person["normalized_phone"]:
            indexes["phone"][person["normalized_phone"]].append(person)

        if person["normalized_name"] and person["dob"]:
            indexes["name_dob"][
                (person["normalized_name"], str(person["dob"]))
            ].append(person)

        if (
            person["normalized_name"]
            and person["city"]
            and person["state"]
        ):
            indexes["name_city_state"][
                (
                    person["normalized_name"],
                    person["city"].lower(),
                    person["state"].lower(),
                )
            ].append(person)

    return indexes


def unique_candidate(candidates):
    ids = {candidate["id"] for candidate in candidates}
    if len(ids) != 1:
        return None
    return candidates[0]


def match_contact(contact, indexes):
    evidence = []

    if contact["normalized_email"]:
        candidate = unique_candidate(
            indexes["email"].get(contact["normalized_email"], [])
        )
        if candidate:
            evidence.append((candidate["id"], 100, "exact_email"))

    if contact["normalized_phone"]:
        candidate = unique_candidate(
            indexes["phone"].get(contact["normalized_phone"], [])
        )
        if candidate:
            evidence.append((candidate["id"], 98, "exact_phone"))

    if contact["normalized_name"] and contact["dob"]:
        candidate = unique_candidate(
            indexes["name_dob"].get(
                (
                    contact["normalized_name"],
                    str(contact["dob"]),
                ),
                [],
            )
        )
        if candidate:
            evidence.append((candidate["id"], 99, "exact_name_dob"))

    if (
        contact["normalized_name"]
        and contact["city"]
        and contact["state"]
    ):
        candidate = unique_candidate(
            indexes["name_city_state"].get(
                (
                    contact["normalized_name"],
                    contact["city"].lower(),
                    contact["state"].lower(),
                ),
                [],
            )
        )
        if candidate:
            evidence.append(
                (candidate["id"], 95, "exact_name_city_state")
            )

    if not evidence:
        return None

    by_person = defaultdict(list)
    for person_id, score, method in evidence:
        by_person[person_id].append((score, method))

    if len(by_person) != 1:
        return None

    person_id = next(iter(by_person))
    best_score, best_method = max(by_person[person_id])

    if best_score < 95:
        return None

    methods = sorted({method for _, method in by_person[person_id]})
    return {
        "person_id": person_id,
        "score": best_score,
        "method": "+".join(methods),
    }


def get_or_create_source_contact(connection, contact):
    source_id_column = column_name(
        source_contacts,
        "source_record_id",
        "external_id",
    )

    if not source_id_column:
        raise RuntimeError(
            "source_contacts has no source_record_id/external_id column"
        )

    existing = connection.execute(
        select(source_contacts.c.id).where(
            source_contacts.c.source_system == SOURCE_SYSTEM,
            source_contacts.c[source_id_column]
            == contact["source_record_id"],
        )
    ).scalar_one_or_none()

    values = source_contact_values(contact)

    if existing:
        connection.execute(
            source_contacts.update()
            .where(source_contacts.c.id == existing)
            .values(**values)
        )
        return existing, False

    source_contact_id = connection.execute(
        source_contacts.insert()
        .values(**values)
        .returning(source_contacts.c.id)
    ).scalar_one()

    return source_contact_id, True


def link_contact(connection, source_contact_id, match):
    existing = connection.execute(
        select(person_source_links.c.person_id).where(
            person_source_links.c.source_contact_id
            == source_contact_id
        )
    ).scalar_one_or_none()

    if existing:
        return False

    values = {
        "person_id": match["person_id"],
        "source_contact_id": source_contact_id,
        "match_method": match["method"],
        "match_score": match["score"],
        "confirmed": True,
    }

    connection.execute(
        pg_insert(person_source_links)
        .values(**values)
        .on_conflict_do_nothing(
            constraint="uq_person_source_link"
        )
    )

    return True


with engine.begin() as connection:
    contacts = build_drake_contacts(connection)
    person_rows = load_people(connection)
    indexes = build_indexes(person_rows)

    created = 0
    updated = 0
    linked = 0
    unmatched = 0

    for contact in contacts:
        source_contact_id, was_created = get_or_create_source_contact(
            connection,
            contact,
        )

        if was_created:
            created += 1
        else:
            updated += 1

        match = match_contact(contact, indexes)

        if not match:
            unmatched += 1
            continue

        if link_contact(connection, source_contact_id, match):
            linked += 1

print()
print("DRAKE SOURCE INTEGRATION COMPLETE")
print("=" * 50)
print(f"Drake taxpayer/spouse source records: {len(contacts):,}")
print(f"Source contacts created:             {created:,}")
print(f"Source contacts updated:             {updated:,}")
print(f"Permanent person links created:      {linked:,}")
print(f"Unmatched / review required:         {unmatched:,}")
print()
print("No new canonical people were created.")
print("Only unique, high-confidence matches were auto-linked.")


