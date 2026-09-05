"""Drake Tax — a first-class SOURCE PROVIDER for Client360 (not a separate application).

Drake documents become CANONICAL documents (ADR-072): each discovered file is hashed (SHA-256) and
resolved against the existing corpus — an identical document already present from another source (e.g.
TaxDome) is REUSED and gains a Drake source reference (provenance preserved); a new document creates the
canonical row plus its first source reference. Nothing here is a parallel document system — canonical
storage, dedup, and ownership are the platform's, and Drake is one source among many.

INGESTION NEVER ASSIGNS AN OWNER
--------------------------------
This importer registers Drake documents with ``person_id`` and ``household_id`` NULL, always. It used
to resolve the top-level folder name through the TaxDome ``resolve_folder`` matcher and write the
result, which put the WEAKEST identity signal available — a folder name matched against
``people.full_name`` — ahead of every stronger one. Because every ownership write in the platform
requires all three owner columns to be NULL (``households.resolve_document_ownership`` re-checks that
inside its UPDATE, and ``document_owner_proposal`` refuses an already-owned document), whichever
mechanism writes FIRST wins permanently. Assigning at ingestion therefore did not merely rank the
folder name first; it locked out Drake's own SSN/EIN identifier-hash resolution
(``app.services.drake_document_owner``) for good.

Leaving ownership NULL is what lets the stronger path run: ``resolve_or_create_canonical`` invokes the
analysis hook in the same transaction, ``propose_drake_document_owner`` evaluates the document's Drake
return identity, and the result is persisted as a NON-authoritative proposal for a human to confirm at
``/admin/documents/unassigned`` — a surface that already states this expectation ("Drake documents are
surfaced individually because their canonical registration intentionally preserved them as unassigned
rather than guessing an owner"). Nothing is lost: the folder name is still recorded in ``tags`` as
provenance and remains available to every downstream resolver as CORROBORATION.

Verified read-only against production before this change: the importer had never run there (no
``uploaded_by='Drake Sync'`` row, no ``tags.sync_version``, no Drake ``import_jobs`` row), so removing
the write corrects no existing document and creates no review backlog. The 794 Drake documents present
were loaded by a separate out-of-band migration and are untouched by this module.

Honest boundary: this discovers Drake artifacts from a Drake EXPORT directory (Drake's live database
format is environment-specific). Files are classified by Drake naming conventions (federal/state return
PDFs, IRS/state acknowledgements, organizers, engagement letters, workpapers, K-1s, depreciation/asset
reports, XML exports, supporting documents). Structured e-file STATUS / acknowledgement ingestion into
the authoritative tax tables is the next roadmap step (3B) — this foundation attaches Drake documents +
provenance, which the existing Client Workspace Documents/Tax/Timeline tabs already surface.

CLI::

    python -m app.importers.drake
    python -m app.importers.drake --dry-run
    python -m app.importers.drake --source-root D:\\DrakeExport --destination-root C:\\Client360\\Data\\Documents\\Drake
    python -m app.importers.drake --purge-missing        # explicit; never automatic
"""
from __future__ import annotations

import argparse
import os
import re
from collections import namedtuple
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, and_, create_engine, select

from app.importers.taxdome_drive import (
    _copy_verified,
    _created_ts,
    _destination_path,
    _is_ignored_file,
    _iso,
    infer_category,
    sanitize_relative_path,
)
from app.services.storage_paths import document_root as _document_root

SOURCE_SYSTEM = "Drake"
STORAGE_PROVIDER = "Client360 Local"
SYNC_VERSION = 1
DEFAULT_SOURCE_ROOT = os.getenv("DRAKE_EXPORT_ROOT", os.getenv("DRAKE_DRIVE_ROOT", "D:\\DrakeExport"))
# CLIENT360_DRAKE_DOCUMENT_ROOT still wins; else <CLIENT360_DATA_ROOT>\Documents\Drake; else legacy default.
DEFAULT_DESTINATION_ROOT = _document_root("Drake", "CLIENT360_DRAKE_DOCUMENT_ROOT")
DEFAULT_PROGRESS_INTERVAL = int(os.getenv("DRAKE_SYNC_PROGRESS_INTERVAL", "100") or "100")

