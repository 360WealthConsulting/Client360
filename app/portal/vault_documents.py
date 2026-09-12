"""Portal ↔ Vault document bridge — the client-facing view of everything published to a client.

TWO BACKING STORES, ONE LIST. A client's Vault page now draws from both:

``vault``        vault documents linked to a reachable person and marked ``client_visible``. This is
                 the original store and its rules are unchanged — client uploads still land here as
                 pending rows an employee must approve.
``publication``  CANONICAL ``documents`` rows published to a reachable audience through
                 ``app.services.publication``. No bytes were copied to get them here: a publication
                 is a reference plus a decision, so the file keeps its OCR, classification and
                 version history in the one canonical row.

The second store is what makes an ingested Drake or TaxDome document reachable by its own client at
all. Before it, the portal read ``vault_documents`` exclusively while every importer wrote to
``documents``, and the two shared no rows — so a correctly owned, correctly filed client document
was structurally invisible to the client it belonged to.

AUDIENCE, NOT OWNER. Publication access is resolved from the publication's audience against the
portal grant's scope — person publications against the account's reachable persons, household
publications against the account's granted households. It is never resolved from
``documents.person_id``: the canonical resolver deduplicates by content hash and fills only NULL
ownership, so one row can be the same file for two unrelated clients while carrying one owner.

Every row carries a ``source`` discriminator and its own ``download_url``, because the two stores
authorize downloads differently and a shared generic path would have to pick one of the two rules.

Staff transitions (approve, toggle vault visibility) go through the Vault service (``vault.manage`` +
category); publication decisions go through the publication service (``vault.manage`` + record
scope). Both write audit events.
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
from app.portal.gate import gate
from app.portal.service import portal_scope
from app.security.audit import write_audit_event
from app.services.publication import service as publication
from app.services.vault import service as vault
from app.services.vault import storage
from app.services.vault.naming import (
    safe_vault_delivery_filename,
    safe_vault_label,
)

#: Which store a client-facing row came from. The portal renders one list; the two stores keep
#: separate download routes because they have separate authorization rules.
SOURCE_VAULT = "vault"
SOURCE_PUBLICATION = "publication"


def store_of(row) -> str:
    """Which store a client-facing row came from, derived from the route that authorizes it.

    Deliberately NOT a field on the row. The client payload must disclose nothing about internal
    provenance — ``source`` is on the portal disclosure contract's forbidden-field list
    (tests/test_portal_task_tax_visibility.py) — and a client has no use for the answer anyway:
    ``download_url`` already routes them correctly. Staff-side callers and tests that genuinely need
    the distinction derive it here rather than reading it off a leaked key.
    """
    return SOURCE_PUBLICATION if "/publications/" in row["download_url"] else SOURCE_VAULT

# Vault status -> client-facing label (task vocabulary: Requested/Received/Under Review/Approved/
# Rejected/Archived). "Requested" is modeled by portal_document_requests, not a vault status.
_CLIENT_STATUS = {
    "uploaded": "Received", "under_review": "Under Review", "approved": "Approved",
    "rejected": "Rejected", "signed": "Approved", "filed": "Approved", "archived": "Archived",
}
_DOWNLOADABLE = {"approved", "signed", "filed"}     # "Download approved documents"


def _client_view(row, *, downloads_enabled=True) -> dict:
    pending = row["uploaded_by_portal_account_id"] is not None and row["status"] in {"uploaded", "under_review"}
    return {
        "id": row["id"], "display_name": safe_vault_label(row), "category": row["category"],
        "document_type": row["document_type"], "status": row["status"],
        "client_status": _CLIENT_STATUS.get(row["status"], row["status"]),
        "pending_approval": pending, "file_size": row["file_size"], "version": row["current_version"],
        "uploaded_by_client": row["uploaded_by_portal_account_id"] is not None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        # The firm-wide download gate is part of "downloadable" so the UI cannot render a Download
        # control that download_document() would then refuse with a 403.
        "downloadable": downloads_enabled and row["client_visible"] and (
            row["status"] in _DOWNLOADABLE or row["uploaded_by_portal_account_id"] is not None),
        # The route that authorizes THIS row. The template used to hard-code
        # "/api/portal/documents/{id}/download" — a registered route, and the right one while the
        # vault was the only store. It is the wrong one for a publication, and a template cannot
        # tell the two apart. Naming the route here, where the store is known, is what keeps the
        # rendered link and the authorizing route from diverging.
        #
        # Two equivalent vault download paths exist: this v1 one (app/routes/portal.py) and the
        # older /api/portal one (app/routes/portal_api.py). Both delegate to download_document, so
        # this is not a behaviour change; v1 is chosen because it carries the fuller denial
        # contract, and the duplication is left for a separate consolidation.
        "download_url": f"/api/v1/portal/documents/{row['id']}/download",
    }


def _publication_client_view(row, *, downloads_enabled=True) -> dict:
    """One published canonical document, in the same shape the template already renders.

    A published canonical document has no vault workflow status — it was not uploaded for approval,
    it was deliberately released by a member of staff — so its client status is simply Shared. It is
    downloadable whenever the firm-wide gate allows: the publication IS the approval, and
    ``client_publications`` has already excluded every revoked, archived, withdrawn and deleted case.
    """
    name = row["display_name"] or row["original_name"]
    return {
        "id": row["publication_id"], "display_name": name,
        "category": row["category"] or "general",
        "document_type": row["document_type"], "status": "published",
        "client_status": "Shared", "pending_approval": False,
        "file_size": row["size_bytes"], "version": None,
        "uploaded_by_client": False,
        "created_at": row["published_at"].isoformat() if row["published_at"] else None,
        "tax_year": row["tax_year"],
        "downloadable": downloads_enabled,
        "download_url": f"/api/v1/portal/publications/{row['publication_id']}/download",
    }


def _portal_audit(*, action, document_id, account_id, request_id="portal", ip_address=None,
                  metadata=None, entity_type="vault_document"):
    write_audit_event(action=action, entity_type=entity_type, entity_id=document_id,
                      actor_user_id=None, request_id=request_id, ip_address=ip_address,
                      metadata={"portal_account_id": account_id, **(metadata or {})})


# --- client reads ------------------------------------------------------------

def portal_documents(principal, scope=None) -> list[dict]:
    """Everything shared with this client: vault documents plus published canonical documents.

    Both stores are filtered by the SAME grant scope. A vault document is reachable through its
    person link; a publication is reachable through its audience — person publications against the
    account's reachable persons, household publications against the households the grant names.

    An account with neither reachable persons nor granted households gets an empty list rather than
    an unfiltered query, because an empty IN-list must never widen to "all".
    """
    scope = scope or portal_scope(principal.account_id, permission="documents")
    person_ids = scope["person_ids"]
    household_ids = scope.get("household_ids") or set()
    organization_ids = scope.get("organization_ids") or set()
    if not person_ids and not household_ids and not organization_ids:
        return []

    downloads_enabled = gate("portal.documents.download_enabled")   # evaluated once, not per row

    rows: list[dict] = []
    if person_ids:
        with engine.connect() as conn:
            vault_rows = conn.execute(
                select(vault_documents)
                .select_from(vault_documents.join(
                    vault_document_links,
                    vault_document_links.c.document_id == vault_documents.c.id))
                .where(vault_document_links.c.person_id.in_(person_ids),
                       vault_documents.c.client_visible.is_(True))
                .distinct().order_by(vault_documents.c.created_at.desc())).mappings().all()
        rows.extend(_client_view(r, downloads_enabled=downloads_enabled) for r in vault_rows)

    # Publications carry their own exclusions (revoked / archived / withdrawn / document deleted or
    # archived) inside client_publications, so there is nothing to re-apply here.
    published = publication.client_publications(
        person_ids=person_ids, household_ids=household_ids, organization_ids=organization_ids)
    rows.extend(_publication_client_view(r, downloads_enabled=downloads_enabled) for r in published)

    rows.sort(key=lambda r: (r["created_at"] or ""), reverse=True)
    return rows


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
    own pending upload). Raises PermissionError otherwise. Audits the download.

    ``filename`` is the SAFE delivery name (see :mod:`app.services.vault.naming`), never the raw
    ``original_filename`` — it becomes the response's Content-Disposition and so the name the
    browser saves. The bytes served and the stored row are unchanged.

    The firm-wide ``portal.documents.download_enabled`` gate is enforced here as well as at the request
    layer (``portal_gate``), so a non-HTTP caller cannot bypass the kill switch. It is checked BEFORE the
    document is resolved, so a disabled gate leaks neither existence nor storage key."""
    if not gate("portal.documents.download_enabled"):
        raise PermissionError("Document download is not available.")
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
    return path, safe_vault_delivery_filename(doc), doc["mime_type"]


