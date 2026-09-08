"""Import Drake returns as source contacts and link the unambiguous ones to canonical people.

Matching semantics are NOT defined here. They live in ``app.services.drake_linkage_evidence`` and are
shared with the identity review queue, because this script and that queue had drifted into two
different opinions about the same evidence -- including two incompatible ``normalize_name`` helpers.

What this script may do is deliberately narrow: it creates a link only where the shared evaluator
returns AUTO_LINK, and only where no link for that source contact exists yet. It never rewrites,
re-scores or removes a link that is already recorded.
"""
from __future__ import annotations

import hashlib

from dotenv import load_dotenv
from sqlalchemy import MetaData, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

load_dotenv(r"C:\Client360\app\.env")

from app.db import engine  # noqa: E402
from app.services.drake_linkage_evidence import (  # noqa: E402
    SPOUSE,
    TAXPAYER,
    Roster,
    RosterPerson,
    build_identity_evidence,
    clean,
    evaluate,
    join_name,
    normalize_email,
    normalize_name,
    normalize_phone,
)
from app.services.link_trust import SOURCE_MACHINE  # noqa: E402

metadata = MetaData()
metadata.reflect(bind=engine)

people = metadata.tables["people"]
source_contacts = metadata.tables["source_contacts"]
person_source_links = metadata.tables["person_source_links"]
drake_returns = metadata.tables["drake_client_returns"]

SOURCE_SYSTEM = "Drake"


def normalize_name_parts(first, last):
    """This script's historical two-argument form, expressed through the shared normaliser."""
    return normalize_name(join_name(first, last))


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
            f"{SOURCE_SYSTEM}|{record['source_record_id']}".encode()
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
        spouse_present = bool(clean(row.get("spouse_first_name")))

        # ``Email`` carries no attribution in Drake; ``TP_*`` phones are explicitly the taxpayer's.
        # The evaluator decides what that entitles each role to claim -- see build_identity_evidence.
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
            "normalized_name": normalize_name_parts(
                taxpayer_first,
                taxpayer_last,
            ),
            "has_spouse": spouse_present,
            "taxpayer_name": join_name(taxpayer_first, taxpayer_last),
            "spouse_name": join_name(clean(row.get("spouse_first_name")),
                                     clean(row.get("spouse_last_name")) or taxpayer_last),
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
                "normalized_name": normalize_name_parts(
                    spouse_first,
                    spouse_last,
                ),
                "has_spouse": True,
                "taxpayer_name": join_name(taxpayer_first, taxpayer_last),
                "spouse_name": join_name(spouse_first, spouse_last),
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
    """The canonical roster the evaluator matches against.

    ONLY canonical ``people`` columns are read. Drake's own source contacts are deliberately NOT
    folded in: a Drake identity whose email matches the Drake source_contact that a previous run
    created is the link vouching for itself, which is not evidence of anything.
    """
    rows = connection.execute(select(people)).mappings().all()

    dob_column = column_name(people, "date_of_birth", "dob", "birth_date")

    roster = []
    for row in rows:
        email = clean(row.get("normalized_email") or row.get("primary_email") or row.get("email"))
        phone = clean(row.get("normalized_phone") or row.get("primary_phone") or row.get("phone"))
        roster.append(RosterPerson(
            person_id=row["id"],
            full_name=clean(row.get("full_name"))
            or join_name(row.get("first_name"), row.get("last_name")),
            dob=row.get(dob_column) if dob_column else None,
            emails=frozenset({email} - {None}),
            phones=frozenset({phone} - {None}),
            city=clean(row.get("city")),
            state=clean(row.get("state")),
        ))

    return roster


def match_contact(contact, roster):
    """Resolve one Drake contact through the shared evaluator.

    Returns a match dict only for AUTO_LINK. REVIEW_CANDIDATE, AMBIGUOUS and NO_MATCH all return
    None here: a candidate is not a decision, and this script's only power is to write links.
    """
    evidence = build_identity_evidence(
        contact.get("identifier_hash") or contact["source_record_id"],
        SPOUSE if contact["role"] == "spouse" else TAXPAYER,
        taxpayer_name=contact.get("taxpayer_name"),
        spouse_name=contact.get("spouse_name"),
        emails=[contact["email"]] if contact["email"] else (),
        phones=[contact["phone"]] if contact["phone"] else (),
        dob=contact.get("dob"),
        city=contact.get("city"),
        state=contact.get("state"),
        has_spouse=bool(contact.get("has_spouse")),
    )

    decision = evaluate(evidence, roster)
    if not decision.is_auto_link:
        return None

    return {
        "person_id": decision.person_id,
        "score": decision.confidence,
        "method": decision.method,
        "trust_level": decision.trust_level,
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

    # Trust is recorded explicitly rather than left to be inferred from ``confirmed``, which is
    # hardcoded True here and means seven different things across the table. See app.services.link_trust.
    values = {
        "person_id": match["person_id"],
        "source_contact_id": source_contact_id,
        "match_method": match["method"],
        "match_score": match["score"],
        "confirmed": True,
    }
    if "trust_level" in person_source_links.c and match.get("trust_level"):
        values["trust_level"] = match["trust_level"]
    if "confirmation_source" in person_source_links.c:
        values["confirmation_source"] = SOURCE_MACHINE
    if "evidence_method" in person_source_links.c:
        values["evidence_method"] = match["method"]

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
    roster = Roster(load_people(connection))

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

        match = match_contact(contact, roster)

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