#: Drake Document Manager names each client's folder with an 8-character hexadecimal key.
_DRAKE_CLIENT_ID_RE = re.compile(r"\A[0-9A-Fa-f]{8}\Z")
#: The fixed folder DDM places directly inside each client folder. Case varies across installs.
_DDM_DOCUMENTS_DIR = "documents"


class DrakeClientIdConflict(RuntimeError):
    """A re-sync derived a different native client id than the one already recorded."""


def drake_client_id(source_relative_path: str) -> str | None:
    """The native Drake client id for a document, or ``None`` when the path does not prove one.

    Drake stores documents in its own installation tree, not in a purpose-built export::

        C:\\DRAKE21\\DT\\<bucket 0-9>\\<CLIENT_ID>\\Documents\\<filename>

    ``CLIENT_ID`` is 8 hexadecimal characters, is created by Drake before Client360 ever sees the
    file, and survives Drake's year rollover — measured against production, 48 of 493 client ids
    appear under two or three different ``DRAKE<YY>`` installs. It is therefore the authoritative
    document-side client identifier, and it is the only one available: the return-side exports carry
    no client id at all (``CLIENT.CSV`` has 123 columns and none of them is one).

    STRUCTURAL, NOT POSITIONAL. A fixed segment index would break the moment the configured source
    root changed from ``C:\\DRAKE21`` to ``C:\\DRAKE21\\DT``, and a bare "first 8-hex segment" scan
    would happily accept a *filename* like ``DEADBEEF.pdf`` or an unrelated nested directory. The id
    is identified by its RELATIONSHIP instead: it is the directory whose immediate child is the DDM
    ``Documents`` folder. That anchor is what makes the rule root-agnostic and fail-closed.

    Fails closed and returns ``None`` when the shape is not proven — no client folder, or (defensively)
    more than one distinct candidate. A caller must leave ``source_external_id`` NULL in that case
    rather than guess; a document with no identity is recoverable, a document with the WRONG identity
    is not.

    Replayed read-only against all 795 existing Drake source paths: 795 derived, 795 matching the
    id recorded out-of-band, 0 mismatches — for both plausible source roots.
    """
    parts = [p for p in re.split(r"[\\/]+", source_relative_path or "") if p not in ("", ".")]
    if len(parts) < 3:
        # Need at least <CLIENT_ID>/Documents/<file>.
        return None

    # ``len(parts) - 1`` excludes the final segment: that is the file, never the client folder.
    candidates = {
        parts[i].upper()
        for i in range(len(parts) - 1)
        if _DRAKE_CLIENT_ID_RE.match(parts[i]) and parts[i + 1].lower() == _DDM_DOCUMENTS_DIR
    }
    if len(candidates) != 1:
        return None
    return candidates.pop()


def _resolve_source_external_id(rel_str, existing_ref):
    """The native client id to persist, or ``None``. Raises on a conflicting re-derivation.

    Precedence is derived-over-recorded, because the derivation is reproducible from the path while
    the recorded value came from an out-of-band migration. They may not DISAGREE, though: an existing
    id and a derived id that differ mean the file moved between client folders, or the recorded id was
    wrong. Either way the document's identity is in question, so this refuses rather than silently
    re-pointing a document at a different client.
    """
    derived = drake_client_id(rel_str)
    existing = (existing_ref or {}).get("source_external_id")

    if derived and existing and derived != str(existing).upper():
        raise DrakeClientIdConflict(
            f"native Drake client id conflict for {rel_str!r}: recorded id differs from the id "
            f"derived from the source path; refusing to change document identity"
        )
    return derived or existing


_Database = namedtuple("_Database", "engine documents document_sources")


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


