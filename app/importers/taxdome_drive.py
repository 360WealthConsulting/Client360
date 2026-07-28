"""TaxDome Drive document indexer for Client360.

Indexes the mounted TaxDome Drive (Z:\\ by default) into Client360's EXISTING document
model — it does NOT introduce a parallel document platform, and it NEVER copies, moves,
or renames anything on the drive (Z:\\ is treated as read-only, the authoritative live
repository). For each file it records METADATA ONLY (no contents, no OCR):

  * documents.storage_provider = "TaxDome Drive"  (the external-source discriminator)
  * documents.storage_uri      = the absolute path on Z:\\  (an external reference; not copied)
  * documents.storage_path     = the path relative to the drive root
  * documents.original_name    = the filename
  * documents.size_bytes / sha256 = size + content hash (hash only — contents are never stored)
  * documents.category / classification = a lightweight path/name-only inference
  * documents.tags (JSONB)     = {source_system, taxdome_folder, relative_path, extension,
                                  file_created, file_modified, available, last_scan_id}
  * documents.status / archived = availability (a missing file is marked per existing rules)

Each top-level folder under the root is treated as one TaxDome account/client. A folder is
linked to a canonical person ONLY on a unique exact normalized-name match (never a weak name
match); everything else is left unresolved (person_id NULL) for the TaxDome Drive review queue.

Rescans are idempotent: unchanged files are skipped, changed files (new hash) are updated, new
files are inserted, and files that vanished from the drive are marked unavailable. Every scan is
recorded in import_jobs (source_system="TaxDome Drive"). Run:  python -m app.importers.taxdome_drive
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import namedtuple
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, and_, create_engine, func, or_, select

SOURCE_SYSTEM = "TaxDome Drive"
DEFAULT_ROOT = os.getenv("TAXDOME_DRIVE_ROOT", "Z:\\")

_Database = namedtuple("_Database", "engine documents people households import_jobs")


@cache
def _database():
    """Resolve the engine and tables on first use, never at import (import-inert)."""
    load_dotenv("app/.env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing from app/.env")
    engine = create_engine(database_url)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return _Database(engine, metadata.tables["documents"], metadata.tables["people"],
                     metadata.tables["households"], metadata.tables["import_jobs"])


# --- helpers -----------------------------------------------------------------

def _stored_name(abs_path: str) -> str:
    """A stable, unique key for a drive file location (documents.stored_name is UNIQUE).

    Derived from the absolute path so a rescan matches the SAME row (content changes update
    it); a moved/renamed file becomes a new row and the old path is marked missing."""
    return "taxdome:" + hashlib.sha256(abs_path.encode("utf-8")).hexdigest()


def _content_sha256(path: Path) -> str:
    """SHA-256 of the file contents. Only the hash is kept — contents are never stored."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _created_ts(stat) -> float | None:
    return getattr(stat, "st_birthtime", None) or stat.st_ctime


def infer_category(filename: str, relative_path: str) -> str | None:
    """Lightweight, path/name-ONLY category inference (no contents, no OCR)."""
    low = f"{filename} {relative_path}".lower()
    if any(k in low for k in ("1040", "w-2", "w2", "1099", "tax return", "8879", "schedule ", "return")):
        return "tax_document"
    if any(k in low for k in ("statement", "brokerage", "1099-div", "1099-int")):
        return "statement"
    if any(k in low for k in ("agreement", "engagement", "contract", "signed", "e-sign", "esign")):
        return "agreement"
    if any(k in low for k in ("driver", "license", "passport", " id ", "identification")):
        return "identification"
    if any(k in low for k in ("invoice", "receipt", "billing")):
        return "invoice"
    return None


_NAME_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _name_key(name: str | None) -> str:
    """Order-insensitive normalized name key so 'Smith, John' matches 'John Smith'.

    Drops common suffixes (family/trust/llc/…) so a folder named 'Hawthorne Family' can match a
    person 'Taylor Hawthorne' is NOT attempted here (that is a weak match) — only exact token
    sets match, which keeps auto-linking conservative."""
    if not name:
        return ""
    tokens = _NAME_TOKEN_RE.findall(name.lower())
    drop = {"family", "trust", "llc", "inc", "the", "and", "household", "jr", "sr", "ii", "iii"}
    tokens = [t for t in tokens if t not in drop]
    return " ".join(sorted(tokens))


# --- scan --------------------------------------------------------------------

