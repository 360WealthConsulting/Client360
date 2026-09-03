"""TaxDome Drive one-way document synchronization for Client360.

TaxDome Drive (``Z:\\`` by default) is treated as a **read-only external source**: nothing on it is
ever renamed, moved, modified, or deleted. Client360 maintains its own **durable local copies** under
``CLIENT360_TAXDOME_DOCUMENT_ROOT`` (default ``C:\\Client360\\Data\\Documents\\TaxDome``), preserving the
complete source-relative directory structure, and records each copy in the EXISTING ``documents`` table
(no parallel document platform). Every run is journalled in ``import_jobs``.

Sync contract (see the project docs for the operational runbook):
  * New source files are copied into the local store.
  * A changed source file is copied to a temp file in the destination directory, verified by size and
    SHA-256, then **atomically** swapped into place (``os.replace``) — a partial copy is never exposed.
  * Unchanged files are skipped using the stored source size + modified-time fast path; a hash is only
    computed when those disagree, to confirm whether the content really changed.
  * Individual file failures are recorded and the run continues; rescans are idempotent.
  * When a source file disappears the local copy is RETAINED: the document is flagged
    ``available_from_source=false`` (status stays ``active``, ``archived`` stays false). A local file is
    only removed with the explicit, off-by-default ``--purge-missing`` flag.

Synchronized documents carry ``storage_provider = "Client360 Local"`` and ``tags.source_system =
"TaxDome Drive"`` (the discriminator). Each top-level folder under the source root is one TaxDome
account; a folder is auto-linked to a canonical person ONLY on a unique exact normalized-name match —
never a weak match — and everything else is left unresolved for human review.

CLI::

    python -m app.importers.taxdome_drive
    python -m app.importers.taxdome_drive --dry-run
    python -m app.importers.taxdome_drive --source-root Z:\\
    python -m app.importers.taxdome_drive --destination-root C:\\Client360\\Data\\Documents\\TaxDome
    python -m app.importers.taxdome_drive --purge-missing        # explicit; never automatic

Forward compatibility (see docs/adr/ADR-072-canonical-document-model.md): the ``documents`` row IS the
canonical document (``documents.id`` is its stable id). ``storage_provider="Client360 Local"`` +
``storage_uri`` are the canonical local copy; the TaxDome **origin** is a *source reference* held in
``tags`` (``source_system``/``source_root``/``source_path``/…) alongside the content ``sha256``. That is
exactly the data a future ``document_sources`` table backfills, so adopting the canonical multi-source
model is an additive migration — no identity rework and no duplicate cleanup. Ownership
(``person_id``/``household_id``) is a relationship only and never participates in document identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import tempfile
from collections import namedtuple
from datetime import UTC, datetime
from functools import cache
from pathlib import Path, PurePosixPath

from dotenv import load_dotenv
from sqlalchemy import MetaData, and_, create_engine, func, or_, select
from sqlalchemy import text as sa_text

from app.services.storage_paths import document_root as _document_root

SOURCE_SYSTEM = "TaxDome Drive"          # tags.source_system discriminator (stable across the change)
STORAGE_PROVIDER = "Client360 Local"     # documents.storage_provider for a retained local copy
SYNC_VERSION = 2                          # bump when the sync semantics change

DEFAULT_SOURCE_ROOT = os.getenv("TAXDOME_DRIVE_ROOT", "Z:\\")
# CLIENT360_TAXDOME_DOCUMENT_ROOT still wins; else <CLIENT360_DATA_ROOT>\Documents\TaxDome; else the legacy
# C:\Client360\Data\Documents\TaxDome (unchanged when no base is set).
DEFAULT_DESTINATION_ROOT = _document_root("TaxDome", "CLIENT360_TAXDOME_DOCUMENT_ROOT")
DEFAULT_PROGRESS_INTERVAL = int(os.getenv("TAXDOME_SYNC_PROGRESS_INTERVAL", "100") or "100")
_CHUNK = 1024 * 1024

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


def taxdome_filter(documents_table):
    """SQL predicate selecting TaxDome-sourced documents regardless of storage_provider.

    Uses ``tags.source_system`` so it matches both the retained local copies written by this sync and
    any legacy metadata-only rows from the previous indexer."""
    return documents_table.c.tags["source_system"].astext == SOURCE_SYSTEM


# --- helpers -----------------------------------------------------------------

def _stored_name(source_relative_path: str) -> str:
    """Stable, unique key for ``documents.stored_name`` derived from the source-relative path, so a
    rescan matches the SAME row (content changes update it) and is independent of the drive letter."""
    norm = str(PurePosixPath(source_relative_path.replace("\\", "/"))).lower()
    return "taxdome:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _legacy_stored_name(absolute_source_path: str) -> str:
    """The stored_name the PREVIOUS (metadata-only) importer used: a hash of the ABSOLUTE source path.

    Kept so this sync recognizes and upgrades rows created before the switch to relative-path keys,
    instead of treating them as missing and inserting duplicates. Must reproduce the old scheme exactly:
    ``"taxdome:" + sha256(os.path.join(dirpath, filename))`` over the same walk/root."""
    return "taxdome:" + hashlib.sha256(absolute_source_path.encode("utf-8")).hexdigest()


# Office/OS temporary and system files: skipped (counted as ignored, never errors).
def _is_ignored_file(filename: str) -> bool:
    low = filename.lower()
    return (filename.startswith("~$") or low.endswith(".tmp")
            or low in ("thumbs.db", "desktop.ini"))


def _content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _created_ts(stat) -> float | None:
    return getattr(stat, "st_birthtime", None) or stat.st_ctime


_UNSAFE_COMPONENT = re.compile(r'[<>:"|?*\x00-\x1f]')


def sanitize_relative_path(relative_path: str) -> PurePosixPath:
    """Return a safe, destination-relative path, or raise ValueError on any traversal attempt.

    Rejects absolute paths, drive letters, and ``..`` segments; strips characters illegal on Windows
    filesystems. The result is always strictly beneath the destination root when joined."""
    raw = (relative_path or "").replace("\\", "/")
    if raw.startswith("/"):
        raise ValueError(f"absolute path is not a safe relative path: {relative_path!r}")
    parts: list[str] = []
    for segment in raw.split("/"):
        segment = segment.strip()
        if segment in ("", "."):
            continue
        # Reject traversal / absolute / drive-qualified segments. A leading "~" is a LEGITIMATE
        # filename character (e.g. "~budget.xlsx") and is allowed — Office lock files ("~$…") are
        # handled separately as ignored temp files, not here.
        if segment == ".." or ":" in segment or segment.startswith("/"):
            raise ValueError(f"unsafe path segment {segment!r} in {relative_path!r}")
        parts.append(_UNSAFE_COMPONENT.sub("_", segment).rstrip(". "))
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError(f"empty path after sanitization: {relative_path!r}")
    return PurePosixPath(*parts)


def _destination_path(destination_root: Path, safe_relative: PurePosixPath) -> Path:
    """Resolve the absolute destination path and guarantee it stays within the destination root."""
    dest = (destination_root / Path(*safe_relative.parts)).resolve()
    root = destination_root.resolve()
    if root != dest and root not in dest.parents:
        raise ValueError(f"path escapes destination root: {safe_relative}")
    return dest


def _copy_verified(source_path: Path, destination_abs: Path) -> tuple[str, int]:
    """Copy source -> destination safely: stream into a temp file in the destination directory, verify
    its size and SHA-256, then atomically replace the prior file. Never exposes a partial file; on any
    failure the temp file is removed and the exception propagates."""
    destination_abs.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(destination_abs.parent), prefix=".sync-", suffix=".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as out, source_path.open("rb") as src:
            for chunk in iter(lambda: src.read(_CHUNK), b""):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            out.flush()
            os.fsync(out.fileno())
        if _content_sha256(Path(tmp)) != digest.hexdigest() or os.path.getsize(tmp) != size:
            raise OSError(f"verification failed writing {destination_abs}")
        os.replace(tmp, str(destination_abs))       # atomic within the same filesystem
        return digest.hexdigest(), size
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
_NAME_DROP = {"family", "trust", "llc", "inc", "the", "and", "household",
              "jr", "sr", "ii", "iii", "iv"}
# Separators that join two people in a TaxDome folder name, e.g. "Michael and Debra White".
_JOINT_SPLIT_RE = re.compile(r"\s+and\s+|\s*&\s*|\s*\+\s*", re.IGNORECASE)


def _name_key(name: str | None) -> str:
    """Order-insensitive normalized name key so 'Smith, John' matches 'John Smith'. Only exact token
    sets match, which keeps auto-linking conservative (never a weak/partial match).

    SINGLE-LETTER TOKENS ARE DROPPED. A middle initial is a rendering detail of whichever system
    wrote the folder, not identity: SharePoint files 'CASHMAN, KIMBERLY S' while the CRM holds
    'Cashman, Kimberly', and keying on the initial made those two different people. Dropping it
    cannot introduce a WRONG link — it can only merge two keys, and every caller here requires a
    UNIQUE match, so a genuine 'John A Smith' / 'John B Smith' pair collapses to one key with two
    candidates and fails closed exactly as an ambiguous name already does.

    A name that is ONLY initials keeps them, because dropping every token would produce the empty
    key, and the empty key matches nothing (callers treat it as "no identity")."""
    if not name:
        return ""
    tokens = [t for t in _NAME_TOKEN_RE.findall(name.lower()) if t not in _NAME_DROP]
    substantive = [t for t in tokens if len(t) > 1]
    return " ".join(sorted(substantive or tokens))


def _tokens(text: str) -> list[str]:
    """Name tokens, minus noise words and single-letter initials — the same rule as ``_name_key``."""
    toks = [t for t in _NAME_TOKEN_RE.findall((text or "").lower()) if t not in _NAME_DROP]
    return [t for t in toks if len(t) > 1] or toks


def _shared_surname(parts: list[str]) -> str | None:
    """The surname both halves of a joint folder share, or None.

    Two conventions occur in the wild and the surname sits at opposite ends of each:

        "Michael and Debra White"        -> surname trails the LAST fragment
        "Philips, Betty & Bill"          -> surname LEADS the first, before the comma
        "STOVALL, JEFFERY W & PEGGY S"   -> same, with initials

    A comma in the first fragment is the reliable marker of the second form, so it is checked
    first; otherwise the original trailing-surname rule applies unchanged.
    """
    if "," in parts[0]:
        lead = _tokens(parts[0].split(",", 1)[0])
        if lead:
            return lead[-1]
    last = _tokens(parts[-1])
    return last[-1] if len(last) >= 2 else None


def _folder_person_keys(folder_name: str) -> list[str]:
    """Normalized name key(s) a folder refers to. A joint folder is split on 'and'/'&'/'+', and a bare
    first name inherits the shared surname, so 'Michael and Debra White' and 'Philips, Betty & Bill'
    both yield one key per spouse. A single-name folder yields one key.

    Initials are dropped here for the same reason ``_name_key`` drops them: 'STOVALL, JEFFERY W &
    PEGGY S' names the same two people as 'Jeffery Stovall' and 'Peggy Stovall', and keying on the
    initial made the joint folder resolve to nobody."""
    parts = [p.strip() for p in _JOINT_SPLIT_RE.split(folder_name or "") if p.strip()]
    if len(parts) <= 1:
        key = _name_key(folder_name)
        return [key] if key else []
    surname = _shared_surname(parts)
    keys = []
    for part in parts:
        tokens = _tokens(part)
        if not tokens:
            continue
        if len(tokens) == 1 and surname and tokens[0] != surname:
            tokens = [*tokens, surname]                 # bare first name -> add the shared surname
        keys.append(" ".join(sorted(tokens)))
    return keys


def resolve_folder(conn, folder_name: str) -> tuple[int | None, int | None]:
    """Resolve a TaxDome folder to (household_id, person_id) against the canonical people.

    - Single-person folder with one unique name match -> (None, person_id).
    - Joint folder whose matched people share exactly one household -> (household_id, None), so both
      spouses see the household's documents.
    - Anything ambiguous (no match, several matches for a name, or matched people without one common
      household) -> (None, None), left for human review. Never a weak/partial match."""
    keys = _folder_person_keys(folder_name)
    if not keys:
        return (None, None)
    people = _database().people
    rows = conn.execute(select(people.c.id, people.c.full_name,
                               people.c.household_id)).mappings().all()
    matched_person_ids: list[int] = []
    households: set[int] = set()
    for key in keys:
        matches = [(r["id"], r["household_id"]) for r in rows if _name_key(r["full_name"]) == key]
        if len(matches) == 1:                          # unique match for this name only
            pid, hh = matches[0]
            matched_person_ids.append(pid)
            if hh is not None:
                households.add(hh)
    unique_people = set(matched_person_ids)
    if not unique_people:
        return (None, None)
    if len(keys) == 1 and len(unique_people) == 1:
        return (None, matched_person_ids[0])           # single-person folder -> person link
    if len(households) == 1:
        return (households.pop(), None)                # joint folder -> shared household
    if len(unique_people) == 1:
        return (None, matched_person_ids[0])           # only one distinct person actually matched
    return (None, None)                                # ambiguous -> review


def _autolink_person_id(conn, folder_name: str) -> int | None:
    """Backward-compatible single-person resolver (unique exact name match only)."""
    _hh, pid = resolve_folder(conn, folder_name)
    return pid


def suggest_people(conn, folder_name: str, *, limit: int = 5) -> list[dict]:
    """Candidate canonical people for an unresolved folder (SUGGESTION only, never auto-applied)."""
    db = _database()
    key = _name_key(folder_name)
    tokens = _NAME_TOKEN_RE.findall((folder_name or "").lower())
    rows = conn.execute(
        select(db.people.c.id, db.people.c.full_name, db.people.c.primary_email,
               db.people.c.household_id)).mappings().all()
    exact, loose = [], []
    for r in rows:
        pk = _name_key(r["full_name"])
        if pk and pk == key:
            exact.append({**dict(r), "reason": "exact name match", "confidence": "high"})
        elif tokens and any(t in (r["full_name"] or "").lower() for t in tokens if len(t) > 2):
            loose.append({**dict(r), "reason": "partial name match", "confidence": "low"})
    return (exact + loose)[:limit]


# --- sync --------------------------------------------------------------------

def _new_summary(source_root, destination_root, scan_id, started):
    return {
        "source_root": str(source_root), "destination_root": str(destination_root),
        "scan_id": scan_id, "started_at": started.isoformat(), "completed_at": None,
        "folders_examined": 0, "files_examined": 0, "copied": 0, "updated": 0, "skipped": 0,
        "bytes_copied": 0, "missing": 0, "purged": 0, "ignored": 0, "errors": [],
        "legacy_rows_to_upgrade": 0, "legacy_rows_upgraded": 0, "reconciled": 0,
        "reconcile_skipped_has_dependents": 0, "reconcile_skipped_details": [],
        "merge_retired_preserved": 0, "merge_retired_details": [],
        "folders_linked": 0, "folders_unresolved": 0, "status": "started", "dry_run": False,
    }


def _print_progress(summary: dict) -> None:
    print(f"  … folders={summary['folders_examined']} files={summary['files_examined']} "
          f"copied={summary['copied']} updated={summary['updated']} skipped={summary['skipped']} "
          f"ignored={summary['ignored']} bytes={summary['bytes_copied']:,} "
          f"errors={len(summary['errors'])}", flush=True)


def sync(source_root: str | os.PathLike | None = None,
         destination_root: str | os.PathLike | None = None, *,
         dry_run: bool = False, purge_missing: bool = False, actor_user_id: int | None = None,
         progress_interval: int = DEFAULT_PROGRESS_INTERVAL, progress=_print_progress) -> dict:
    """Run a one-way TaxDome Drive -> Client360 local sync. Returns a summary dict (see module docs)."""
    db = _database()
    source = Path(source_root or DEFAULT_SOURCE_ROOT)
    destination = Path(destination_root or DEFAULT_DESTINATION_ROOT)
    started = datetime.now(UTC)

    # A dry run makes NO database changes at all — not even a job-ledger row.
    scan_id = None
    if not dry_run:
        with db.engine.begin() as conn:
            scan_id = conn.execute(db.import_jobs.insert().values(
                source_system=SOURCE_SYSTEM, source_file=str(source),
                status="started").returning(db.import_jobs.c.id)).scalar_one()

    summary = _new_summary(source, destination, scan_id, started)
    summary["dry_run"] = dry_run
    seen_keys: set[str] = set()
    folders_seen: set[str] = set()
    interrupted = False

    try:
        if not source.exists():
            summary["errors"].append(f"source root not found: {source}")
        else:
            if not dry_run:
                destination.mkdir(parents=True, exist_ok=True)
            for entry in sorted(p for p in source.iterdir() if p.is_dir()):
                folder_name = entry.name
                folders_seen.add(folder_name)
                summary["folders_examined"] += 1
                for dirpath, _dirs, filenames in os.walk(entry):
                    for filename in sorted(filenames):
                        if _is_ignored_file(filename):
                            summary["ignored"] += 1        # temp/system file, not an error
                            continue
                        abs_path = os.path.join(dirpath, filename)
                        summary["files_examined"] += 1
                        try:
                            _sync_one_file(db, source, destination, folder_name, abs_path, filename,
                                           scan_id, dry_run, seen_keys, summary)
                        except Exception as exc:          # noqa: BLE001 — record & continue per spec
                            summary["errors"].append(f"{abs_path}: {exc}")
                        if progress and summary["files_examined"] % max(progress_interval, 1) == 0:
                            progress(summary)

        # Auto-link folders (skip in dry-run — it is a DB write).
        if not dry_run:
            _link_folders(db, folders_seen)

        _handle_missing(db, seen_keys, scan_id, dry_run, purge_missing, summary)
    except KeyboardInterrupt:
        interrupted = True
        summary["errors"].append("interrupted by user (Ctrl+C)")

    if not dry_run:
        _folder_counts(db, summary)
    completed = datetime.now(UTC)
    summary["completed_at"] = completed.isoformat()
    summary["status"] = (
        "interrupted" if interrupted else
        "dry_run" if dry_run else
        "completed_with_errors" if summary["errors"] else "completed")

    if not dry_run:
        with db.engine.begin() as conn:
            conn.execute(db.import_jobs.update().where(db.import_jobs.c.id == scan_id).values(
                status=summary["status"], completed_at=completed,
                rows_read=summary["files_examined"], rows_inserted=summary["copied"],
                rows_updated=summary["updated"], rows_skipped=summary["skipped"],
                error_message=json.dumps(summary)))
    if progress:
        progress(summary)
    if interrupted:
        raise KeyboardInterrupt
    return summary


# --- legacy-key reconciliation safety -----------------------------------------------------------
# Deleting a documents row CASCADES to its OCR, classifications, facts, source references,
# relationships, events and version history, and ABORTS on the NO ACTION referrers. The legacy-key
# reconciliation below therefore refuses to delete a row that anything still references. The FK list
# is read from the LIVE schema rather than hardcoded, so a newly added reference is covered the day
# it is created instead of silently losing data.
_DOCUMENT_REFERENCE_SQL = """
SELECT tc.table_name, kcu.column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND ccu.table_name = 'documents' AND ccu.column_name = 'id'
  AND tc.table_name <> 'documents'
