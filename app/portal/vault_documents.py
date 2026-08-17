"""Portal ↔ Vault document bridge.

The Vault (``app.services.vault``) is staff-RBAC only. This module is the *client-facing* view
of the Vault: a portal client sees ONLY vault documents that (a) are linked to a person the
portal account may reach (``portal_scope``) AND (b) are explicitly ``client_visible``. Clients can
download *approved* documents (or their own pending upload) and upload documents, which land as
**pending** vault documents (``status='uploaded'``, ``uploaded_by_portal_account_id`` set) that an
employee must approve before they become official. Every action writes an audit event.

Staff transitions (approve, toggle visibility) go through the Vault service (``vault.manage`` +
category), so the existing RBAC governs who can make a client upload official — Lauren/Michael
(full) can; department roles cannot.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.db import (
    engine,
    people,
    portal_accounts,
    portal_document_requests,
    vault_document_links,
    vault_document_versions,
    vault_documents,
)
from app.portal.service import portal_scope
from app.security.audit import write_audit_event
from app.services.vault import service as vault
from app.services.vault import storage

# Vault status -> client-facing label (task vocabulary: Requested/Received/Under Review/Approved/
# Rejected/Archived). "Requested" is modeled by portal_document_requests, not a vault status.
_CLIENT_STATUS = {
    "uploaded": "Received", "under_review": "Under Review", "approved": "Approved",
    "rejected": "Rejected", "signed": "Approved", "filed": "Approved", "archived": "Archived",
}
_DOWNLOADABLE = {"approved", "signed", "filed"}     # "Download approved documents"


def _client_view(row) -> dict:
    pending = row["uploaded_by_portal_account_id"] is not None and row["status"] in {"uploaded", "under_review"}
    return {
        "id": row["id"], "display_name": row["display_name"], "category": row["category"],
        "document_type": row["document_type"], "status": row["status"],
        "client_status": _CLIENT_STATUS.get(row["status"], row["status"]),
        "pending_approval": pending, "file_size": row["file_size"], "version": row["current_version"],
        "uploaded_by_client": row["uploaded_by_portal_account_id"] is not None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "downloadable": row["client_visible"] and (row["status"] in _DOWNLOADABLE
                                                    or row["uploaded_by_portal_account_id"] is not None),
    }


def _portal_audit(*, action, document_id, account_id, request_id="portal", ip_address=None, metadata=None):
    write_audit_event(action=action, entity_type="vault_document", entity_id=document_id,
                      actor_user_id=None, request_id=request_id, ip_address=ip_address,
                      metadata={"portal_account_id": account_id, **(metadata or {})})


# --- client reads ------------------------------------------------------------

def portal_documents(principal, scope=None) -> list[dict]:
    """Client-visible vault documents linked to the portal account's reachable persons."""
    scope = scope or portal_scope(principal.account_id, permission="documents")
    person_ids = scope["person_ids"]
    if not person_ids:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(vault_documents)
            .select_from(vault_documents.join(
                vault_document_links, vault_document_links.c.document_id == vault_documents.c.id))
            .where(vault_document_links.c.person_id.in_(person_ids),
                   vault_documents.c.client_visible.is_(True))
            .distinct().order_by(vault_documents.c.created_at.desc())).mappings().all()
    return [_client_view(r) for r in rows]


def _reachable_document(conn, principal, document_id, scope):
    """Load a document only if it is client-visible AND linked to a reachable person."""
    doc = conn.execute(
        select(vault_documents).where(vault_documents.c.id == document_id)).mappings().first()
    if doc is None or not doc["client_visible"]:
        return None
    linked_people = {r[0] for r in conn.execute(
        select(vault_document_links.c.person_id).where(
            vault_document_links.c.document_id == document_id)).all()}
    if not (linked_people & scope["person_ids"]):
        return None
    return doc


def download_document(principal, document_id, *, request_id="portal", ip_address=None):
    """Authorize + return (path, filename, mime) for an approved client-visible doc (or the client's
    own pending upload). Raises PermissionError otherwise. Audits the download."""
    scope = portal_scope(principal.account_id, permission="documents")
    with engine.connect() as conn:
        doc = _reachable_document(conn, principal, document_id, scope)
    if doc is None:
        raise PermissionError("Document is not available to this portal account.")
    own_upload = doc["uploaded_by_portal_account_id"] == principal.account_id
    if doc["status"] not in _DOWNLOADABLE and not own_upload:
        raise PermissionError("Document has not been approved for download.")
    path = storage.resolve_path(doc["storage_key"])
    _portal_audit(action="portal.document.downloaded", document_id=document_id,
                  account_id=principal.account_id, request_id=request_id, ip_address=ip_address)
    return path, doc["original_filename"], doc["mime_type"]