def _autolink_person_id(conn, folder_name: str) -> int | None:
    """Return a canonical person_id ONLY when exactly one person has the exact normalized-name
    match for this folder — a strong, unique match. Zero or several matches → None (unresolved,
    for the review queue). Never a weak name match."""
    db = _database()
    key = _name_key(folder_name)
    if not key:
        return None
    rows = conn.execute(select(db.people.c.id, db.people.c.full_name)).mappings().all()
    matches = [r["id"] for r in rows if _name_key(r["full_name"]) == key]
    return matches[0] if len(matches) == 1 else None


def suggest_people(conn, folder_name: str, *, limit: int = 5) -> list[dict]:
    """Candidate canonical people for an unresolved folder — exact normalized-name matches first,
    then a loose ILIKE on any token (a SUGGESTION for a human; never auto-applied)."""
    db = _database()
    key = _name_key(folder_name)
    tokens = _NAME_TOKEN_RE.findall((folder_name or "").lower())
    rows = conn.execute(
        select(db.people.c.id, db.people.c.full_name, db.people.c.primary_email,
               db.people.c.household_id)
    ).mappings().all()
    exact, loose = [], []
    for r in rows:
        pk = _name_key(r["full_name"])
        if pk and pk == key:
            exact.append({**dict(r), "reason": "exact name match", "confidence": "high"})
        elif tokens and any(t in (r["full_name"] or "").lower() for t in tokens if len(t) > 2):
            loose.append({**dict(r), "reason": "partial name match", "confidence": "low"})
    return (exact + loose)[:limit]


def scan(root: str | os.PathLike | None = None, *, actor_user_id: int | None = None):
    """Recursively index the TaxDome Drive. Read-only w.r.t. the drive; idempotent w.r.t. the DB.

    Returns a summary dict: folders, files_scanned, new, changed, unchanged, missing,
    folders_linked, folders_unresolved, errors, scan_id, started_at, completed_at, status.
    """
    db = _database()
    root_path = Path(root or DEFAULT_ROOT)
    started = datetime.now(UTC)
    errors: list[str] = []
    new = changed = unchanged = 0
    seen_keys: set[str] = set()
    folders_seen: set[str] = set()

    with db.engine.begin() as conn:
        scan_id = conn.execute(
            db.import_jobs.insert().values(
                source_system=SOURCE_SYSTEM, source_file=str(root_path),
                status="started").returning(db.import_jobs.c.id)
        ).scalar_one()

        if not root_path.exists():
            errors.append(f"drive root not found: {root_path}")
        else:
            for entry in sorted(p for p in root_path.iterdir() if p.is_dir()):
                folder_name = entry.name
                folders_seen.add(folder_name)
                for dirpath, _dirs, filenames in os.walk(entry):
                    for filename in sorted(filenames):
                        abs_path = os.path.join(dirpath, filename)
                        try:
                            stat = os.stat(abs_path)
                            content_sha = _content_sha256(Path(abs_path))
                        except OSError as exc:
                            errors.append(f"{abs_path}: {exc}")
                            continue
                        rel_path = os.path.relpath(abs_path, root_path)
                        key = _stored_name(abs_path)
                        seen_keys.add(key)
                        ext = Path(filename).suffix.lower()
                        tags = {
                            "source_system": SOURCE_SYSTEM, "taxdome_folder": folder_name,
                            "relative_path": rel_path, "extension": ext,
                            "file_created": _iso(_created_ts(stat)),
                            "file_modified": _iso(stat.st_mtime), "available": True,
                            "last_scan_id": scan_id,
                        }
                        existing = conn.execute(
                            select(db.documents.c.id, db.documents.c.sha256, db.documents.c.status)
                            .where(db.documents.c.stored_name == key)
                        ).mappings().first()
                        base_values = {
                            "storage_provider": SOURCE_SYSTEM, "storage_uri": abs_path,
                            "storage_path": rel_path, "original_name": filename,
                            "size_bytes": int(stat.st_size), "sha256": content_sha,
                            "category": infer_category(filename, rel_path),
                            "tags": tags, "status": "active", "archived": False,
                            "archived_at": None, "updated_at": datetime.now(UTC),
                        }
                        if existing is None:
                            conn.execute(db.documents.insert().values(
                                stored_name=key, **base_values))
                            new += 1
                        elif existing["sha256"] != content_sha or existing["status"] != "active":
                            conn.execute(db.documents.update()
                                         .where(db.documents.c.id == existing["id"])
                                         .values(**base_values))
                            changed += 1
                        else:
                            unchanged += 1

            # Conservative folder -> canonical person auto-link (unique exact-name only).
            for folder_name in folders_seen:
                already = conn.execute(
                    select(func.count()).select_from(db.documents).where(and_(
                        db.documents.c.storage_provider == SOURCE_SYSTEM,
                        db.documents.c.tags["taxdome_folder"].astext == folder_name,
                        db.documents.c.person_id.isnot(None)))
                ).scalar() or 0
                if already:
                    continue
                pid = _autolink_person_id(conn, folder_name)
                if pid is not None:
                    conn.execute(db.documents.update().where(and_(
                        db.documents.c.storage_provider == SOURCE_SYSTEM,
                        db.documents.c.tags["taxdome_folder"].astext == folder_name)
                    ).values(person_id=pid))

        # Mark files that vanished from the drive as unavailable (existing retention rule).
        missing_rows = conn.execute(
            select(db.documents.c.id, db.documents.c.stored_name)
            .where(and_(db.documents.c.storage_provider == SOURCE_SYSTEM,
                        db.documents.c.status == "active"))
        ).mappings().all()
        gone = [r["id"] for r in missing_rows if r["stored_name"] not in seen_keys]
        if gone:
            # Mark vanished files unavailable per the existing document retention rule: 'archived'
            # (an allowed documents.status), which also hides them from the person document list.
            conn.execute(db.documents.update().where(db.documents.c.id.in_(gone)).values(
                status="archived", archived=True, archived_at=datetime.now(UTC)))
        missing = len(gone)

        folders_linked = conn.execute(
            select(func.count(func.distinct(db.documents.c.tags["taxdome_folder"].astext)))
            .where(and_(db.documents.c.storage_provider == SOURCE_SYSTEM,
                        db.documents.c.person_id.isnot(None)))
        ).scalar() or 0
        folders_total = conn.execute(
            select(func.count(func.distinct(db.documents.c.tags["taxdome_folder"].astext)))
            .where(db.documents.c.storage_provider == SOURCE_SYSTEM)
        ).scalar() or 0

        completed = datetime.now(UTC)
        summary = {
            "folders": len(folders_seen) or folders_total,
            "files_scanned": new + changed + unchanged, "new": new, "changed": changed,
            "unchanged": unchanged, "missing": missing, "folders_linked": folders_linked,
            "folders_unresolved": max(folders_total - folders_linked, 0),
            "errors": errors, "scan_id": scan_id,
            "started_at": started.isoformat(), "completed_at": completed.isoformat(),
            "status": "completed" if not errors else "completed_with_errors",
        }
        conn.execute(db.import_jobs.update().where(db.import_jobs.c.id == scan_id).values(
            status=summary["status"], completed_at=completed,
            rows_read=summary["files_scanned"], rows_inserted=new, rows_updated=changed,
            rows_skipped=unchanged, error_message=json.dumps(summary)))
    return summary


