"""Normalized image derivatives over the canonical document model.

This is the ONE seam every image consumer goes through — OCR, the browser preview, ingestion analysis
and any AI image call. A consumer asks for "the image bytes for this document" and gets back a path it
can always use: the original for a JPEG/PNG/GIF/TIFF, and the normalized JPEG rendition for an iPhone
HEIC/HEIF. No caller implements HEIC conversion itself, and no caller has to know which case it is in.

Relationship to the rest of the document model (unchanged by this module):
  * the ORIGINAL is authoritative and immutable — same ``documents`` row, same ``original_name``, same
    ``content_type``, same storage path, same ``sha256``. Downloads and the source-document view keep
    serving it byte for byte;
  * the DERIVATIVE is a separate, content-addressed file recorded in ``document_derivatives`` (one row
    per document + kind) with its own MIME, path and SHA-256, plus the conversion state machine
    (pending -> processing -> completed / failed / unsupported) and the timestamp of the conversion.
    The per-document status is mirrored onto the existing ``documents.preview_status`` column.

The engine/service split mirrors ``ocr_backend`` + ``document_ocr``: pixels live in
:mod:`app.services.image_normalization` (DB-free, lazily imports Pillow); state, provenance and
idempotency live here.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Table, select

from app.db import documents, engine, metadata
from app.services.image_normalization import (
    NORMALIZED_MIME,
    ImageNormalizationError,
    ImageNormalizationUnavailable,
    UnsupportedImageError,
    derivative_root,
    needs_normalization,
    normalize_image_file,
)

_log = logging.getLogger(__name__)

#: The only derivative kind today: the JPEG rendition used by OCR, previews and AI image inputs.
KIND_NORMALIZED_IMAGE = "normalized_image"

PENDING, PROCESSING, COMPLETED, FAILED, SKIPPED, UNSUPPORTED = (
    "pending", "processing", "completed", "failed", "skipped", "unsupported")

#: States a caller must never treat as "there is a usable derivative".
_TERMINAL_BAD = (FAILED, UNSUPPORTED)


class DerivativeUnavailable(RuntimeError):
    """A normalized derivative is REQUIRED for this document but could not be produced.

    Raised instead of quietly handing a consumer the HEIF original, so an AI/OCR step reports a real
    failure rather than claiming success on bytes it could not read. ``retryable`` distinguishes a host
    problem (imaging libraries absent) from a terminal verdict on the file itself."""

    def __init__(self, message: str, *, document_id: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.document_id = document_id
        self.retryable = retryable


@dataclass(frozen=True)
class ImageSource:
    """What an image consumer should actually read for a document."""
    document_id: int | None
    path: Path
    mime: str
    is_derivative: bool
    original_mime: str | None = None
    original_name: str | None = None


def _table() -> Table:
    """Tolerant bind for ``document_derivatives`` (the docnorm01 migration may not be applied yet in
    every environment — the same pattern ``document_sources`` uses)."""
    table = metadata.tables.get("document_derivatives")
    if table is None:
        table = Table("document_derivatives", metadata, autoload_with=engine)
    return table


def original_path(row) -> Path | None:
    """Resolve the on-disk path of a document's ORIGINAL file.

    Prefers an absolute ``storage_uri`` (TaxDome-synced and relocated repository documents) and falls
    back to the repo-relative ``storage_path`` used by directly-uploaded documents — the resolution the
    download and OCR paths already perform."""
    for key in ("storage_uri", "storage_path"):
        value = row.get(key) if hasattr(row, "get") else None
        if value:
            path = Path(str(value))
            if path.exists():
                return path
    return None


# --- state -------------------------------------------------------------------

def _write_state(document_id: int, *, status: str, kind: str = KIND_NORMALIZED_IMAGE,
                 source_mime=None, source_hash=None, derivative_mime=None, derivative_path=None,
                 derivative_hash=None, derivative_size_bytes=None, width=None, height=None,
                 engine_name=None, last_error=None, bump_attempt=False, completed=False) -> None:
    """Upsert the ``document_derivatives`` row and mirror the status onto ``documents.preview_status``.

    Idempotent — one row per (document, kind), enforced by ``uq_document_derivative_kind``."""
    table = _table()
    now = datetime.now(UTC)
    values = {"status": status, "updated_at": now}
    for column, value in (("source_mime", source_mime), ("source_hash", source_hash),
                          ("derivative_mime", derivative_mime),
                          ("derivative_path", derivative_path),
                          ("derivative_hash", derivative_hash),
                          ("derivative_size_bytes", derivative_size_bytes),
                          ("width", width), ("height", height), ("engine", engine_name),
                          ("last_error", last_error)):
        if value is not None:
            values[column] = value
    if status == COMPLETED:
        values["last_error"] = None                        # a success clears the previous failure
    if completed:
        values["converted_at"] = now

    with engine.begin() as connection:
        existing = connection.execute(
            select(table.c.id, table.c.attempts)
            .where(table.c.document_id == document_id, table.c.kind == kind)).mappings().first()
        if existing is None:
            connection.execute(table.insert().values(
                document_id=document_id, kind=kind, attempts=1 if bump_attempt else 0, **values))
        else:
            if bump_attempt:
                values["attempts"] = (existing["attempts"] or 0) + 1
            connection.execute(table.update().where(table.c.id == existing["id"]).values(**values))
        connection.execute(documents.update().where(documents.c.id == document_id)
                           .values(preview_status=status))


def mark_pending(document_id: int, *, source_mime: str | None = None,
                 source_hash: str | None = None, kind: str = KIND_NORMALIZED_IMAGE) -> None:
    """Record that a document NEEDS a derivative but does not have one yet.

    Called at upload so the conversion is visible to the document processing pipeline as real pending
    work rather than an absence. Writes state only — no image is decoded here."""
    _write_state(document_id, status=PENDING, kind=kind, source_mime=source_mime,
                 source_hash=source_hash)


def derivative_state(document_id: int, *, kind: str = KIND_NORMALIZED_IMAGE) -> dict | None:
    """The recorded conversion state for a document, or ``None`` if it has never been normalized."""
    table = _table()
    with engine.connect() as connection:
        row = connection.execute(
            select(table).where(table.c.document_id == document_id,
                                table.c.kind == kind)).mappings().first()
    return dict(row) if row is not None else None


def _document_row(document_id: int):
    with engine.connect() as connection:
        return connection.execute(
            select(documents).where(documents.c.id == document_id)).mappings().one_or_none()


# --- conversion --------------------------------------------------------------

def ensure_normalized_image(document_id: int, *, row=None, force: bool = False) -> dict:
    """Ensure a document has a usable normalized JPEG derivative, and record the outcome.

    Never raises for a bad file and never touches the original: a failure is recorded as ``failed``
    (retryable — a host/library problem) or ``unsupported`` (terminal — a multi-frame HEIF, a spoofed
    extension, a corrupt image or an image bomb) and returned to the caller. Returns the state dict.

    Documents that do not need normalization are recorded ``skipped`` — an explicit, truthful state
    rather than a silent absence."""
    row = row if row is not None else _document_row(document_id)
    if row is None:
        raise LookupError(f"document {document_id} does not exist")

    name, content_type = row.get("original_name"), row.get("content_type")
    if not needs_normalization(filename=name, content_type=content_type):
        _write_state(document_id, status=SKIPPED, source_mime=content_type,
                     source_hash=row.get("sha256"),
                     last_error=None)
        return derivative_state(document_id) or {}

    existing = derivative_state(document_id)
    source_hash = row.get("sha256")
    if (not force and existing and existing["status"] == COMPLETED
            and existing["source_hash"] == source_hash
            and existing["derivative_path"] and Path(existing["derivative_path"]).exists()):
        return existing                                    # idempotent: content unchanged, file present

    path = original_path(row)
    if path is None:
        _write_state(document_id, status=FAILED, source_mime=content_type, source_hash=source_hash,
                     last_error="The stored copy of this document could not be found on the server.",
                     bump_attempt=True)
        return derivative_state(document_id) or {}

    _write_state(document_id, status=PROCESSING, source_mime=content_type, source_hash=source_hash)
    try:
        result = normalize_image_file(path, source_sha256=source_hash or None, force=force,
                                      display_name=name)
    except UnsupportedImageError as exc:
        # Terminal: the file itself cannot yield a normalized image. The original is untouched and
        # remains downloadable; retrying would burn the same work for the same verdict.
        _log.info("Image normalization unsupported: doc=%s (%s)", document_id, type(exc).__name__)
        _write_state(document_id, status=UNSUPPORTED, source_mime=content_type,
                     source_hash=source_hash, last_error=str(exc)[:2000], bump_attempt=False)
        return derivative_state(document_id) or {}
    except (ImageNormalizationUnavailable, ImageNormalizationError, OSError) as exc:
        # Retryable: a host problem (imaging libraries absent, unwritable derivative root, disk error).
        _log.warning("Image normalization failed: doc=%s (%s)", document_id, type(exc).__name__)
        _write_state(document_id, status=FAILED, source_mime=content_type, source_hash=source_hash,
                     last_error=str(exc)[:2000], bump_attempt=True)
        return derivative_state(document_id) or {}

    _write_state(document_id, status=COMPLETED, source_mime=content_type,
                 source_hash=result.source_sha256, derivative_mime=result.mime,
                 derivative_path=str(result.path), derivative_hash=result.sha256,
                 derivative_size_bytes=result.size_bytes, width=result.width, height=result.height,
                 engine_name=result.engine, bump_attempt=True, completed=True)
    return derivative_state(document_id) or {}


# --- the consumer seam -------------------------------------------------------

def image_source_for_document(document_id: int, *, row=None, require_normalized: bool = True
                              ) -> ImageSource:
    """The image a consumer should read for this document.

    For a HEIC/HEIF original this converts on demand (or reuses an existing derivative) and returns the
    normalized JPEG. For every other image type it returns the original untouched — no re-encode, no
    behaviour change for the JPEG/PNG/GIF/TIFF flows that already work.

    Raises :class:`DerivativeUnavailable` when a HEIF document has no usable derivative, so an AI or
    OCR consumer fails honestly instead of being handed bytes it cannot read. Pass
    ``require_normalized=False`` for a best-effort consumer (e.g. a browser preview that has its own
    fallback page) that would rather try the original than get an exception."""
    row = row if row is not None else _document_row(document_id)
    if row is None:
        raise LookupError(f"document {document_id} does not exist")

    name, content_type = row.get("original_name"), row.get("content_type")
    path = original_path(row)

    if not needs_normalization(filename=name, content_type=content_type):
        if path is None:
            raise DerivativeUnavailable(
                "The stored copy of this document could not be found on the server.",
                document_id=document_id, retryable=True)
        return ImageSource(document_id=document_id, path=path,
                           mime=content_type or "application/octet-stream", is_derivative=False,
                           original_mime=content_type, original_name=name)

    state = ensure_normalized_image(document_id, row=row)
    if state.get("status") == COMPLETED and state.get("derivative_path"):
        derivative = Path(state["derivative_path"])
        if derivative.exists():
            return ImageSource(document_id=document_id, path=derivative, mime=NORMALIZED_MIME,
                               is_derivative=True, original_mime=content_type, original_name=name)

    message = state.get("last_error") or "This image could not be converted for processing."
    if require_normalized:
        raise DerivativeUnavailable(message, document_id=document_id,
                                    retryable=state.get("status") not in _TERMINAL_BAD
                                    or state.get("status") == FAILED)
    if path is None:
        raise DerivativeUnavailable(message, document_id=document_id, retryable=True)
    return ImageSource(document_id=document_id, path=path,
                       mime=content_type or "application/octet-stream", is_derivative=False,
                       original_mime=content_type, original_name=name)


def ai_image_source(document_id: int, *, row=None) -> ImageSource:
    """The image path an AI/OpenAI image call must attach for this document.

    Always a downstream-safe, size-bounded JPEG (or an already-safe original); never a HEIC/HEIF. A
    document that cannot produce one raises :class:`DerivativeUnavailable` — an AI step must report the
    gap, not silently attach an unreadable file or claim it processed the image."""
    return image_source_for_document(document_id, row=row, require_normalized=True)


# --- lifecycle: orphaned derivative files ------------------------------------

#: The ONLY filename shape this module ever creates, and therefore the only shape the sweep will
#: consider deleting: a 64-hex content digest plus ``.jpg``. An uploaded ORIGINAL never has this
#: shape — workspace originals are ``<uuid32>.<ext>`` under the document root and vault originals are
#: ``<shard>/<uuid32>.<ext>`` under the vault root — so an original cannot be selected even if one
#: were somehow placed inside the derivative store.
_DERIVATIVE_NAME = re.compile(r"^[0-9a-f]{64}\.jpg$")

#: A derivative younger than this is never swept, so a conversion that is in flight (or a document
#: row committed moments after its file was written) can never be raced.
ORPHAN_MIN_AGE_DAYS = 7


def _referenced_digests(connection) -> set[str]:
    """Every source digest that still has a claim on a derivative file.

    Two independent claims, deliberately: a ``document_derivatives`` row that points at the file, and
    ANY live ``documents.sha256`` — content addressing means one file legitimately serves every
    document with identical bytes, so a still-present twin keeps it alive even after one document's
    derivative row is gone."""
    table = _table()
    digests = {row[0] for row in connection.execute(
        select(table.c.source_hash).where(table.c.source_hash.isnot(None))) if row[0]}
    digests |= {row[0] for row in connection.execute(
        select(documents.c.sha256).where(documents.c.sha256.isnot(None))) if row[0]}
    # A derivative_path recorded against a row is authoritative even if its source_hash is NULL.
    for row in connection.execute(
            select(table.c.derivative_path).where(table.c.derivative_path.isnot(None))):
        if row[0]:
            digests.add(Path(row[0]).stem)
    return digests


def prune_orphan_derivatives(*, dry_run: bool = True, min_age_days: int = ORPHAN_MIN_AGE_DAYS,
                             root: Path | None = None) -> dict:
    """Remove derivative JPEGs that no document can still claim. DRY RUN BY DEFAULT.

    Bounded growth: derivatives are content-addressed, so the store holds at most one JPEG per
    DISTINCT uploaded image — re-uploading the same photo reuses the existing file rather than adding
    another. This sweep closes the remaining leak: files whose document was hard-deleted or whose
    content no longer exists anywhere in ``documents``.

    Four independent safety bounds, each of which alone prevents deleting anything that is not an
    orphaned derivative:

      1. only files INSIDE the resolved derivative root are even listed, and each candidate's
         resolved path is re-checked against that root before deletion (no symlink escape);
      2. only files matching ``<64 hex>.jpg`` — the one shape this module writes — are considered, so
         an uploaded original can never be selected;
      3. only files older than ``min_age_days`` are considered, so an in-flight conversion is safe;
      4. any digest still claimed by a ``document_derivatives`` row OR by ANY live ``documents.sha256``
         is kept, so one document's sweep can never take another document's shared derivative.

    Returns ``{root, scanned, orphans, deleted, reclaimed_bytes, kept, dry_run, skipped}``. Never
    touches, reads or deletes an original document under any circumstance."""
    root = (root or derivative_root()).resolve()
    cutoff = datetime.now(UTC).timestamp() - (max(0, min_age_days) * 86400)
    summary = {"root": str(root), "scanned": 0, "orphans": 0, "deleted": 0, "reclaimed_bytes": 0,
               "kept": 0, "dry_run": dry_run, "skipped": []}

    with engine.connect() as connection:
        referenced = _referenced_digests(connection)

    for candidate in sorted(root.rglob("*.jpg")):
        summary["scanned"] += 1
        if not _DERIVATIVE_NAME.match(candidate.name):
            summary["skipped"].append(f"{candidate.name}: not a derivative filename")
            continue
        resolved = candidate.resolve()
        if root not in resolved.parents:                  # bound 1, re-checked after resolution
            summary["skipped"].append(f"{candidate.name}: resolves outside the derivative root")
            continue
        if candidate.stem in referenced:                  # bound 4
            summary["kept"] += 1
            continue
        try:
            stat = candidate.stat()
        except OSError:                                   # vanished under us — nothing to do
            continue
        if stat.st_mtime > cutoff:                        # bound 3
            summary["kept"] += 1
            continue
        summary["orphans"] += 1
        if dry_run:
            continue
        try:
            candidate.unlink()
        except OSError as exc:
            summary["skipped"].append(f"{candidate.name}: {type(exc).__name__}")
            continue
        summary["deleted"] += 1
        summary["reclaimed_bytes"] += stat.st_size

    _log.info("derivative orphan sweep: scanned=%s orphans=%s deleted=%s kept=%s dry_run=%s",
              summary["scanned"], summary["orphans"], summary["deleted"], summary["kept"], dry_run)
    return summary


def main(argv=None):
    """``python -m app.services.document_derivatives --prune [--apply]`` — operational sweep."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m app.services.document_derivatives",
        description="Report (and optionally remove) orphaned normalized-image derivative files.")
    parser.add_argument("--prune", action="store_true", help="Run the orphan sweep (default: report).")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete the orphans. Without it the sweep is a dry run.")
    parser.add_argument("--min-age-days", type=int, default=ORPHAN_MIN_AGE_DAYS,
                        help=f"Only sweep files older than this (default {ORPHAN_MIN_AGE_DAYS}).")
    args = parser.parse_args(argv)
    if not args.prune:
        parser.error("nothing to do: pass --prune")
    result = prune_orphan_derivatives(dry_run=not args.apply, min_age_days=args.min_age_days)
    print(f"Derivative store: {result['root']}")
    print(f"  scanned:   {result['scanned']}")
    print(f"  kept:      {result['kept']}")
    print(f"  orphans:   {result['orphans']}")
    print(f"  deleted:   {result['deleted']} ({result['reclaimed_bytes']} bytes)"
          if not result["dry_run"] else "  deleted:   0 (dry run — pass --apply)")
    for skipped in result["skipped"][:20]:
        print(f"  skipped:   {skipped}")
    return 0


if __name__ == "__main__":  # pragma: no cover — operational entry point
    raise SystemExit(main())