# --- Drake document-type classification (by export naming conventions) -------

def drake_doc_type(filename: str) -> str:
    low = filename.lower()
    if low.endswith(".xml"):
        return "xml_export"
    if "ack" in low or "acknowledg" in low:
        return "state_ack" if "state" in low else "irs_ack"
    if "organizer" in low:
        return "organizer"
    if "engagement" in low:
        return "engagement_letter"
    if "k-1" in low or "k1" in low or "sch k1" in low:
        return "k1"
    if "deprec" in low:
        return "depreciation"
    if "asset" in low:
        return "asset_detail"
    if "workpaper" in low or low.startswith("wp") or "_wp" in low:
        return "workpaper"
    if low.endswith(".pdf") and "state" in low:
        return "state_return"
    if low.endswith(".pdf") and any(k in low for k in ("1040", "1120", "1065", "federal", "return")):
        return "federal_return"
    return "supporting"


def _tax_year(filename: str, rel_path: str):
    import re
    m = re.search(r"(20\d{2})", f"{filename} {rel_path}")
    return m.group(1) if m else None


def _drake_stored_name(source_relative_path: str) -> str:
    import hashlib
    from pathlib import PurePosixPath
    norm = str(PurePosixPath(source_relative_path.replace("\\", "/"))).lower()
    return "drake:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()


# --- sync --------------------------------------------------------------------

def _new_summary(source, dest, scan_id, started):
    return {"source_root": str(source), "destination_root": str(dest), "scan_id": scan_id,
            "started_at": started.isoformat(), "completed_at": None,
            "folders_examined": 0, "files_examined": 0, "ignored": 0,
            "canonical_created": 0, "reused_canonical": 0, "source_refs_added": 0,
            "skipped": 0, "missing": 0, "purged": 0, "bytes_copied": 0, "errors": [],
            # Replaces the former linked_household/linked_person counters: ingestion no longer
            # assigns an owner, so every document it registers is left for the proposal path.
            "left_unassigned": 0,
            # Native Drake client id capture. ``client_id_missing`` is surfaced rather than silently
            # tolerated: a document ingested without one cannot later be resolved by client identity,
            # so a non-zero count is a signal that the source root or the DDM layout is not what this
            # importer expects. A conflict is recorded in ``errors`` and the file is skipped.
            "client_id_captured": 0, "client_id_missing": 0, "client_id_missing_paths": [],
            "status": "started", "dry_run": False}


def _print_progress(s):
    print(f"  … folders={s['folders_examined']} files={s['files_examined']} "
          f"canonical_created={s['canonical_created']} reused={s['reused_canonical']} "
          f"skipped={s['skipped']} ignored={s['ignored']} errors={len(s['errors'])}", flush=True)


def sync(source_root=None, destination_root=None, *, dry_run=False, purge_missing=False,
         actor_user_id=None, progress_interval=DEFAULT_PROGRESS_INTERVAL, progress=_print_progress):
    """Discover a Drake export tree and integrate it into the canonical document model. Returns a
    summary dict. Read-only w.r.t. the Drake source; idempotent + resumable w.r.t. Client360."""
    from app.services.document_sources import mark_source_unavailable, resolve_or_create_canonical
    db = _database()
    source = Path(source_root or DEFAULT_SOURCE_ROOT)
    destination = Path(destination_root or DEFAULT_DESTINATION_ROOT)
    started = datetime.now(UTC)
    scan_id = None
    summary = _new_summary(source, destination, scan_id, started)
    summary["dry_run"] = dry_run
    seen_uris: set[str] = set()

    if not source.exists():
        summary["errors"].append(f"Drake export root not found: {source}")
    else:
        if not dry_run:
            destination.mkdir(parents=True, exist_ok=True)
        for entry in sorted(p for p in source.iterdir() if p.is_dir()):
            folder_name = entry.name
            summary["folders_examined"] += 1
            for dirpath, _dirs, filenames in os.walk(entry):
                for filename in sorted(filenames):
                    if _is_ignored_file(filename):
                        summary["ignored"] += 1
                        continue
                    abs_path = os.path.join(dirpath, filename)
                    summary["files_examined"] += 1
                    try:
                        _sync_one(db, source, destination, folder_name, abs_path, filename,
                                  dry_run, seen_uris, summary, resolve_or_create_canonical)
                    except Exception as exc:      # noqa: BLE001 — record & continue
                        summary["errors"].append(f"{abs_path}: {exc}")
                    if progress and summary["files_examined"] % max(progress_interval, 1) == 0:
                        progress(summary)

    _handle_missing(db, seen_uris, dry_run, purge_missing, summary, mark_source_unavailable)

    completed = datetime.now(UTC)
    summary["completed_at"] = completed.isoformat()
    summary["status"] = "dry_run" if dry_run else ("completed_with_errors" if summary["errors"]
                                                   else "completed")
    if progress:
        progress(summary)
    return summary


