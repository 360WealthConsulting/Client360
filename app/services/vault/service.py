"""Client Vault service — create / list / read / version / edit / archive, with authorization + audit.

Reuses the existing RBAC (``Principal.can`` over ``vault.*`` capabilities), record scope
(``record_in_scope`` / ``record.read_all``), and writes an immutable-per-row audit trail to
``vault_document_audit_events`` for every view, download, upload, version, edit, and archive.

Authorization has two layers:
  * category — the principal needs ``vault.access.all`` (or ``record.read_all``), or the
    matching ``vault.category.<category>`` capability;
  * record scope — the linked person/household must be in the principal's scope.
Both are enforced on every document access; a denied access still records an audit event.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select

from app.db import (
    engine,
    vault_document_audit_events,
    vault_document_links,
    vault_document_versions,
    vault_documents,
)
from app.security.authorization import record_in_scope
from app.services.vault import storage

CATEGORIES = ("tax", "wealth", "accounting", "payroll", "benefits", "insurance", "compliance", "general")
STATUSES = ("uploaded", "under_review", "approved", "rejected", "signed", "filed", "archived")


class VaultPermissionError(PermissionError):
    """Raised when the principal may not perform the requested vault action."""


class VaultNotFound(LookupError):
    """Raised when a vault document does not exist."""


# --- authorization -----------------------------------------------------------

def can_access_category(principal, category: str) -> bool:
    """True if the principal may access this category. ``vault.access.all`` is the cross-department
    override; ``record.read_all`` is a RECORD-scope override only and does NOT grant category access."""
    return principal.can("vault.access.all") or principal.can(f"vault.category.{category}")


def _links_rows(conn, document_id):
    return conn.execute(
        select(vault_document_links).where(vault_document_links.c.document_id == document_id)
    ).mappings().all()


def _in_record_scope(principal, links, *, write=False) -> bool:
    """A document is in scope if the principal has firm-wide read, there is no client anchor, or
    any linked person/household is in the principal's record scope."""
    if principal.can("record.read_all"):
        return True
    anchors = [(lk["person_id"], lk["household_id"]) for lk in links]
    if not any(pid or hid for pid, hid in anchors):
        return True                                       # unanchored doc — category gate governs
    for pid, hid in anchors:
        if pid and record_in_scope(principal, "person", pid, write=write):
            return True
        if hid and record_in_scope(principal, "household", hid, write=write):
            return True
    return False


def _authorize(principal, doc, links, *, write=False):
    if not can_access_category(principal, doc["category"]):
        raise VaultPermissionError(f"Not authorized for '{doc['category']}' documents.")
    if not _in_record_scope(principal, links, write=write):
        raise VaultPermissionError("Client record is out of your scope.")


# --- audit -------------------------------------------------------------------

def record_audit(conn, *, document_id, user_id, action, ip_address=None, metadata=None):
    conn.execute(vault_document_audit_events.insert().values(
        document_id=document_id, user_id=user_id, action=action,
        ip_address=ip_address, metadata_json=metadata or {}))


# --- reads -------------------------------------------------------------------

def get_document(principal, document_id, *, actor_user_id=None, ip_address=None, audit_action="view"):
    """Full document view (metadata + current version + version history + links + audit). Authorizes
    and, when ``audit_action`` is set, records an audit event (e.g. 'view')."""
    with engine.begin() as conn:
        doc = conn.execute(
            select(vault_documents).where(vault_documents.c.id == document_id)).mappings().first()
        if doc is None:
            raise VaultNotFound(f"Vault document {document_id} not found.")
        links = _links_rows(conn, document_id)
        _authorize(principal, doc, links)
        versions = conn.execute(
            select(vault_document_versions)
            .where(vault_document_versions.c.document_id == document_id)
            .order_by(desc(vault_document_versions.c.version_number))).mappings().all()
        audit = conn.execute(
            select(vault_document_audit_events)
            .where(vault_document_audit_events.c.document_id == document_id)
            .order_by(desc(vault_document_audit_events.c.occurred_at))).mappings().all()
        if audit_action:
            record_audit(conn, document_id=document_id, user_id=actor_user_id,
                         action=audit_action, ip_address=ip_address)
        return {"document": dict(doc), "versions": [dict(v) for v in versions],
                "links": [dict(lk) for lk in links], "audit": [dict(a) for a in audit]}