def download_publication(principal, publication_id, *, request_id="portal", ip_address=None):
    """Authorize + return (path, filename, mime) for a PUBLISHED canonical document.

    Keyed on the publication id, not the document id. The publication is the grant, so the id the
    client holds names something decided for them specifically; a document id would name a shared
    object and invite enumeration against it. Either way authorization is re-derived here and the
    client's claim is never trusted.

    Every refusal — unknown id, revoked, archived, visibility withdrawn, out of audience, canonical
    document deleted or archived — raises the SAME PermissionError with the same message, so a client
    cannot tell "does not exist" from "exists but is not yours". The route maps all of them to one
    generic 404, matching the vault download's denial contract.

    The firm-wide download gate is checked FIRST, before the publication is resolved, so a disabled
    gate leaks neither existence nor storage path.
    """
    if not gate("portal.documents.download_enabled"):
        raise PermissionError("Document download is not available.")
    scope = portal_scope(principal.account_id, permission="documents")
    row = publication.authorized_publication(
        publication_id,
        person_ids=scope["person_ids"],
        household_ids=scope.get("household_ids") or set(),
        organization_ids=scope.get("organization_ids") or set())
    if row is None:
        raise PermissionError("Document is not available to this portal account.")

    # Same resolution rule the staff canonical download uses: an absolute storage_uri wins, and a
    # legacy repo-relative storage_path is the fallback. No second copy of the bytes exists to
    # resolve differently.
    from pathlib import Path
    uri = row["storage_uri"]
    path = Path(uri) if uri and Path(uri).is_absolute() else Path(row["storage_path"])

    from app.services.document_naming import document_delivery_filename
    filename = document_delivery_filename(row)
    _portal_audit(action="portal.publication.downloaded", document_id=publication_id,
                  account_id=principal.account_id, request_id=request_id, ip_address=ip_address,
                  entity_type="document_publication",
                  metadata={"document_id": row["document_id"]})
    return path, filename, row["content_type"]


# --- client upload (pending employee approval) -------------------------------

def upload_document(principal, *, source, original_filename, display_name, category="general",
                    document_type=None, request_id=None, http_request_id="portal", ip_address=None):
    """Client uploads a document. It is stored as a PENDING vault document (status='uploaded',
    client_visible, uploaded_by_portal_account_id set) linked to the account's person — it becomes
    official only when an employee approves it. If ``request_id`` is given, the matching portal
    document request is marked fulfilled.

    The firm-wide ``portal.documents.upload_enabled`` gate is enforced here as well as at the request
    layer, before any scope resolution or byte is written, so a disabled gate creates no document row,
    no storage object and no partial upload state."""
    if not gate("portal.documents.upload_enabled"):
        raise PermissionError("Document upload is not available.")
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

    # verify_content: client uploads are untrusted, so reject files whose bytes don't match the
    # claimed extension (e.g. a renamed executable/HTML). Staff/import paths keep the default off.
    stored = storage.save_stream(source, original_filename=original_filename, verify_content=True)
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