"""
_DOCUMENT_REFERENCES: list[tuple[str, str]] | None = None


def _document_references(conn):
    """Every (table, column) with a real FK to documents.id. Cached for the life of the process."""
    global _DOCUMENT_REFERENCES
    if _DOCUMENT_REFERENCES is None:
        _DOCUMENT_REFERENCES = sorted(
            (r[0], r[1]) for r in conn.execute(sa_text(_DOCUMENT_REFERENCE_SQL)).fetchall())
    return _DOCUMENT_REFERENCES


def _dependent_tables(conn, document_id) -> list[str]:
    """``table.column`` for every reference that still points at this document. Empty = safe to
    delete. One UNION ALL query, so the cost does not grow with the number of referring tables."""
    refs = _document_references(conn)
    if not refs:
        return []
    union = " UNION ALL ".join(
        f"SELECT '{t}.{c}' AS ref FROM {t} WHERE {c} = :did" for t, c in refs)
    return [r[0] for r in conn.execute(sa_text(union), {"did": document_id}).fetchall()]


#: Lifecycle event the merge executor writes on every document it retires
#: (app/services/document_merge_execute._retire). It is the authoritative, already-existing
#: evidence that a soft-deleted row was retired BY A MERGE rather than by a person.
MERGE_RETIREMENT_EVENT = "merged_into_canonical"


def _is_merge_retired(conn, document_id) -> bool:
    """True when this document was soft-deleted BY A MERGE and must never be reactivated.

    Deliberately narrow. It is not "the row is deleted" - an administrative or user deletion is a
    different lifecycle decision and is left exactly as it behaves today. The distinguishing
    evidence already exists and needs no new column: the merge executor stamps a
    ``merged_into_canonical`` document_events row on each document it retires, inside the same
    transaction as the retirement, and the hash-chained audit records the same fact."""
    return bool(conn.execute(sa_text(
        "SELECT 1 FROM documents d WHERE d.id = :did AND d.status = 'deleted'"
        " AND EXISTS (SELECT 1 FROM document_events e WHERE e.document_id = d.id"
        "             AND e.event_type = :ev) LIMIT 1"),
        {"did": document_id, "ev": MERGE_RETIREMENT_EVENT}).scalar())


def _resolve_identity(rows, key, legacy_key):
    """Resolve the candidate rows for one source file into (canonical, duplicates, had_legacy).

    ``rows`` are the documents whose stored_name is the new relative-path key or the legacy
    absolute-path key. When both a new-style and a legacy row exist for the same file, the
    person-associated row is preserved as canonical and the other becomes a duplicate to merge away."""
    new_row = next((r for r in rows if r["stored_name"] == key), None)
    legacy_row = next((r for r in rows if r["stored_name"] == legacy_key and r["stored_name"] != key), None)
    if new_row and legacy_row:
        if legacy_row["person_id"] and not new_row["person_id"]:
            return legacy_row, [new_row], True          # preserve the linked legacy row
        return new_row, [legacy_row], True
    if legacy_row:
        return legacy_row, [], True
    if new_row:
        return new_row, [], False
    return None, [], False


def _merge_field(canonical, duplicates, field):
    """Keep the canonical value; fall back to a duplicate's value only where canonical is empty."""
    if canonical[field] is not None:
        return canonical[field]
    for dup in duplicates:
        if dup[field] is not None:
            return dup[field]
    return None