def list_documents(principal, *, person_id=None, household_id=None, category=None, document_type=None,
                   status=None, year=None, query=None, include_archived=True, limit=200):
    """List documents linked to a client (person or household), filtered, and reduced to the
    categories + records the principal may access. This is the 'only linked documents' list."""
    with engine.begin() as conn:
        stmt = select(vault_documents).select_from(
            vault_documents.join(vault_document_links,
                                 vault_document_links.c.document_id == vault_documents.c.id))
        if person_id is not None:
            stmt = stmt.where(vault_document_links.c.person_id == person_id)
        if household_id is not None:
            stmt = stmt.where(vault_document_links.c.household_id == household_id)
        if category:
            stmt = stmt.where(vault_documents.c.category == category)
        if document_type:
            stmt = stmt.where(vault_documents.c.document_type == document_type)
        if status:
            stmt = stmt.where(vault_documents.c.status == status)
        if not include_archived:
            stmt = stmt.where(vault_documents.c.status != "archived")
        if query:
            like = f"%{query.lower()}%"
            stmt = stmt.where(vault_documents.c.display_name.ilike(like))
        stmt = stmt.distinct().order_by(desc(vault_documents.c.created_at)).limit(limit)
        rows = conn.execute(stmt).mappings().all()

        out = []
        for doc in rows:
            if year and (doc["created_at"] is None or doc["created_at"].year != int(year)):
                continue
            links = _links_rows(conn, doc["id"])
            try:
                _authorize(principal, doc, links)
            except VaultPermissionError:
                continue                                  # hide docs the user may not access
            out.append(dict(doc))
        return out


def download_target(principal, document_id, *, actor_user_id=None, ip_address=None):
    """Authorize a download and return ``(path, filename, mime_type)`` for the current version.
    Records a 'download' audit event."""
    with engine.begin() as conn:
        doc = conn.execute(
            select(vault_documents).where(vault_documents.c.id == document_id)).mappings().first()
        if doc is None:
            raise VaultNotFound(f"Vault document {document_id} not found.")
        links = _links_rows(conn, document_id)
        _authorize(principal, doc, links)
        path = storage.resolve_path(doc["storage_key"])
        record_audit(conn, document_id=document_id, user_id=actor_user_id,
                     action="download", ip_address=ip_address,
                     metadata={"version": doc["current_version"]})
    return path, doc["original_filename"], doc["mime_type"]


# --- writes ------------------------------------------------------------------

def create_document(principal, *, source, original_filename, display_name, category, document_type=None,
                    security_classification="internal", status="uploaded", mime_type=None,
                    actor_user_id=None, ip_address=None, person_id=None, household_id=None,
                    organization_id=None, engagement_id=None, work_item_id=None):
    """Store a new document (v1), link it to the client, and audit the upload."""
    if category not in CATEGORIES:
        raise ValueError(f"Invalid category '{category}'.")
    if status not in STATUSES:
        raise ValueError(f"Invalid status '{status}'.")
    if not principal.can("vault.upload"):
        raise VaultPermissionError("Missing capability: vault.upload.")
    if not can_access_category(principal, category):
        raise VaultPermissionError(f"Not authorized to upload '{category}' documents.")
    if not (principal.can("record.read_all")
            or (person_id and record_in_scope(principal, "person", person_id, write=True))
            or (household_id and record_in_scope(principal, "household", household_id, write=True))
            or not (person_id or household_id)):
        raise VaultPermissionError("Client record is out of your scope.")

    stored = storage.save_stream(source, original_filename=original_filename)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        doc_id = conn.execute(vault_documents.insert().values(
            display_name=display_name, original_filename=original_filename,
            document_type=document_type, category=category,
            security_classification=security_classification, status=status,
            mime_type=mime_type, file_size=stored["file_size"], storage_key=stored["storage_key"],
            checksum_sha256=stored["checksum_sha256"], current_version=1,
            uploaded_by_user_id=actor_user_id, created_at=now, updated_at=now,
        ).returning(vault_documents.c.id)).scalar_one()
        conn.execute(vault_document_versions.insert().values(
            document_id=doc_id, version_number=1, storage_key=stored["storage_key"],
            checksum_sha256=stored["checksum_sha256"], file_size=stored["file_size"],
            uploaded_by_user_id=actor_user_id, created_at=now))
        conn.execute(vault_document_links.insert().values(
            document_id=doc_id, person_id=person_id, household_id=household_id,
            organization_id=organization_id, engagement_id=engagement_id, work_item_id=work_item_id))
        record_audit(conn, document_id=doc_id, user_id=actor_user_id, action="upload",
                     ip_address=ip_address,
                     metadata={"category": category, "filename": original_filename})
    return doc_id


