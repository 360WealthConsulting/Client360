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


def _normalize_graph_folder(path: str) -> str:
    """Strip the Microsoft Graph transport PREFIX from a driveItem ``parentReference.path`` so only the
    real SharePoint folder hierarchy remains, e.g.::

        /drives/<drive-id>/root:/360 Wealth Consulting, LLC/Accounts  ->  360 Wealth Consulting, LLC/Accounts
        /drive/root:/Statements                                       ->  Statements
        /drives/<drive-id>/root:                                      ->  ''  (drive root)
        Clients/Jane Doe                                              ->  Clients/Jane Doe  (already relative)

    This removes ONLY the ``…/root:`` transport syntax. The folder hierarchy is preserved verbatim and the
    result is still passed through :func:`sanitize_relative_path`, so the general unsafe-path guard
    (``..``, absolute paths, drive letters, any residual ``:`` segment) is fully intact — e.g. a traversal
    hidden behind the prefix (``…/root:/../../etc``) normalizes to ``../../etc`` and is still rejected."""
    if not path:
        return ""
    p = str(path)
    marker = "root:"
    idx = p.find(marker)                                   # SharePoint folder names cannot contain ':'
    if idx != -1:
        p = p[idx + len(marker):]                          # keep only what follows the transport prefix
    return p.strip("/")


def _rel_path(item: dict, filename: str) -> str:
    """Human-meaningful relative path under the destination root: Site/Library/<folder>/<file>."""
    parts = [str(item.get("site") or "Site"), str(item.get("library") or "Documents")]
    folder = _normalize_graph_folder(item.get("folder_path") or item.get("folder") or "")
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


# The staged local file may be recorded under any of these keys — the connector uses its destination
# field (e.g. `target`); the importer needs an EXISTING local file. Never fabricated from the name.
_STAGED_PATH_FIELDS = ("local_path", "target", "dest", "destination", "downloaded_path", "staged_path",
                       "local_file", "file_path", "path")


def resolved_staged_path(item):
    """The staged local file for an item: ``local_path`` if it exists, else the connector's download path
    field (``target``/...) — accepted ONLY if it resolves to an existing FILE. None if none exists."""
    for field in _STAGED_PATH_FIELDS:
        v = item.get(field)
        if v and Path(str(v)).is_file():
            return str(v)
    return None


def _has_resolvable_file(row) -> bool:
    """True if a documents row points at an existing local file (storage_uri or storage_path)."""
    if row is None:
        return False
    for key in ("storage_uri", "storage_path"):
        v = row.get(key) if hasattr(row, "get") else None
        if v and Path(str(v)).exists():
            return True
    return False


def backfill_local_source(document_id, staged_path, *, destination_root=None) -> bool:
    """Give a canonical document a resolvable LOCAL file for OCR/analysis by copying a staged file into the
    canonical store and pointing storage_uri/path at it. SAFE: a no-op if the document already has a
    resolvable file; the staged content SHA must match the document's sha256 (never links the wrong file);
    never overwrites a good copy; never changes canonical identity/content. Returns True if it backfilled."""
    staged = Path(staged_path)
    if not staged.is_file():
        return False
    db = _database()
    destination = Path(destination_root or DEFAULT_DESTINATION_ROOT)
    with db.engine.begin() as conn:
        row = conn.execute(select(db.documents.c.storage_uri, db.documents.c.storage_path,
                                  db.documents.c.sha256, db.documents.c.original_name)
                           .where(db.documents.c.id == document_id)).mappings().first()
        if row is None or _has_resolvable_file(row):
            return False
        if row["sha256"] and _content_sha256(staged) != row["sha256"]:
            return False                                   # safety: staged file must be this document
        safe_rel = sanitize_relative_path(row["original_name"] or staged.name)
        dest_abs = destination / Path(*safe_rel.parts)
        _copy_verified(staged, dest_abs)
        conn.execute(db.documents.update().where(db.documents.c.id == document_id)
                     .values(storage_uri=str(dest_abs), storage_path=str(safe_rel)))
    return True


def import_sharepoint_items(items, *, destination_root=None, actor_user_id=None, request_id=None,
                            dry_run=False, purge_missing=False, authoritative=False, progress=None,
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
    # is ONLY valid against a COMPLETE, AUTHORITATIVE drive enumeration. It must be opted into explicitly
    # (authoritative=True) — never inferred from dry_run=False. Skip it otherwise so a partial batch never
    # falsely marks/reports the rest of the corpus missing:
    #   * non-authoritative: a partial/manual batch — a --manifest reconcile, a --limit run, or any input
    #     that is NOT a full drive snapshot. Reconciling here would flag every unseen ref (the 21,697 bug).
    #   * dry-run: a preview (also never authoritative) whose records may be metadata-only.
    #   * empty staging: zero seen items means a connector/enumeration failure, not a mass deletion.
    if not authoritative:
        summary["missing_reconciliation_skipped"] = "partial/non-authoritative batch"
    elif dry_run:
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
    if size_hint is None:
        size_hint = item.get("size_bytes")            # connector may record size under size_bytes
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

    # Normalize the staged file: prefer local_path, else the connector's download target (e.g. `target`),
    # accepted only if it is an existing FILE. Never fabricate a path from the item name.
    local_path = resolved_staged_path(item)
    if not local_path:
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
            storage_uri, storage_path = "", ""             # reuse ignores storage_* (keeps existing copy)
            # ...BUT if the reused canonical has NO resolvable local file (e.g. a prior migrated/dedup
            # document with empty/stale storage), OCR/analysis would have no source. Backfill a local copy
            # from the staged file — only when missing, content verified, never overwriting a good file.
            ex = conn.execute(select(db.documents.c.storage_uri, db.documents.c.storage_path)
                              .where(db.documents.c.id == existing_doc)).mappings().first()
            if not _has_resolvable_file(ex):
                dest_abs = (destination / Path(*safe_rel.parts))
                _copy_verified(staged, dest_abs)
                conn.execute(db.documents.update().where(db.documents.c.id == existing_doc)
                             .values(storage_uri=str(dest_abs), storage_path=str(safe_rel)))
                summary["bytes_copied"] += size
                summary["storage_backfilled"] = summary.get("storage_backfilled", 0) + 1

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
    p.add_argument("--authoritative", action="store_true",
                   help="the manifest is a COMPLETE drive snapshot; only then reconcile missing/deleted "
                        "refs. Omit for a partial/manual batch (default): missing is never reconciled.")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    items = json.loads(Path(args.manifest).read_text())
    summary = import_sharepoint_items(items, destination_root=args.destination_root,
                                      dry_run=args.dry_run, purge_missing=args.purge_missing,
                                      authoritative=args.authoritative)
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
