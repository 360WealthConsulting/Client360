"""The ONE definition of "this document still exists".

A soft delete stamps TWO columns — ``status = 'deleted'`` and ``deleted_at`` — and until this
module existed every consumer picked one of them by hand. They do not agree in practice: the
production census found 49 documents carrying ``deleted_at`` with a ``status`` that was never moved
to ``'deleted'``, because ``document_merge_recovery`` and ``document_merge_execute`` write the pair
in separate statements and an interrupted run leaves the row half-retired. A listing that filters on
``status`` alone therefore shows documents a user already deleted.

So "active" is defined here once, as the CONJUNCTION, and every read path imports it:

    active  <=>  status IS DISTINCT FROM 'deleted'  AND  deleted_at IS NULL

``IS DISTINCT FROM`` rather than ``!=`` because it is NULL-safe by construction. ``status`` carries
a NOT NULL constraint today, so this changes nothing on current data — it is chosen so that if the
constraint is ever relaxed, a NULL status reads as "not deleted" rather than silently hiding the row
(``NULL != 'deleted'`` is NULL, which SQL treats as false).

Archived is deliberately NOT part of this. Archiving is a filing state a document returns from;
deletion is not — the document library must still be able to list archived rows on request. Callers
that also want to hide archived rows say so by using :func:`active_unarchived_clause`, which is the
one definition of what a CLIENT-facing list shows.
"""
from __future__ import annotations

from sqlalchemy import and_

from app.db import documents

#: The sentinel ``documents.status`` value meaning soft-deleted.
DELETED_STATUS = "deleted"

#: The sentinel ``documents.status`` value meaning archived.
ARCHIVED_STATUS = "archived"


def active_documents_clause():
    """SQL restriction to documents that have not been soft-deleted.

    AND this into any query over ``documents`` that feeds a normal user-facing surface. It carries
    no authority — it says nothing about whether the caller may SEE these rows, only that they
    still exist. Combine it with ``service.visible_documents_clause`` for the scope boundary.
    """
    return and_(documents.c.status.is_distinct_from(DELETED_STATUS),
                documents.c.deleted_at.is_(None))


def active_unarchived_clause():
    """SQL restriction to the documents a CLIENT-facing list shows: not deleted, and not archived.

    Every read that answers "what documents does this client have" must use this rather than
    :func:`active_documents_clause` alone. Archiving exists precisely so a document stops appearing
    on the client's file, so a list that shows archived rows is showing paperwork the firm has
    already put away — and, worse, disagreeing with the count beside it, because
    ``services.documents.person_documents_clause`` has always excluded archived rows.

    Archiving is written TWO independent ways, exactly as soft-delete was before this module
    existed:

    * :func:`document_platform.service.archive` sets ``status = 'archived'`` and stamps
      ``archived_at``, and never touches the boolean;
    * the older :func:`services.documents.archive_document` sets ``archived = true``, and never
      touches ``status``.

    So both markers are checked, and the stricter reading wins: a row that looks archived by either
    measure is suppressed. Today only the boolean is populated, which makes the status half a no-op
    on current data — it is here so that the two representations cannot drift into a leak the way
    the two delete markers already did once.

    ``IS DISTINCT FROM`` for the status half, for the same NULL-safety reason as the delete half.

    This carries no authority: it says what still belongs on a client's list, never who may see it.
    The library browser (``service.list_documents``) deliberately does NOT use this — staff must be
    able to ask for archived documents by name.
    """
    return and_(active_documents_clause(),
                documents.c.archived.is_(False),
                documents.c.status.is_distinct_from(ARCHIVED_STATUS))


def is_active(row) -> bool:
    """The row-level form of :func:`active_documents_clause`, for a document already loaded.

    Accepts anything with ``.get`` / ``__getitem__`` (a dict or a SQLAlchemy RowMapping). A row
    missing either key is treated as active, matching the SQL: absent is not deleted.
    """
    if row is None:
        return False
    get = row.get if hasattr(row, "get") else (lambda k, d=None: row[k] if k in row else d)
    return get("status", None) != DELETED_STATUS and get("deleted_at", None) is None