def _sync_one(db, source, destination, folder_name, abs_path, filename, dry_run, seen_uris, summary,
              resolve_or_create_canonical):
    stat = os.stat(abs_path)
    rel_str = os.path.relpath(abs_path, source)
    safe_rel = sanitize_relative_path(rel_str)
    dest_abs = _destination_path(destination, safe_rel)
    seen_uris.add(abs_path)

    # Incremental fast path: an existing Drake source ref with the same size (+ local copy) is skipped.
    with db.engine.connect() as conn:
        existing_ref = conn.execute(
            select(db.document_sources.c.document_id, db.document_sources.c.metadata,
                   db.document_sources.c.source_hash,
                   db.document_sources.c.source_external_id)
            .where(db.document_sources.c.source_system == SOURCE_SYSTEM,
                   db.document_sources.c.source_uri == abs_path)).mappings().first()
    if existing_ref is not None:
        meta = existing_ref["metadata"] or {}
        if dest_abs.exists() and meta.get("size") == int(stat.st_size) \
                and meta.get("mtime") == _iso(stat.st_mtime):
            summary["skipped"] += 1
            return

    if dry_run:
        summary["files_examined"]  # already counted
        # Report what would happen without hashing/copying every file.
        summary["canonical_created" if existing_ref is None else "reused_canonical"] += 1
        summary["bytes_copied"] += int(stat.st_size)
        return

    # Resolve the native client id BEFORE any copy or write. A conflict raises here, so the file is
    # skipped and recorded in summary["errors"] by the caller rather than being ingested under a
    # contested identity.
    source_external_id = _resolve_source_external_id(rel_str, existing_ref)
    if source_external_id:
        summary["client_id_captured"] += 1
    else:
        summary["client_id_missing"] += 1
        if len(summary["client_id_missing_paths"]) < 50:
            summary["client_id_missing_paths"].append(rel_str)

    sha, size = _copy_verified(Path(abs_path), dest_abs)   # canonical local copy + verify
    with db.engine.begin() as conn:
        tags = {
            "source_system": SOURCE_SYSTEM, "drake_doc_type": drake_doc_type(filename),
            "tax_year": _tax_year(filename, rel_str), "source_path": abs_path,
            "source_relative_path": rel_str, "taxdome_folder": folder_name,
            "source_created": _iso(_created_ts(stat)), "source_modified": _iso(stat.st_mtime),
            "sync_version": SYNC_VERSION, "last_synced_at": datetime.now(UTC).isoformat(),
        }
        import mimetypes
        result = resolve_or_create_canonical(
            sha256=sha, original_name=filename, stored_name=_drake_stored_name(rel_str),
            storage_provider=STORAGE_PROVIDER, storage_uri=str(dest_abs), storage_path=str(safe_rel),
            size_bytes=size, content_type=mimetypes.guess_type(filename)[0],
            category=infer_category(filename, rel_str), tags=tags,
            # Deliberately unowned — see "INGESTION NEVER ASSIGNS AN OWNER" above. Passing None also
            # means the hash-hit branch computes an empty fill, so a Drake file that dedupes onto a
            # canonical row from another source cannot write an owner onto that row either.
            person_id=None, household_id=None,
            source_system=SOURCE_SYSTEM, source_uri=abs_path, source_path=rel_str,
            # The native Drake client id — DERIVED from the DDM path for a new document, and falling
            # back to the already-recorded value on a re-sync (add_source_reference upserts on
            # (document_id, source_system, source_uri) and assigns source_external_id
            # unconditionally, so passing None would blank the id the out-of-band migration recorded).
            # ``_resolve_source_external_id`` refuses a derived id that contradicts a recorded one.
            source_external_id=source_external_id,
            conn=conn)
        # Record size/mtime on the source ref for the next incremental run.
        conn.execute(db.document_sources.update().where(and_(
            db.document_sources.c.document_id == result["document_id"],
            db.document_sources.c.source_system == SOURCE_SYSTEM,
            db.document_sources.c.source_uri == abs_path)).values(
            metadata={"size": int(size), "mtime": _iso(stat.st_mtime),
                      "drake_doc_type": tags["drake_doc_type"]}))
    summary["reused_canonical" if result["reused"] else "canonical_created"] += 1
    summary["source_refs_added"] += 1
    summary["left_unassigned"] += 1
    summary["bytes_copied"] += size