def latest_scan():
    """The most recent TaxDome Drive scan summary (from import_jobs), or None."""
    db = _database()
    with db.engine.connect() as conn:
        row = conn.execute(
            select(db.import_jobs).where(db.import_jobs.c.source_system == SOURCE_SYSTEM)
            .order_by(db.import_jobs.c.id.desc()).limit(1)
        ).mappings().first()
    if not row:
        return None
    try:
        summary = json.loads(row["error_message"]) if row["error_message"] else {}
    except (TypeError, ValueError):
        summary = {}
    summary.setdefault("status", row["status"])
    summary.setdefault("completed_at", row["completed_at"].isoformat() if row["completed_at"] else None)
    return summary


def _search_documents(conn, term: str, *, limit: int = 50):
    """Search indexed TaxDome document METADATA (filename + folder) — reused by the demo UI so
    indexed TaxDome documents are searchable in Client360."""
    db = _database()
    like = f"%{term.strip()}%"
    return conn.execute(
        select(db.documents.c.id, db.documents.c.original_name, db.documents.c.person_id,
               db.documents.c.tags["taxdome_folder"].astext.label("folder"),
               db.documents.c.category, db.documents.c.size_bytes, db.documents.c.status)
        .where(and_(db.documents.c.storage_provider == SOURCE_SYSTEM,
                    or_(db.documents.c.original_name.ilike(like),
                        db.documents.c.tags["taxdome_folder"].astext.ilike(like))))
        .order_by(db.documents.c.original_name).limit(limit)
    ).mappings().all()


def main():
    summary = scan()
    print("TaxDome Drive scan complete.")
    for key in ("folders", "files_scanned", "new", "changed", "unchanged", "missing",
                "folders_linked", "folders_unresolved", "status"):
        print(f"  {key}: {summary[key]}")
    if summary["errors"]:
        print(f"  errors ({len(summary['errors'])}):")
        for e in summary["errors"][:20]:
            print(f"    - {e}")
    return summary


if __name__ == "__main__":
    main()