def _sync_one_file(db, source, destination, folder_name, abs_path, filename, scan_id, dry_run,
                   seen_keys, summary) -> None:
    """Sync a single source file. Raises on failure so the caller records it and continues.

    Identity resolution happens FIRST (new relative-path key OR legacy absolute-path key), so legacy
    metadata-only rows are upgraded in place rather than duplicated or reported missing."""
    source_path = Path(abs_path)
    stat = os.stat(source_path)
    source_size = int(stat.st_size)
    source_modified = _iso(stat.st_mtime)
    rel_str = os.path.relpath(abs_path, source)
    safe_rel = sanitize_relative_path(rel_str)             # raises on traversal
    dest_abs = _destination_path(destination, safe_rel)    # raises if it escapes the root
    local_rel = str(safe_rel)
    key = _stored_name(rel_str)
    legacy_key = _legacy_stored_name(abs_path)
    # Track BOTH keys as seen so missing-reconciliation (which runs later) never flags a legacy row
    # whose source still exists.
    seen_keys.add(key)
    seen_keys.add(legacy_key)

    with db.engine.connect() as conn:
        rows = conn.execute(
            select(db.documents.c.id, db.documents.c.stored_name, db.documents.c.sha256,
                   db.documents.c.tags, db.documents.c.person_id, db.documents.c.household_id,
                   db.documents.c.category, db.documents.c.classification, db.documents.c.size_bytes,
                   db.documents.c.status)
            .where(db.documents.c.stored_name.in_([key, legacy_key]))).mappings().all()
    canonical, duplicates, had_legacy = _resolve_identity(rows, key, legacy_key)

    # A document the merge executor retired must never be resurrected by a routine sync. The row is
    # still MATCHED above, so nothing here creates a replacement duplicate - the sync simply
    # recognises the merged row and leaves its lifecycle exactly as the merge left it: status
    # 'deleted', deleted_at intact, merged_into_canonical history intact. Its content now lives on
    # the surviving canonical document, which this sync keeps up to date under its own key.
    if canonical is not None and canonical["status"] == "deleted":
        with db.engine.connect() as probe:
            if _is_merge_retired(probe, canonical["id"]):
                summary["merge_retired_preserved"] += 1
                summary["merge_retired_details"].append(
                    {"document_id": canonical["id"], "stored_name": canonical["stored_name"],
                     "source_relative_path": rel_str})
                return

    # Decide the copy action using the size+mtime fast path; hash only when needed.
    action = "new"
    if canonical is not None:
        tags = canonical["tags"] or {}
        local_ok = dest_abs.exists()
        if local_ok and tags.get("source_size") == source_size \
                and tags.get("source_modified") == source_modified:
            action = "skip"
        else:
            source_hash = _content_sha256(source_path)
            action = "skip" if (local_ok and source_hash == canonical["sha256"]) else "changed"

    if dry_run:
        if canonical is None:
            summary["copied"] += 1
            summary["bytes_copied"] += source_size
        elif action == "skip":
            summary["skipped"] += 1
        else:
            summary["updated"] += 1
            summary["bytes_copied"] += source_size
        if had_legacy:
            summary["legacy_rows_to_upgrade"] += 1
        # Preview the SAME safety decision the real run makes, so an operator sees what would be
        # skipped before committing to a sync. Read-only.
        with db.engine.connect() as probe:
            blocked_preview = [d for d in duplicates if _dependent_tables(probe, d["id"])]
        if blocked_preview:
            summary["reconcile_skipped_has_dependents"] += len(blocked_preview)
            summary["reconcile_skipped_details"].extend(
                {"document_id": d["id"], "stored_name": d["stored_name"],
                 "source_relative_path": rel_str, "dry_run": True} for d in blocked_preview)
        summary["reconciled"] += len(duplicates) - len(blocked_preview)
        return

    # Copy the bytes when needed; on a genuine skip, reuse the verified local copy's hash/size.
    if action in ("new", "changed"):
        sha, size = _copy_verified(source_path, dest_abs)   # temp + verify + atomic replace
    else:
        sha, size = canonical["sha256"], canonical["size_bytes"]

    base_tags = dict((canonical["tags"] if canonical else None) or {})
    base_tags.update({
        "source_system": SOURCE_SYSTEM, "source_root": str(source), "source_path": abs_path,
        "source_relative_path": rel_str, "taxdome_folder": folder_name,
        "local_relative_path": local_rel, "source_created": _iso(_created_ts(stat)),
        "source_modified": source_modified, "source_size": source_size,
        "available_from_source": True, "retained_locally": True, "last_scan_id": scan_id,
        "last_synced_at": datetime.now(UTC).isoformat(), "sync_version": SYNC_VERSION,
    })
    if duplicates:
        base_tags["reconciled_from"] = sorted(d["stored_name"] for d in duplicates)
    values = {
        "storage_provider": STORAGE_PROVIDER, "storage_uri": str(dest_abs), "storage_path": local_rel,
        "original_name": filename, "content_type": mimetypes.guess_type(filename)[0],
        "size_bytes": size, "sha256": sha, "tags": base_tags,
        "status": "active", "archived": False, "archived_at": None, "deleted_at": None,
        "uploaded_by": "TaxDome Drive Sync", "updated_at": datetime.now(UTC),
    }
    with db.engine.begin() as conn:
        # Remove duplicate rows first so converting the canonical row to the new key cannot collide on
        # the unique stored_name. Only DB rows are removed here — never the retained local file.
        # A duplicate that anything still references is NEVER deleted: the cascade would take its OCR,
        # classifications, facts and source references with it, and the NO ACTION referrers would abort
        # the whole sync. Such a row is left exactly as it is and reported instead.
        removable, blocked = [], []
        for dup in duplicates:
            deps = _dependent_tables(conn, dup["id"])
            (blocked if deps else removable).append((dup, deps))
        if blocked:
            summary["reconcile_skipped_has_dependents"] += len(blocked)
            summary["reconcile_skipped_details"].extend(
                {"document_id": dup["id"], "stored_name": dup["stored_name"],
                 "dependents": deps, "source_relative_path": rel_str}
                for dup, deps in blocked)
        # A blocked duplicate that HOLDS the target key cannot be reconciled at all: converting the
        # canonical row to that key would violate the unique stored_name. Leave both rows untouched
        # for this file rather than delete data or raise — the next run retries.
        if any(dup["stored_name"] == key for dup, _ in blocked):
            return
        for dup, _deps in removable:
            conn.execute(db.documents.delete().where(db.documents.c.id == dup["id"]))
        if canonical is None:
            conn.execute(db.documents.insert().values(
                stored_name=key, category=infer_category(filename, rel_str), **values))
            summary["copied"] += 1
        else:
            # Upgrade/reconcile in place: convert to the new stable key and preserve useful metadata.
            conn.execute(db.documents.update().where(db.documents.c.id == canonical["id"]).values(
                stored_name=key,
                person_id=_merge_field(canonical, duplicates, "person_id"),
                household_id=_merge_field(canonical, duplicates, "household_id"),
                category=_merge_field(canonical, duplicates, "category") or infer_category(filename, rel_str),
                classification=_merge_field(canonical, duplicates, "classification"),
                **values))
            summary["updated" if action != "skip" else "skipped"] += 1
        if had_legacy:
            summary["legacy_rows_upgraded"] += 1
        summary["reconciled"] += len(removable)
    if action in ("new", "changed"):
        summary["bytes_copied"] += size