def add_version(principal, document_id, *, source, original_filename, actor_user_id=None, ip_address=None):
    """Store a new version, increment ``version_number``/``current_version``, and audit."""
    with engine.begin() as conn:
        doc = conn.execute(
            select(vault_documents).where(vault_documents.c.id == document_id)).mappings().first()
        if doc is None:
            raise VaultNotFound(f"Vault document {document_id} not found.")
        links = _links_rows(conn, document_id)
        if not principal.can("vault.upload"):
            raise VaultPermissionError("Missing capability: vault.upload.")
        _authorize(principal, doc, links, write=True)
        stored = storage.save_stream(source, original_filename=original_filename)
        next_version = int(doc["current_version"]) + 1
        now = datetime.now(UTC)
        conn.execute(vault_document_versions.insert().values(
            document_id=document_id, version_number=next_version, storage_key=stored["storage_key"],
            checksum_sha256=stored["checksum_sha256"], file_size=stored["file_size"],
            uploaded_by_user_id=actor_user_id, created_at=now))
        conn.execute(vault_documents.update().where(vault_documents.c.id == document_id).values(
            current_version=next_version, storage_key=stored["storage_key"],
            checksum_sha256=stored["checksum_sha256"], file_size=stored["file_size"],
            original_filename=original_filename, updated_at=now))
        record_audit(conn, document_id=document_id, user_id=actor_user_id, action="version",
                     ip_address=ip_address, metadata={"version": next_version})
    return next_version


_EDITABLE = {"display_name", "category", "document_type", "security_classification", "status"}


def update_metadata(principal, document_id, *, changes, actor_user_id=None, ip_address=None):
    """Edit metadata fields (from the editable allow-list) and audit the change."""
    fields = {k: v for k, v in (changes or {}).items() if k in _EDITABLE and v is not None}
    if "category" in fields and fields["category"] not in CATEGORIES:
        raise ValueError(f"Invalid category '{fields['category']}'.")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"Invalid status '{fields['status']}'.")
    with engine.begin() as conn:
        doc = conn.execute(
            select(vault_documents).where(vault_documents.c.id == document_id)).mappings().first()
        if doc is None:
            raise VaultNotFound(f"Vault document {document_id} not found.")
        links = _links_rows(conn, document_id)
        if not principal.can("vault.manage"):
            raise VaultPermissionError("Missing capability: vault.manage.")
        _authorize(principal, doc, links, write=True)
        if fields:
            conn.execute(vault_documents.update().where(vault_documents.c.id == document_id).values(
                updated_at=datetime.now(UTC), **fields))
        record_audit(conn, document_id=document_id, user_id=actor_user_id, action="update",
                     ip_address=ip_address, metadata={"changed": sorted(fields)})
    return fields


def archive_document(principal, document_id, *, actor_user_id=None, ip_address=None):
    """Archive (soft) — sets status='archived' + archived_at, PRESERVING the row, versions and files."""
    with engine.begin() as conn:
        doc = conn.execute(
            select(vault_documents).where(vault_documents.c.id == document_id)).mappings().first()
        if doc is None:
            raise VaultNotFound(f"Vault document {document_id} not found.")
        links = _links_rows(conn, document_id)
        if not principal.can("vault.manage"):
            raise VaultPermissionError("Missing capability: vault.manage.")
        _authorize(principal, doc, links, write=True)
        now = datetime.now(UTC)
        conn.execute(vault_documents.update().where(vault_documents.c.id == document_id).values(
            status="archived", archived_at=now, updated_at=now))
        record_audit(conn, document_id=document_id, user_id=actor_user_id, action="archive",
                     ip_address=ip_address)
    return True


def get_audit(principal, document_id):
    """Audit history for a document (authorizes; does not itself add an audit row)."""
    with engine.begin() as conn:
        doc = conn.execute(
            select(vault_documents).where(vault_documents.c.id == document_id)).mappings().first()
        if doc is None:
            raise VaultNotFound(f"Vault document {document_id} not found.")
        links = _links_rows(conn, document_id)
        _authorize(principal, doc, links)
        rows = conn.execute(
            select(vault_document_audit_events)
            .where(vault_document_audit_events.c.document_id == document_id)
            .order_by(desc(vault_document_audit_events.c.occurred_at))).mappings().all()
        return [dict(r) for r in rows]
