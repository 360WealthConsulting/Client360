"""Microsoft SharePoint — a first-class canonical SOURCE PROVIDER for Client360 (not a separate app).

SharePoint documents become CANONICAL documents (ADR-072): each discovered item is hashed (SHA-256) and
resolved against the existing corpus — an identical document already present from another source (TaxDome,
Drake, Upload, …) is REUSED and simply gains a SharePoint source reference (provenance preserved); a new
document creates the canonical row plus its first source reference. Ownership follows the platform's
household/person model (ADR-073). Nothing here is a parallel document system: canonical storage, dedup,
and ownership are the platform's — SharePoint contributes source references only, surfaced by the existing
Client Workspace Documents/Timeline tabs. No SharePoint-specific pages.

Honest boundary: a live Microsoft Graph / SharePoint tenant is environment-specific, so the network
enumeration + content download lives in the (deployment-time) connector. This module is the
canonical-integration mechanism it feeds: it takes structured SharePoint *items* (what Graph
``/sites/{id}/drives/{id}/root/children`` + ``/content`` yields — id, name, webUrl, site, library, parent
path, author, created/modified, size, and a locally-staged content path) and integrates them idempotently.
Supported types (PDF, Word, Excel, PowerPoint, images, text, email attachments, and other office/document
formats) are classified by name; deleted items and metadata changes are reconciled on each run.

CLI (given a connector that has staged items to a manifest JSON)::

    python -m app.importers.sharepoint --manifest staged_items.json
    python -m app.importers.sharepoint --manifest staged_items.json --dry-run
    python -m app.importers.sharepoint --manifest staged_items.json --purge-missing   # explicit; never automatic
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
from collections import namedtuple
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, and_, create_engine, select

from app.importers.taxdome_drive import (
    _content_sha256,
    _copy_verified,
    _is_ignored_file,
    infer_category,
    resolve_folder,
    sanitize_relative_path,
)

SOURCE_SYSTEM = "SharePoint"
STORAGE_PROVIDER = "Client360 Local"
SYNC_VERSION = 1
DEFAULT_DESTINATION_ROOT = os.getenv(
    "CLIENT360_SHAREPOINT_DOCUMENT_ROOT", r"C:\Client360\Data\Documents\SharePoint")

_Database = namedtuple("_Database", "engine documents document_sources")

# Extension → coarse document family (discovery; name-only, no contents/OCR).
_DOC_FAMILY = {
    "pdf": {"pdf"},
    "word": {"doc", "docx", "docm", "dot", "dotx", "rtf"},
    "excel": {"xls", "xlsx", "xlsm", "xlsb", "csv"},
    "powerpoint": {"ppt", "pptx", "ppsx", "pps"},
    "image": {"png", "jpg", "jpeg", "gif", "tif", "tiff", "bmp", "heic", "webp"},
    "text": {"txt", "md", "log"},
    "email": {"msg", "eml"},
}


@cache
def _database():
    load_dotenv("app/.env")
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is missing from app/.env")
    engine = create_engine(url)
    md = MetaData()
    md.reflect(bind=engine)
    return _Database(engine, md.tables["documents"], md.tables["document_sources"])


# --- classification ----------------------------------------------------------

def sharepoint_doc_type(name: str) -> str:
    """Coarse family by file extension. Email attachments arrive as their own file with the parent
    email recorded in item metadata; a bare ``.msg``/``.eml`` is classified as an email."""
    ext = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    for family, exts in _DOC_FAMILY.items():
        if ext in exts:
            return "email_attachment" if family == "email" else family
    return "other"


def _stored_name(item_key: str, sha: str = "") -> str:
    # Include the content hash so a CHANGED file at the same SharePoint URI produces a NEW canonical
    # version with a unique stored_name (the old uri-only key collided on documents_stored_name_key).
    import hashlib
    return "sharepoint:" + hashlib.sha256(f"{item_key or ''}:{sha or ''}".encode()).hexdigest()


def _item_uri(item: dict) -> str:
    """Stable per-item source URI: the Graph webUrl, else a synthesized site/library/id locator."""
    return (item.get("web_url") or item.get("webUrl")
            or f"sharepoint://{item.get('site','')}/{item.get('library','')}/{item.get('item_id','')}")


def _rel_path(item: dict, filename: str) -> str:
    """Human-meaningful relative path under the destination root: Site/Library/<folder>/<file>."""
    parts = [str(item.get("site") or "Site"), str(item.get("library") or "Documents")]
    folder = (item.get("folder_path") or item.get("folder") or "").strip("/")
    if folder:
        parts.extend(folder.split("/"))
    parts.append(filename)
    return "/".join(p for p in parts if p)


# --- import ------------------------------------------------------------------

def _new_summary(dry_run: bool) -> dict:
    return {"items_examined": 0, "ignored": 0, "canonical_created": 0, "reused_canonical": 0,
            "source_refs_added": 0, "metadata_updated": 0, "skipped": 0, "deleted": 0,
            "missing": 0, "purged": 0, "bytes_copied": 0, "linked_person": 0,
            "linked_household": 0, "affected_document_ids": [], "errors": [], "dry_run": dry_run,
            "status": "started"}


def import_sharepoint_items(items, *, destination_root=None, actor_user_id=None, request_id=None,
                            dry_run=False, purge_missing=False, progress=None,
                            progress_interval=100) -> dict:
    """Integrate structured SharePoint items into the canonical document model (ADR-072/073).

    Each ``item`` is a dict:
      required: ``name``; one of ``web_url`` / (``site``+``library``+``item_id``) for identity.
      content:  ``local_path`` (a locally-staged download) — required to import a live item; omit for
                a ``deleted`` item.
      metadata: ``site``, ``library``, ``folder_path``, ``item_id``, ``author``, ``created_at``,
                ``modified_at``, ``size``, ``content_type``, ``parent_email`` (for attachments).
      ownership: ``person_id`` / ``household_id`` directly, or ``client_folder`` to resolve.
      lifecycle: ``deleted=True`` marks a removed SharePoint item.

    Idempotent + resumable: unchanged items (same size + modified) are skipped without re-hashing;
    hash matches reuse the canonical document; metadata changes refresh the source reference; items no
    longer present are marked unavailable (never deleting the canonical document or local copy)."""
    from app.services.document_sources import mark_source_unavailable, resolve_or_create_canonical
    db = _database()
    destination = Path(destination_root or DEFAULT_DESTINATION_ROOT)
    summary = _new_summary(dry_run)
    seen_uris: set[str] = set()

    for item in items:
        summary["items_examined"] += 1
        try:
            _import_one(db, destination, item, dry_run, seen_uris, summary,
                        resolve_or_create_canonical, mark_source_unavailable)
        except Exception as exc:      # noqa: BLE001 — record & continue
            summary["errors"].append(f"{item.get('name', '?')}: {exc}")
        if progress and summary["items_examined"] % max(progress_interval, 1) == 0:
            progress(summary)

    # Safety — "missing" reconciliation (marking every SharePoint ref NOT seen this pass as unavailable)
    # is only valid against a COMPLETE, authoritative enumeration from a real sync. Skip it when that
    # precondition does not hold, so the existing corpus is never falsely reported/marked missing:
    #   * dry-run: a preview that may be partial (e.g. `--limit 10` enumerates only some items) and whose
    #     staged records are metadata-only — computing "missing" here would flag the rest of the corpus.
    #   * empty staging: zero seen items almost always means a connector/enumeration failure, not that
    #     every document was deleted.
    # A real (non-dry-run) sync with items still reconciles missing/deleted exactly as before.
    if dry_run:
        summary["missing_reconciliation_skipped"] = "dry-run preview (possibly partial); not reconciled"
    elif seen_uris:
        _handle_missing(db, seen_uris, dry_run, purge_missing, summary, mark_source_unavailable)
    else:
        summary["missing_reconciliation_skipped"] = "no items staged (connector returned zero)"
    _audit(db, summary, actor_user_id, request_id, dry_run)

    summary["status"] = "dry_run" if dry_run else ("completed_with_errors" if summary["errors"]
                                                   else "completed")
    if progress:
        progress(summary)
    return summary


def _import_one(db, destination, item, dry_run, seen_uris, summary, resolve_or_create_canonical,
                mark_source_unavailable):
    filename = item.get("name") or ""
    if not filename or _is_ignored_file(filename):
        summary["ignored"] += 1
        return
    uri = _item_uri(item)
    seen_uris.add(uri)

    # Deleted SharePoint item: drop only the SharePoint source reference (canonical + local copy stay).
    if item.get("deleted"):
        summary["deleted"] += 1
        if not dry_run:
            with db.engine.begin() as conn:
                mark_source_unavailable(conn, _doc_id_for_uri(db, uri), SOURCE_SYSTEM, uri)
        return

    size_hint = item.get("size")
    modified = item.get("modified_at")
    # Incremental fast path: an unchanged item (same size + modified) is skipped without re-hashing.
    with db.engine.connect() as conn:
        existing_ref = conn.execute(
            select(db.document_sources.c.document_id, db.document_sources.c.metadata)
            .where(db.document_sources.c.source_system == SOURCE_SYSTEM,
                   db.document_sources.c.source_uri == uri)).mappings().first()
    if existing_ref is not None:
        meta = existing_ref["metadata"] or {}
        if size_hint is not None and meta.get("size") == int(size_hint) \
                and meta.get("modified") == _norm(modified):
            summary["skipped"] += 1
            return

    if dry_run:
        summary["canonical_created" if existing_ref is None else "reused_canonical"] += 1
        if size_hint is not None:
            summary["bytes_copied"] += int(size_hint)
        return

    local_path = item.get("local_path")
    if not local_path or not Path(local_path).exists():
        raise ValueError("missing staged local_path for a live SharePoint item")
    staged = Path(local_path)
    sha = _content_sha256(staged)
    size = staged.stat().st_size
    safe_rel = sanitize_relative_path(_rel_path(item, filename))

    with db.engine.begin() as conn:
        # Never duplicate: a content hash already in the corpus is reused (no second local copy).
        existing_doc = conn.execute(
            select(db.documents.c.id).where(db.documents.c.sha256 == sha,
                                            db.documents.c.status != "deleted")
            .order_by(db.documents.c.id).limit(1)).scalar()
        if existing_doc is None:
            dest_abs = (destination / Path(*safe_rel.parts))
            _copy_verified(staged, dest_abs)               # canonical local copy + verify
            storage_uri, storage_path = str(dest_abs), str(safe_rel)
            summary["bytes_copied"] += size
        else:
            storage_uri, storage_path = "", ""             # reuse ignores storage_*

        household_id = item.get("household_id")
        person_id = item.get("person_id")
        if person_id is None and household_id is None and item.get("client_folder"):
            household_id, person_id = resolve_folder(conn, item["client_folder"])
        if person_id is not None:
            summary["linked_person"] += 1
        if household_id is not None:
            summary["linked_household"] += 1

        tags = {
            "source_system": SOURCE_SYSTEM, "sharepoint_doc_type": sharepoint_doc_type(filename),
            "sharepoint_site": item.get("site"), "sharepoint_library": item.get("library"),
            "sharepoint_folder": item.get("folder_path") or item.get("folder"),
            "sharepoint_item_id": item.get("item_id"), "author": item.get("author"),
            "web_url": uri, "parent_email": item.get("parent_email"),
            "source_created": _norm(item.get("created_at")),
            "source_modified": _norm(modified), "sync_version": SYNC_VERSION,
            "last_synced_at": datetime.now(UTC).isoformat(),
        }
        result = resolve_or_create_canonical(
            sha256=sha, original_name=filename, stored_name=_stored_name(uri, sha),
            storage_provider=STORAGE_PROVIDER, storage_uri=storage_uri, storage_path=storage_path,
            size_bytes=size, content_type=item.get("content_type") or mimetypes.guess_type(filename)[0],
            category=infer_category(filename, safe_rel.as_posix()), tags=tags,
            person_id=person_id, household_id=household_id,
            source_system=SOURCE_SYSTEM, source_uri=uri, source_path=str(safe_rel),
            source_external_id=item.get("item_id"), conn=conn)
        # Persist size/modified + rich metadata on the source ref (incremental key + provenance).
        ref_meta = {"size": int(size), "modified": _norm(modified),
                    "site": item.get("site"), "library": item.get("library"),
                    "folder": item.get("folder_path") or item.get("folder"),
                    "author": item.get("author"), "created": _norm(item.get("created_at")),
                    "sharepoint_doc_type": tags["sharepoint_doc_type"],
                    "parent_email": item.get("parent_email")}
        conn.execute(db.document_sources.update().where(and_(
            db.document_sources.c.document_id == result["document_id"],
            db.document_sources.c.source_system == SOURCE_SYSTEM,
            db.document_sources.c.source_uri == uri)).values(metadata=ref_meta))

    summary["reused_canonical" if result["reused"] else "canonical_created"] += 1
    summary["source_refs_added"] += 1
    if not result["reused"]:
        # A NEW canonical document (new content / new version) — the only case that needs analysis.
        summary["affected_document_ids"].append(result["document_id"])
    if existing_ref is not None:
        summary["metadata_updated"] += 1


def _doc_id_for_uri(db, uri: str):
    with db.engine.connect() as conn:
        return conn.execute(select(db.document_sources.c.document_id).where(and_(
            db.document_sources.c.source_system == SOURCE_SYSTEM,
            db.document_sources.c.source_uri == uri)).limit(1)).scalar()


def _handle_missing(db, seen_uris, dry_run, purge_missing, summary, mark_source_unavailable):
    """SharePoint source references not seen this run: mark unavailable (canonical + local copy kept).
    ``--purge-missing`` additionally flags them purged; it never deletes a canonical document."""
    with db.engine.connect() as conn:
        rows = conn.execute(
            select(db.document_sources.c.document_id, db.document_sources.c.source_uri,
                   db.document_sources.c.available)
            .where(db.document_sources.c.source_system == SOURCE_SYSTEM)).mappings().all()
    for r in rows:
        if r["source_uri"] in seen_uris or not r["available"]:
            continue
        summary["missing"] += 1
        if purge_missing:
            summary["purged"] += 1
        if dry_run:
            continue
        with db.engine.begin() as conn:
            mark_source_unavailable(conn, r["document_id"], SOURCE_SYSTEM, r["source_uri"])


def _audit(db, summary, actor_user_id, request_id, dry_run):
    if dry_run:
        return
    import uuid

    from app.security.audit import write_audit_event
    write_audit_event(
        action="sharepoint.imported", entity_type="document_source", entity_id=None,
        actor_user_id=actor_user_id, request_id=request_id or f"sharepoint-{uuid.uuid4()}",
        metadata={k: summary[k] for k in (
            "items_examined", "canonical_created", "reused_canonical", "source_refs_added",
            "metadata_updated", "skipped", "deleted", "missing", "purged",
            "linked_person", "linked_household")})


def _norm(value):
    """Normalize a date/datetime/string to an ISO string for stable metadata comparison."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# --- CLI ---------------------------------------------------------------------

def _parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m app.importers.sharepoint",
                                description="Integrate staged SharePoint items as a canonical source.")
    p.add_argument("--manifest", required=True, help="JSON file: a list of staged SharePoint items.")
    p.add_argument("--destination-root", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--purge-missing", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    items = json.loads(Path(args.manifest).read_text())
    summary = import_sharepoint_items(items, destination_root=args.destination_root,
                                      dry_run=args.dry_run, purge_missing=args.purge_missing)
    label = "DRY RUN — no changes made" if args.dry_run else "SharePoint integration complete"
    print(f"SharePoint {label}.")
    for k in ("items_examined", "ignored", "canonical_created", "reused_canonical", "source_refs_added",
              "metadata_updated", "skipped", "deleted", "missing", "purged", "bytes_copied",
              "linked_household", "linked_person", "status"):
        print(f"  {k}: {summary[k]}")
    if summary["errors"]:
        print(f"  errors ({len(summary['errors'])}):")
        for e in summary["errors"][:20]:
            print(f"    - {e}")
    return 1 if summary["status"] == "completed_with_errors" else 0


if __name__ == "__main__":
    raise SystemExit(main())
