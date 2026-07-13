import hashlib
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

from sqlalchemy import insert, select, update

from app.db import documents, engine


DOCUMENT_ROOT = Path("documents")


def _person_directory(person_id: int) -> Path:
    directory = DOCUMENT_ROOT / str(person_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()

    if len(suffix) > 20:
        return ""

    return suffix


def save_person_document(
    person_id: int,
    original_name: str,
    source: BinaryIO,
    content_type: Optional[str] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    uploaded_by: Optional[str] = None,
) -> int:
    stored_name = f"{uuid.uuid4().hex}{_safe_suffix(original_name)}"
    destination = _person_directory(person_id) / stored_name

    digest = hashlib.sha256()
    size_bytes = 0

    try:
        with destination.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)

        with engine.begin() as connection:
            document_id = connection.execute(
                insert(documents)
                .values(
                    person_id=person_id,
                    original_name=original_name,
                    stored_name=stored_name,
                    storage_path=str(destination),
                    content_type=content_type,
                    size_bytes=size_bytes,
                    sha256=digest.hexdigest(),
                    category=category or None,
                    description=description or None,
                    uploaded_by=uploaded_by or None,
                )
                .returning(documents.c.id)
            ).scalar_one()

        return document_id

    except Exception:
        destination.unlink(missing_ok=True)
        raise


def get_person_documents(person_id: int):
    with engine.connect() as connection:
        rows = connection.execute(
            select(documents)
            .where(
                documents.c.person_id == person_id,
                documents.c.archived.is_(False),
            )
            .order_by(
                documents.c.created_at.desc(),
                documents.c.id.desc(),
            )
        ).mappings().all()

    return [
        {
            **dict(row),
            "name": row["original_name"],
            "path": row["storage_path"],
            "size": row["size_bytes"],
        }
        for row in rows
    ]


def get_document(document_id: int):
    with engine.connect() as connection:
        return connection.execute(
            select(documents).where(
                documents.c.id == document_id
            )
        ).mappings().one_or_none()


def archive_document(document_id: int, person_id: int) -> bool:
    with engine.begin() as connection:
        result = connection.execute(
            update(documents)
            .where(
                documents.c.id == document_id,
                documents.c.person_id == person_id,
            )
            .values(archived=True)
        )

    return result.rowcount > 0
