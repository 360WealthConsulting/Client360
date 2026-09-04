import hashlib
import logging
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import and_, func, insert, or_, select, update

from app.db import documents, engine, people
from app.services.document_platform.lifecycle import (
    active_documents_clause,
    active_unarchived_clause,
)

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

    A HEIC/HEIF upload is stored EXACTLY as uploaded — same bytes, same ``original_name``, same
    ``content_type``, same SHA-256 — and is additionally queued for image normalization (see
    :func:`_queue_image_normalization`), which produces a separate JPEG derivative for OCR, previews
    and AI image inputs. The original is never replaced or re-encoded.
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

        _queue_image_normalization(document_id, original_name, content_type)
        return document_id

    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _queue_image_normalization(document_id: int, original_name: str, content_type: str | None) -> None:
    """Record a PENDING normalization for an upload that needs a JPEG derivative (HEIC/HEIF today).

    State only — no decode happens on the upload request, so an unusual image cannot slow or fail an
    upload that has already been safely stored. The derivative itself is produced on first use by
    ``document_derivatives.ensure_normalized_image``, which records the terminal state.

    Never raises: the ORIGINAL is already durably stored and its row committed by this point, and a
    provenance bookkeeping problem must not undo a successful upload."""
    try:
        from app.services.document_derivatives import mark_pending
        from app.services.image_normalization import needs_normalization
        if not needs_normalization(filename=original_name, content_type=content_type):
            return
        mark_pending(document_id, source_mime=content_type)
    except Exception:  # noqa: BLE001 — bookkeeping only; the stored original is unaffected
        logging.getLogger(__name__).warning(
            "Could not queue image normalization for document %s", document_id)


#: A document is VISIBLE to a client-facing surface unless it has been soft-deleted.
#:
#: The canonical delete semantics live in ``document_platform.service``: :func:`soft_delete` sets
#: ``status='deleted'`` AND stamps ``deleted_at``; :func:`restore` clears both. ``archived`` is a
#: SEPARATE, older lifecycle flag (a document can be archived without being deleted), so it stays an
#: independent filter rather than being folded into this one — a single-document read
#: (:func:`get_document`) must still deliver an archived document, which is why only the LIST
#: clause below suppresses them, through ``lifecycle.active_unarchived_clause``.
#:
#: Both columns are checked, not just ``status``. They are written together by the service, but a
#: document that carries either marker must never render on a client surface, so the stricter
#: predicate is the safe one: any row that looks deleted by either measure is suppressed.
def _not_deleted():
    """SQL predicate: the document has not been soft-deleted.

    Delegates to ``document_platform.lifecycle.active_documents_clause`` so this file cannot drift
    from the canonical rule. That clause spells the status half as ``IS DISTINCT FROM 'deleted'``,
    which additionally keeps rows whose ``status`` is NULL — ``status != 'deleted'`` evaluates to
    NULL for those and silently dropped them.
    """
    return active_documents_clause()


def person_documents_clause(connection, person_id: int):
    """The ONE definition of "the documents that belong to this person's client surface".

    A person's paperwork is anchored two ways: on the person, and on the household they belong
    to (the joint return, the family organiser). Both are theirs to see, so both are in scope.
    Archived AND soft-deleted rows are excluded — the same safety pair every client surface uses.

    This exists because the scope used to be spelled out separately in each caller, and they
    drifted: client_summary counted ONLY person-anchored rows and omitted the deleted check,
    so a client whose documents all hang off the household reported zero documents while the very
    same page listed them, and a soft-deleted document would have been counted. Anything that
    needs to know whether this client has documents — a list, a count, an alert — must ask here.
    """
    household_id = connection.execute(
        select(people.c.household_id).where(people.c.id == person_id)
    ).scalar_one_or_none()
    scope = documents.c.person_id == person_id
    if household_id is not None:
        scope = or_(scope, documents.c.household_id == household_id)
    return and_(scope, active_unarchived_clause())


def count_person_documents(person_id: int, connection=None) -> int:
    """How many documents this client actually has, on exactly the scope the list uses.

    Takes an optional open connection so a caller already inside one (client_summary) does
    not open a second.
    """
    def _run(conn):
        return conn.execute(
            select(func.count()).select_from(documents)
            .where(person_documents_clause(conn, person_id))
        ).scalar_one()
    if connection is not None:
        return _run(connection)
    with engine.connect() as conn:
        return _run(conn)


def get_person_documents(person_id: int):
    with engine.connect() as connection:
        rows = connection.execute(
            select(documents)
            .where(person_documents_clause(connection, person_id))
            .order_by(
                documents.c.created_at.desc(),
                documents.c.id.desc(),
            )
        ).mappings().all()

    # ``name`` is what the page SHOWS: the canonical display name when one is set and safe, else the
    # original filename (document_naming.document_display_name — the single naming layer, not a second
    # one). ``original_name`` stays on every row, so provenance is still available to the detail view.
    from app.services.document_naming import document_display_name
    from app.services.document_tax_year import infer_tax_year

    out = []
    for row in rows:
        record = dict(row)
        year = infer_tax_year(record)
        out.append({
            **record,
            "name": document_display_name(record) or row["original_name"],
            "path": row["storage_path"],
            "size": row["size_bytes"],
            # Derived for display only — never written. See document_tax_year.
            "tax_year": year.year if year.is_proposed else None,
            "tax_year_inferred": year.is_proposed,
        })
    return out


def get_document(document_id: int, *, include_deleted: bool = False):
    """One document row, or None.

    Soft-deleted documents are suppressed by DEFAULT so that every delivery entry point (download,
    spreadsheet preview, image preview) fails closed: a document the firm has deleted must not be
    retrievable from a client-facing URL just because the id is still guessable.

    ``include_deleted=True`` is the explicit opt-in for an admin/recovery surface that intends to
    show or restore deleted documents. It must never be set from a client-facing route.
    """
    condition = documents.c.id == document_id
    if not include_deleted:
        condition = and_(condition, _not_deleted())
    with engine.connect() as connection:
        return connection.execute(
            select(documents).where(condition)
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