def _apply_folder_link(conn, d, folder_name, household_id, person_id) -> int:
    """Fill household_id / person_id on a folder's documents where they are currently NULL (never
    overwriting an existing manual/auto link). Returns the number of rows updated."""
    base = and_(taxdome_filter(d), d.c.tags["taxdome_folder"].astext == folder_name)
    updated = 0
    if person_id is not None:
        updated += conn.execute(d.update().where(and_(base, d.c.person_id.is_(None)))
                                .values(person_id=person_id)).rowcount
    if household_id is not None:
        updated += conn.execute(d.update().where(and_(base, d.c.household_id.is_(None)))
                                .values(household_id=household_id)).rowcount
    return updated


def _link_folders(db, folders_seen) -> None:
    """Resolve each folder to a household and/or person and fill in the links (NULLs only)."""
    with db.engine.begin() as conn:
        for folder_name in folders_seen:
            hh, pid = resolve_folder(conn, folder_name)
            if hh is not None or pid is not None:
                _apply_folder_link(conn, db.documents, folder_name, hh, pid)


def _handle_missing(db, seen_keys, scan_id, dry_run, purge_missing, summary) -> None:
    """Reconcile documents whose source file was not seen this run. Default: retain the local copy and
    flag it unavailable. With purge_missing: delete the local copy and archive the row."""
    with db.engine.connect() as conn:
        rows = conn.execute(
            select(db.documents.c.id, db.documents.c.stored_name, db.documents.c.tags,
                   db.documents.c.storage_uri)
            .where(taxdome_filter(db.documents))).mappings().all()
    for r in rows:
        if r["stored_name"] in seen_keys:
            continue
        tags = r["tags"] or {}
        if not purge_missing and tags.get("available_from_source") is False:
            continue                                       # already flagged missing on a prior run
        summary["missing"] += 1
        if dry_run:
            if purge_missing:
                summary["purged"] += 1
            continue
        if purge_missing:
            uri = r["storage_uri"]
            if uri:
                try:
                    os.unlink(uri)
                except OSError:
                    pass
            new_tags = {**tags, "available_from_source": False, "retained_locally": False,
                        "source_status": "purged", "last_scan_id": scan_id}
            with db.engine.begin() as conn:
                conn.execute(db.documents.update().where(db.documents.c.id == r["id"]).values(
                    tags=new_tags, status="archived", archived=True,
                    archived_at=datetime.now(UTC), deleted_at=datetime.now(UTC)))
            summary["purged"] += 1
        else:
            new_tags = {**tags, "available_from_source": False, "retained_locally": True,
                        "source_status": "missing", "last_scan_id": scan_id}
            with db.engine.begin() as conn:
                # Retain the local copy: status stays active, archived stays false.
                conn.execute(db.documents.update().where(db.documents.c.id == r["id"]).values(
                    tags=new_tags, status="active", archived=False))


