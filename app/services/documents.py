import hashlib
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import and_, insert, or_, select, update

from app.db import documents, engine, people

# Reuse the SINGLE vault validation implementation (no second security implementation) so an
# untrusted client upload landing in the documents table gets the same controls as the vault path.
from app.services.vault.storage import (
    MAX_UPLOAD_BYTES,
    VaultStorageError,
    content_matches_extension,
    validate_extension,
)

DOCUMENT_ROOT = Path("documents")


#: The owner column a workspace upload anchors to. Exactly one is ever set.
OWNER_COLUMNS = {"person": "person_id", "household": "household_id", "organization": "organization_id"}


def _person_directory(person_id: int) -> Path:
    directory = DOCUMENT_ROOT / str(person_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _owner_directory(owner_type: str, owner_id: int) -> Path:
    """Storage directory for an owner. Person keeps its historical ``documents/<person_id>`` layout
    untouched; household and organization uploads get their own subtree. The path is derived from the
    owner id alone — never from anything in the request."""
    if owner_type == "person":
        return _person_directory(owner_id)
    directory = DOCUMENT_ROOT / owner_type / str(owner_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _assert_owner_exists(owner_type: str, owner_id: int) -> None:
    from app.db import households, relationship_entities
    table, column = {"person": (people, people.c.id),
                     "household": (households, households.c.id),
                     "organization": (relationship_entities,
                                      relationship_entities.c.id)}[owner_type]
    with engine.connect() as connection:
        if connection.scalar(select(column).where(column == owner_id)) is None:
            raise DocumentOwnerNotFound(f"{owner_type} {owner_id} does not exist")


class DocumentOwnerNotFound(LookupError):
    """The person/household/organization a document was being uploaded to does not exist."""


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()

    if len(suffix) > 20:
        return ""

    return suffix


def save_person_document(
    person_id: int,
    original_name: str,
    source: BinaryIO,
    content_type: str | None = None,
    category: str | None = None,
    description: str | None = None,
    uploaded_by: str | None = None,
    verify_content: bool = False,
) -> int:
    """Store an uploaded stream as a PERSON document. Unchanged signature and behaviour; the storage
    core now lives in :func:`save_workspace_document`, which this delegates to so there is exactly
    one uploader rather than one per owner kind."""
    return save_workspace_document(
        owner_type="person", owner_id=person_id, original_name=original_name, source=source,
        content_type=content_type, category=category, description=description,
        uploaded_by=uploaded_by, verify_content=verify_content, validate_owner=False)


def save_workspace_document(
    *,
    owner_type: str,
    owner_id: int,
    original_name: str,
    source: BinaryIO,
    content_type: str | None = None,
    category: str | None = None,
    description: str | None = None,
    uploaded_by: str | None = None,
    verify_content: bool = False,
    validate_owner: bool = True,
) -> int:
    """Store an uploaded stream as a canonical document owned by ONE person, household or
    organization.

    The single canonical uploader — streaming write, SHA-256, and (with ``verify_content``) the same
    extension allow-list, size cap and leading-byte content check the vault client-upload path uses,
    via the one vault validation implementation. Exactly one owner column is ever populated; the
    other two stay NULL, so an upload never implies a second owner or touches the relationship graph.

    Filename safety: the stored name is a random hex plus a short sanitised suffix, and the directory
    is derived from the owner id, so nothing in the request can influence where bytes land or escape
    the document root. ``original_name`` is preserved verbatim as provenance.
    """
    if owner_type not in OWNER_COLUMNS:
        raise ValueError(f"unknown owner_type {owner_type!r}")
    if validate_owner:
        _assert_owner_exists(owner_type, owner_id)
    ext = validate_extension(original_name) if verify_content else None
    stored_name = f"{uuid.uuid4().hex}{_safe_suffix(original_name)}"
    destination = _owner_directory(owner_type, owner_id) / stored_name

    digest = hashlib.sha256()
    size_bytes = 0
    first_chunk = True

    try:
        with destination.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                if verify_content and first_chunk:
                    first_chunk = False
                    if not content_matches_extension(ext, chunk):
                        raise VaultStorageError(
                            f"File contents do not match a '.{ext}' file. Please upload a genuine "
                            f"{ext.upper()} file.")
                size_bytes += len(chunk)
                if verify_content and size_bytes > MAX_UPLOAD_BYTES:
                    raise VaultStorageError(
                        f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
                digest.update(chunk)
                output.write(chunk)
        if verify_content and size_bytes == 0:
            raise VaultStorageError("Empty file.")

        with engine.begin() as connection:
            document_id = connection.execute(
                insert(documents)
                .values(
                    **{OWNER_COLUMNS[owner_type]: owner_id},
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
        # Include the person's own documents AND documents linked to their household, so household
        # documents (e.g. joint tax returns, estate documents) are visible to every household member.
        household_id = connection.execute(
            select(people.c.household_id).where(people.c.id == person_id)
        ).scalar_one_or_none()
        scope = documents.c.person_id == person_id
        if household_id is not None:
            scope = or_(scope, documents.c.household_id == household_id)
        rows = connection.execute(
            select(documents)
            .where(
                and_(scope, documents.c.archived.is_(False)),
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
