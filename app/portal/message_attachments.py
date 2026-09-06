"""Secure-message attachments — the ONE write path, and the invariant that keeps clients safe.

Two document stores exist. Canonical ``documents`` is internal work product: it has no
client-publication flag and no client-facing serving path, and the one that existed was removed as a
vulnerability (``de26702`` — "there is NO fallback to the canonical table"). ``vault_documents`` is
the published-artifact store: ``client_visible``, an approval workflow, client-safe delivery
filenames, and an audited client download. Migration ``msgatt01`` lets an attachment reference either.

THE INVARIANT, and why it lives here rather than in the database:

    A CLIENT-VISIBLE portal message may carry ONLY vault-backed attachments.
    An INTERNAL note may carry either.

The discriminator (``portal_messages.visibility``) lives on the PARENT row, and a PostgreSQL CHECK
cannot inspect another table. The honest options were a cross-table trigger — a pattern this
repository does not use — or a denormalized copy of ``visibility`` onto every attachment row, which
would create a second source of truth that can drift. Neither is worth it when a single service
function can be the only way an attachment is ever written. So this module is that function, and the
test suite is the proof: ``tests/test_message_attachments.py`` pins the refusal from every caller.

What the database DOES guarantee (msgatt01):
  * ``portal_message_attachments``  — EXACTLY ONE reference. Both FKs CASCADE, so a deleted document
    deletes the attachment and a row can never sit with no live reference.
  * ``communication_attachments``   — AT MOST ONE. Both FKs SET NULL, because that table deliberately
    keeps a tombstone row when the document is deleted, preserving communication history.

Nothing here stores bytes, renames a file, or creates a document. Uploads go through the existing
vault path (``portal.vault_documents.upload_document``); this only records the link.
"""
from __future__ import annotations

CLIENT_VISIBLE = "client"
INTERNAL = "internal"

#: Staff-facing wording. Never names a document id — a refusal must not confirm one exists.
_CANONICAL_ON_CLIENT_MESSAGE = (
    "A message the client can read may only carry documents published to the Client Vault. "
    "Canonical documents are internal and are never delivered to a client.")


class MessageAttachmentError(ValueError):
    """An attachment was refused. The message is safe to show a staff user verbatim."""


def assert_canonical_allowed(visibility: str) -> None:
    """Refuse a canonical ``documents`` reference on a message the client can read.

    This is the invariant. It is checked before anything is written, so a refused attachment leaves
    no row behind and the surrounding message transaction rolls back with it.
    """
    if visibility == CLIENT_VISIBLE:
        raise MessageAttachmentError(_CANONICAL_ON_CLIENT_MESSAGE)


def attach(conn, *, message_id: int, visibility: str,
           document_ids=(), vault_document_ids=()) -> int:
    """Record attachment rows for one message. Returns how many were written.

    ``conn`` is the caller's OPEN transaction — the same one that inserted the message — so a failure
    anywhere rolls the message and its attachments back together and no orphan row survives. Callers
    must not open their own transaction here.
    """
    from app.db import portal_message_attachments

    document_ids = tuple(dict.fromkeys(document_ids or ()))
    vault_document_ids = tuple(dict.fromkeys(vault_document_ids or ()))
    if document_ids:
        assert_canonical_allowed(visibility)

    written = 0
    for document_id in document_ids:
        conn.execute(portal_message_attachments.insert().values(
            message_id=message_id, document_id=document_id, vault_document_id=None))
        written += 1
    for vault_document_id in vault_document_ids:
        conn.execute(portal_message_attachments.insert().values(
            message_id=message_id, document_id=None, vault_document_id=vault_document_id))
        written += 1
    return written


# --- read model ---------------------------------------------------------------------------------

#: Each audience downloads through the route that authorizes IT — never a shared generic path.
#: staff  -> the vault route (vault.download + category + record scope + audit)
#: client -> the portal route (documents grant + client_visible + link scope + audit)
STAFF, CLIENT = "staff", "client"
_DOWNLOAD_BASE = {
    STAFF: "/api/vault/documents/{id}/download",
    CLIENT: "/api/v1/portal/documents/{id}/download",
}


def _vault_view(row, audience) -> dict:
    """Presentation fields only. No storage_key, checksum, provider, status or internal metadata."""
    from app.services.vault.naming import safe_vault_label
    return {
        "kind": "vault",
        "vault_document_id": row["id"],
        "document_id": None,
        "label": safe_vault_label(row),
        "content_type": row["mime_type"],
        "download_href": _DOWNLOAD_BASE[audience].format(id=row["id"]),
    }


def _canonical_view(row) -> dict:
    from app.services.document_naming import document_delivery_filename
    return {
        "kind": "document",
        "vault_document_id": None,
        "document_id": row["id"],
        "label": document_delivery_filename(row),
        "content_type": row["content_type"],
        "download_href": f"/documents/{row['id']}/download",
    }


def attachments_for_messages(message_ids, *, audience: str = STAFF) -> dict[int, list[dict]]:
    """``{message_id: [safe attachment view, …]}`` for a set of messages.

    A READ of link rows plus the minimum metadata needed to render them. It performs no
    authorization of its own: the caller has already proved the principal may read these messages,
    and each ``download_href`` points at a route that authorizes the document on its own terms —
    knowing the id here grants nothing.

    ``audience`` only chooses which of those two routes is linked. A CLIENT never receives a
    canonical-document row: the invariant keeps canonical attachments off client-visible messages,
    and one is skipped here as well rather than linked to a route a client cannot use.
    """
    from sqlalchemy import select

    from app.db import documents, engine, portal_message_attachments, vault_documents

    ids = tuple(dict.fromkeys(int(i) for i in message_ids or ()))
    if not ids:
        return {}
    out: dict[int, list[dict]] = {}
    with engine.connect() as c:
        rows = c.execute(select(portal_message_attachments).where(
            portal_message_attachments.c.message_id.in_(ids)).order_by(
            portal_message_attachments.c.id)).mappings().all()
        if not rows:
            return {}
        vault_ids = {r["vault_document_id"] for r in rows if r["vault_document_id"]}
        doc_ids = {r["document_id"] for r in rows if r["document_id"]}
        vault_by_id = {}
        if vault_ids:
            vault_by_id = {v["id"]: v for v in c.execute(select(vault_documents).where(
                vault_documents.c.id.in_(tuple(vault_ids)))).mappings()}
        docs_by_id = {}
        if doc_ids:
            docs_by_id = {d["id"]: d for d in c.execute(select(documents).where(
                documents.c.id.in_(tuple(doc_ids)))).mappings()}
    for r in rows:
        if r["vault_document_id"] and r["vault_document_id"] in vault_by_id:
            view = _vault_view(vault_by_id[r["vault_document_id"]], audience)
        elif r["document_id"] and r["document_id"] in docs_by_id and audience == STAFF:
            view = _canonical_view(docs_by_id[r["document_id"]])
        else:
            continue                      # gone, or internal-only for this audience — render nothing
        out.setdefault(r["message_id"], []).append(view)
    return out
