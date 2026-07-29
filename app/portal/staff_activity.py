"""Staff-side portal activity (record-scoped read over EXISTING portal/vault tables).

The portal's own ``service.client_*`` reads are single-account (client-facing). The Staff Home
dashboard needs the mirror image: recent client-portal activity across the staff member's book —
pending client uploads, open document requests, and recent secure messages — scoped to the persons
the principal may see (``accessible_person_ids``). This is a NEW read but introduces NO schema: it
only queries ``vault_documents``/``vault_document_links``, ``portal_document_requests``,
``portal_threads``/``portal_messages``. Internal-visibility messages are never surfaced to nobody —
only ``visibility='client'`` messages appear (staff see the same client-visible thread content).
"""
from __future__ import annotations

from sqlalchemy import desc, or_, select

from app.db import (
    engine,
    people,
    portal_document_requests,
    portal_messages,
    portal_threads,
    vault_document_links,
    vault_documents,
)
from app.security.authorization import accessible_person_ids


def _scope_person_ids(conn, principal):
    """The person ids the principal may see: None → firm-wide (record.read_all); a set otherwise.
    An empty set means no access → the caller returns empty activity."""
    return accessible_person_ids(conn, principal)


def _apply_person_scope(stmt, column, person_ids):
    if person_ids is None:
        return stmt                       # firm-wide (record.read_all)
    return stmt.where(column.in_(tuple(person_ids)))


def portal_activity_for_staff(principal, *, limit=8) -> dict:
    """Recent, record-scoped client-portal activity for a staff member. Returns
    ``{pending_uploads, open_requests, recent_messages}`` (each a bounded list of dicts)."""
    if not principal.can("client.read"):
        return {"pending_uploads": [], "open_requests": [], "recent_messages": [], "enabled": False}

    with engine.connect() as conn:
        person_ids = _scope_person_ids(conn, principal)
        if person_ids is not None and not person_ids:
            return {"pending_uploads": [], "open_requests": [], "recent_messages": [], "enabled": True}

        # Pending client uploads awaiting employee approval (client-uploaded, not yet approved).
        uploads_stmt = (
            select(vault_documents.c.id, vault_documents.c.display_name, vault_documents.c.category,
                   vault_documents.c.status, vault_documents.c.created_at,
                   vault_document_links.c.person_id)
            .select_from(vault_documents.join(
                vault_document_links, vault_document_links.c.document_id == vault_documents.c.id))
            .where(vault_documents.c.uploaded_by_portal_account_id.isnot(None),
                   vault_documents.c.status.in_(["uploaded", "under_review"]))
            .order_by(desc(vault_documents.c.created_at)).limit(limit))
        uploads_stmt = _apply_person_scope(uploads_stmt, vault_document_links.c.person_id, person_ids)
        pending_uploads = [dict(r) for r in conn.execute(uploads_stmt).mappings()]

        # Open document requests the client still owes.
        req_stmt = (
            select(portal_document_requests.c.id, portal_document_requests.c.title,
                   portal_document_requests.c.status, portal_document_requests.c.due_date,
                   portal_document_requests.c.person_id, portal_document_requests.c.created_at)
            .where(portal_document_requests.c.status.in_(["open", "uploaded"]))
            .order_by(portal_document_requests.c.due_date.asc().nullslast()).limit(limit))
        req_stmt = _apply_person_scope(req_stmt, portal_document_requests.c.person_id, person_ids)
        open_requests = [dict(r) for r in conn.execute(req_stmt).mappings()]

        # Recent client-visible messages across the book (client → staff).
        msg_stmt = (
            select(portal_messages.c.id, portal_messages.c.body, portal_messages.c.sent_at,
                   portal_threads.c.id.label("thread_id"), portal_threads.c.subject,
                   portal_threads.c.person_id)
            .select_from(portal_messages.join(
                portal_threads, portal_threads.c.id == portal_messages.c.thread_id))
            .where(portal_messages.c.visibility == "client",
                   or_(portal_messages.c.sender_portal_account_id.isnot(None)))
            .order_by(desc(portal_messages.c.sent_at)).limit(limit))
        msg_stmt = _apply_person_scope(msg_stmt, portal_threads.c.person_id, person_ids)
        recent_messages = [dict(r) for r in conn.execute(msg_stmt).mappings()]

        # Resolve client names for display (single bounded query — no N+1).
        pids = {r["person_id"] for r in (pending_uploads + open_requests + recent_messages) if r["person_id"]}
        names = {}
        if pids:
            names = {r["id"]: r["full_name"] for r in conn.execute(
                select(people.c.id, people.c.full_name).where(people.c.id.in_(tuple(pids)))).mappings()}
    for row in pending_uploads + open_requests + recent_messages:
        row["person_name"] = names.get(row["person_id"])
    return {"pending_uploads": pending_uploads, "open_requests": open_requests,
            "recent_messages": recent_messages, "enabled": True}
