from sqlalchemy import select

from app.db import engine, microsoft_documents


def get_person_microsoft_documents(person_id: int, limit: int = 100):
    with engine.connect() as connection:
        return connection.execute(
            select(microsoft_documents)
            .where(
                microsoft_documents.c.person_id == person_id,
                microsoft_documents.c.status == "matched",
                microsoft_documents.c.deleted.is_(False),
            )
            .order_by(
                microsoft_documents.c.modified_at_microsoft.desc(),
                microsoft_documents.c.id.desc(),
            )
            .limit(limit)
        ).mappings().all()