def _handle_missing(db, seen_uris, dry_run, purge_missing, summary, mark_source_unavailable):
    """Drake source references not seen this run: mark unavailable (canonical + local copy retained).
    --purge-missing additionally flags them purged; it never deletes a canonical document."""
    with db.engine.connect() as conn:
        rows = conn.execute(
            select(db.document_sources.c.document_id, db.document_sources.c.source_uri,
                   db.document_sources.c.available)
            .where(db.document_sources.c.source_system == SOURCE_SYSTEM)).mappings().all()
    for r in rows:
        if r["source_uri"] in seen_uris or not r["available"]:
            continue
        summary["missing"] += 1
        if dry_run:
            if purge_missing:
                summary["purged"] += 1
            continue
        with db.engine.begin() as conn:
            mark_source_unavailable(conn, r["document_id"], SOURCE_SYSTEM, r["source_uri"])
        if purge_missing:
            summary["purged"] += 1


# --- CLI ---------------------------------------------------------------------

def _parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m app.importers.drake",
                                description="Integrate a Drake export as a canonical source provider.")
    p.add_argument("--source-root", default=None)
    p.add_argument("--destination-root", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--purge-missing", action="store_true")
    p.add_argument("--progress-interval", type=int, default=DEFAULT_PROGRESS_INTERVAL)
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    summary = sync(args.source_root, args.destination_root, dry_run=args.dry_run,
                   purge_missing=args.purge_missing, progress_interval=args.progress_interval)
    label = "DRY RUN — no changes made" if args.dry_run else "Drake integration complete"
    print(f"Drake {label}.")
    for k in ("folders_examined", "files_examined", "ignored", "canonical_created", "reused_canonical",
              "source_refs_added", "skipped", "missing", "purged", "bytes_copied",
              "left_unassigned", "client_id_captured", "client_id_missing", "status"):
        print(f"  {k}: {summary[k]}")
    if summary["client_id_missing"]:
        # Not an error — the document is still ingested — but it can never be resolved by client
        # identity, so it must not pass silently.
        print(f"  documents with NO native Drake client id ({summary['client_id_missing']}):")
        for p in summary["client_id_missing_paths"][:20]:
            print(f"    - {p}")
    if summary["errors"]:
        print(f"  errors ({len(summary['errors'])}):")
        for e in summary["errors"][:20]:
            print(f"    - {e}")
    return 1 if summary["status"] == "completed_with_errors" else 0


if __name__ == "__main__":
    raise SystemExit(main())