def _folder_counts(db, summary) -> None:
    linked_expr = db.documents.c.person_id.isnot(None) | db.documents.c.household_id.isnot(None)
    with db.engine.connect() as conn:
        linked = conn.execute(
            select(func.count(func.distinct(db.documents.c.tags["taxdome_folder"].astext)))
            .where(and_(taxdome_filter(db.documents), linked_expr))).scalar() or 0
        total = conn.execute(
            select(func.count(func.distinct(db.documents.c.tags["taxdome_folder"].astext)))
            .where(taxdome_filter(db.documents))).scalar() or 0
    summary["folders_linked"] = linked
    summary["folders_unresolved"] = max(total - linked, 0)


def repair_person_links(*, dry_run: bool = False, progress=None) -> dict:
    """Relink ALREADY-IMPORTED TaxDome documents to the canonical household/person using the stored
    ``tags->>'taxdome_folder'`` metadata — without re-copying files, inserting rows, or touching
    storage_path/hashes/OCR metadata/version history. Fills household_id/person_id where NULL only, so
    it is idempotent and preserves any manual links. Returns a summary."""
    db = _database()
    d = db.documents
    summary = {"folders": 0, "linked_household": 0, "linked_person": 0, "unresolved": 0,
               "documents_updated": 0, "dry_run": dry_run}
    with db.engine.connect() as conn:
        folders = [r[0] for r in conn.execute(
            select(d.c.tags["taxdome_folder"].astext).where(taxdome_filter(d)).distinct()).all() if r[0]]
    for folder_name in sorted(folders):
        summary["folders"] += 1
        with db.engine.begin() as conn:
            hh, pid = resolve_folder(conn, folder_name)
            if hh is None and pid is None:
                summary["unresolved"] += 1
                continue
            if pid is not None:
                summary["linked_person"] += 1
            if hh is not None:
                summary["linked_household"] += 1
            if dry_run:
                base = and_(taxdome_filter(d), d.c.tags["taxdome_folder"].astext == folder_name)
                if pid is not None:
                    summary["documents_updated"] += conn.execute(
                        select(func.count()).select_from(d)
                        .where(and_(base, d.c.person_id.is_(None)))).scalar() or 0
                if hh is not None:
                    summary["documents_updated"] += conn.execute(
                        select(func.count()).select_from(d)
                        .where(and_(base, d.c.household_id.is_(None)))).scalar() or 0
            else:
                summary["documents_updated"] += _apply_folder_link(conn, d, folder_name, hh, pid)
        if progress:
            progress(summary)
    return summary