# --- client upload (pending employee approval) -------------------------------

def upload_document(principal, *, source, original_filename, display_name, category="general",
                    document_type=None, request_id=None, http_request_id="portal", ip_address=None):
    """Client uploads a document. It is stored as a PENDING vault document (status='uploaded',
    client_visible, uploaded_by_portal_account_id set) linked to the account's person — it becomes
    official only when an employee approves it. If ``request_id`` is given, the matching portal
    document request is marked fulfilled."""
    if category not in vault.CATEGORIES:
        category = "general"
    person_id = principal.person_id
    scope = portal_scope(principal.account_id, permission="documents")
    if person_id not in scope["person_ids"]:
        raise PermissionError("Portal account cannot upload for this person.")

    # If fulfilling a document request, that request must belong to a person the account can reach
    # (documents scope). Without this a client could pass a forged request_id and flip ANOTHER
    # client's request to "uploaded" (cross-client IDOR). Checked before any bytes are stored.
    if request_id is not None:
        with engine.connect() as conn:
            req_person = conn.scalar(select(portal_document_requests.c.person_id).where(
                portal_document_requests.c.id == request_id))
        if req_person is None or req_person not in scope["person_ids"]:
            raise PermissionError("Document request is outside portal access scope.")

    stored = storage.save_stream(source, original_filename=original_filename)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        doc_id = conn.execute(vault_documents.insert().values(
            display_name=display_name, original_filename=original_filename, document_type=document_type,
            category=category, security_classification="client_upload", status="uploaded",
            mime_type=None, file_size=stored["file_size"], storage_key=stored["storage_key"],
            checksum_sha256=stored["checksum_sha256"], current_version=1,
            uploaded_by_user_id=None, uploaded_by_portal_account_id=principal.account_id,
            client_visible=True, created_at=now, updated_at=now,
        ).returning(vault_documents.c.id)).scalar_one()
        conn.execute(vault_document_versions.insert().values(
            document_id=doc_id, version_number=1, storage_key=stored["storage_key"],
            checksum_sha256=stored["checksum_sha256"], file_size=stored["file_size"], created_at=now))
        conn.execute(vault_document_links.insert().values(document_id=doc_id, person_id=person_id))
        if request_id is not None:
            conn.execute(portal_document_requests.update().where(
                portal_document_requests.c.id == request_id).values(status="uploaded"))
    _portal_audit(action="portal.document.uploaded", document_id=doc_id,
                  account_id=principal.account_id, request_id=http_request_id, ip_address=ip_address,
                  metadata={"category": category, "request_id": request_id})
    return doc_id


def pending_uploads_for_people(person_ids) -> list[dict]:
    """Staff view — client uploads awaiting approval for a set of persons (Vault tab review queue)."""
    if not person_ids:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(vault_documents)
            .select_from(vault_documents.join(
                vault_document_links, vault_document_links.c.document_id == vault_documents.c.id))
            .where(vault_document_links.c.person_id.in_(list(person_ids)),
                   vault_documents.c.uploaded_by_portal_account_id.isnot(None),
                   vault_documents.c.status.in_(["uploaded", "under_review"]))
            .distinct().order_by(vault_documents.c.created_at.desc())).mappings().all()
    return [dict(r) for r in rows]


# --- staff transitions (reuse Vault RBAC: vault.manage + category) -----------

def set_client_visible(staff_principal, document_id, visible, *, actor_user_id=None, ip_address=None):
    """Employee toggles whether a vault document is visible to the client. Reuses vault.update_metadata
    (vault.manage + category + record scope)."""
    return vault.update_metadata(staff_principal, document_id, changes={"client_visible": bool(visible)},
                                 actor_user_id=actor_user_id, ip_address=ip_address)


def approve_upload(staff_principal, document_id, *, approved=True, actor_user_id=None, ip_address=None):
    """Employee approves (or rejects) a client-uploaded document, making it official (or not)."""
    new_status = "approved" if approved else "rejected"
    return vault.update_metadata(staff_principal, document_id, changes={"status": new_status},
                                 actor_user_id=actor_user_id, ip_address=ip_address)


def person_display(person_id):
    with engine.connect() as conn:
        return conn.scalar(select(people.c.full_name).where(people.c.id == person_id))


def account_person_id(account_id):
    with engine.connect() as conn:
        return conn.scalar(select(portal_accounts.c.person_id).where(portal_accounts.c.id == account_id))
