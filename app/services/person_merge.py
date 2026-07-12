from typing import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database.schema import (
    engine,
    people,
    person_source_links,
    source_contacts,
)


def _first_value(records, field_name):
    for record in records:
        value = record.get(field_name)
        if value not in (None, ""):
            return value
    return None


def merge_source_contacts(record_ids: Iterable[int]) -> int:
    normalized_ids = sorted({int(record_id) for record_id in record_ids})

    if not normalized_ids:
        raise ValueError("At least one source contact ID is required.")

    with engine.begin() as conn:
        records = conn.execute(
            select(source_contacts)
            .where(source_contacts.c.id.in_(normalized_ids))
            .order_by(source_contacts.c.id)
        ).mappings().all()

        if len(records) != len(normalized_ids):
            found_ids = {record["id"] for record in records}
            missing_ids = [
                record_id
                for record_id in normalized_ids
                if record_id not in found_ids
            ]
            raise ValueError(
                f"Source contacts not found: {missing_ids}"
            )

        existing_person_ids = conn.execute(
            select(person_source_links.c.person_id)
            .where(
                person_source_links.c.source_contact_id.in_(
                    normalized_ids
                )
            )
            .distinct()
        ).scalars().all()

        if len(existing_person_ids) > 1:
            raise ValueError(
                "The selected source contacts are already linked "
                "to different canonical people."
            )

        if existing_person_ids:
            person_id = existing_person_ids[0]
        else:
            person_values = {
                "first_name": _first_value(records, "first_name"),
                "middle_name": _first_value(records, "middle_name"),
                "last_name": _first_value(records, "last_name"),
                "full_name": _first_value(records, "full_name"),
                "primary_email": _first_value(records, "email"),
                "normalized_email": _first_value(
                    records,
                    "normalized_email",
                ),
                "primary_phone": _first_value(records, "phone"),
                "normalized_phone": _first_value(
                    records,
                    "normalized_phone",
                ),
                "address_line_1": _first_value(
                    records,
                    "address_line_1",
                ),
                "address_line_2": _first_value(
                    records,
                    "address_line_2",
                ),
                "city": _first_value(records, "city"),
                "state": _first_value(records, "state"),
                "postal_code": _first_value(records, "postal_code"),
                "active": True,
            }

            person_id = conn.execute(
                people.insert()
                .values(**person_values)
                .returning(people.c.id)
            ).scalar_one()

        for record_id in normalized_ids:
            statement = (
                insert(person_source_links)
                .values(
                    person_id=person_id,
                    source_contact_id=record_id,
                    match_method="manual_review",
                    match_score=100,
                    confirmed=True,
                )
                .on_conflict_do_nothing(
                    constraint="uq_person_source_link"
                )
            )

            conn.execute(statement)

    return person_id
