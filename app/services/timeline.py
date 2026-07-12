from sqlalchemy import select

from app.database.schema import person_source_links, source_contacts

def get_person_timeline(connection, person_id: int):
    statement = (
        select(
            source_contacts.c.source_system,
            source_contacts.c.source_file,
            source_contacts.c.imported_at,
            person_source_links.c.match_method,
            person_source_links.c.created_at.label("linked_at"),
        )
        .select_from(
            person_source_links.join(
                source_contacts,
                source_contacts.c.id
                == person_source_links.c.source_contact_id,
            )
        )
        .where(person_source_links.c.person_id == person_id)
        .order_by(
            source_contacts.c.imported_at.desc(),
            person_source_links.c.created_at.desc(),
        )
    )

    rows = connection.execute(statement).mappings().all()

    events = []

    for row in rows:
        events.append(
            {
                "event_type": "source_linked",
                "occurred_at": row["linked_at"] or row["imported_at"],
                "title": f"Linked {row['source_system']} record",
                "details": (
                    f"Source file: {row['source_file']}. "
                    f"Match method: {row['match_method'] or 'unknown'}."
                ),
            }
        )

    return events
