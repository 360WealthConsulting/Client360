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
deletion is not. Callers that also want to hide archived rows say so themselves.
"""
from __future__ import annotations

from sqlalchemy import and_

from app.db import documents

#: The sentinel ``documents.status`` value meaning soft-deleted.
DELETED_STATUS = "deleted"


def active_documents_clause():
    """SQL restriction to documents that have not been soft-deleted.

    AND this into any query over ``documents`` that feeds a normal user-facing surface. It carries
    no authority — it says nothing about whether the caller may SEE these rows, only that they
    still exist. Combine it with ``service.visible_documents_clause`` for the scope boundary.
    """
    return and_(documents.c.status.is_distinct_from(DELETED_STATUS),
                documents.c.deleted_at.is_(None))


def is_active(row) -> bool:
    """The row-level form of :func:`active_documents_clause`, for a document already loaded.

    Accepts anything with ``.get`` / ``__getitem__`` (a dict or a SQLAlchemy RowMapping). A row
    missing either key is treated as active, matching the SQL: absent is not deleted.
    """
    if row is None:
        return False
    get = row.get if hasattr(row, "get") else (lambda k, d=None: row[k] if k in row else d)
    return get("status", None) != DELETED_STATUS and get("deleted_at", None) is None