# Backward-compatible alias: the previous entry point was ``scan``; keep it working.
def scan(root=None, *, actor_user_id=None):
    return sync(root, actor_user_id=actor_user_id)


def latest_scan():
    """The most recent TaxDome sync summary (from import_jobs), or None."""
    db = _database()
    with db.engine.connect() as conn:
        row = conn.execute(
            select(db.import_jobs).where(db.import_jobs.c.source_system == SOURCE_SYSTEM)
            .order_by(db.import_jobs.c.id.desc()).limit(1)).mappings().first()
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
    """Search synchronized TaxDome documents (filename + folder) — reused by the demo UI."""
    db = _database()
    like = f"%{term.strip()}%"
    return conn.execute(
        select(db.documents.c.id, db.documents.c.original_name, db.documents.c.person_id,
               db.documents.c.tags["taxdome_folder"].astext.label("folder"),
               db.documents.c.category, db.documents.c.size_bytes, db.documents.c.status)
        .where(and_(taxdome_filter(db.documents),
                    or_(db.documents.c.original_name.ilike(like),
                        db.documents.c.tags["taxdome_folder"].astext.ilike(like))))
        .order_by(db.documents.c.original_name).limit(limit)).mappings().all()


# --- CLI ---------------------------------------------------------------------

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m app.importers.taxdome_drive",
        description="One-way TaxDome Drive -> Client360 local document synchronization.")
    parser.add_argument("--source-root", default=None,
                        help=f"TaxDome Drive root (default {DEFAULT_SOURCE_ROOT} / $TAXDOME_DRIVE_ROOT)")
    parser.add_argument("--destination-root", default=None,
                        help="Local document root (default $CLIENT360_TAXDOME_DOCUMENT_ROOT)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change; make no file or database changes.")
    parser.add_argument("--purge-missing", action="store_true",
                        help="DELETE local copies whose source file has disappeared (off by default).")
    parser.add_argument("--repair-links", action="store_true",
                        help="Relink already-imported documents to household/person by folder "
                             "metadata (no file copying). Honors --dry-run.")
    parser.add_argument("--progress-interval", type=int, default=DEFAULT_PROGRESS_INTERVAL,
                        help="Print progress every N files examined.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.repair_links:
        summary = repair_person_links(dry_run=args.dry_run)
        label = "DRY RUN — no changes made" if args.dry_run else "link repair complete"
        print(f"TaxDome Drive {label}.")
        for key in ("folders", "linked_household", "linked_person", "unresolved", "documents_updated"):
            print(f"  {key}: {summary[key]}")
        return 0
    try:
        summary = sync(args.source_root, args.destination_root, dry_run=args.dry_run,
                       purge_missing=args.purge_missing, progress_interval=args.progress_interval)
    except KeyboardInterrupt:
        print("\nTaxDome sync interrupted — recorded as interrupted.", flush=True)
        return 130
    label = "DRY RUN — no changes made" if args.dry_run else "sync complete"
    print(f"TaxDome Drive {label}.")
    legacy_key = "legacy_rows_to_upgrade" if args.dry_run else "legacy_rows_upgraded"
    for key in ("folders_examined", "files_examined", "ignored", "copied", "updated", "skipped",
                "bytes_copied", "missing", "purged", "reconciled", legacy_key,
                "folders_linked", "folders_unresolved", "status"):
        print(f"  {key}: {summary[key]}")
    if summary["errors"]:
        print(f"  errors ({len(summary['errors'])}):")
        for e in summary["errors"][:20]:
            print(f"    - {e}")
    return 1 if summary["status"] == "completed_with_errors" and not args.dry_run else 0


if __name__ == "__main__":
    raise SystemExit(main())
