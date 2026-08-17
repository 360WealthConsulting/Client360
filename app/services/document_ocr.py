"""OCR as a service over the canonical document model (PR 5A).

OCR ENRICHES the existing canonical ``documents`` row — it does not create a second document system.
Extracted text + state live in ``document_ocr`` (one row per canonical document), and the authoritative
per-document status is mirrored onto ``documents.ocr_status`` so the Documents tab and Universal Search
read it directly. No ADR change, no ownership change, no OCR-specific pages.

Honest boundary: the pixel/PDF text-extraction ENGINE (Tesseract, a PDF text layer, a cloud OCR) is
environment-specific, so it is injected as an ``extractor`` callable. This service is the orchestration
around it: candidate selection (initial / incremental / retry / reprocess), idempotent writes, an
attempts counter for retry, content-hash change detection for reprocessing, and audit logging. Tests
inject a deterministic extractor; production wires a real engine at deploy time. When no engine is
configured, supported documents fail *cleanly* (retryable) rather than being silently marked done.

``extractor(document_row: dict, path: pathlib.Path | None) -> str | dict`` — return the extracted text,
or a dict ``{"text", "engine", "page_count"}``. It is responsible for reading the file; raising signals
a (retryable) failure.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import or_, select

from app.db import document_ocr, documents, engine

# OCR applies to scanned/image documents and PDFs (a scanned document arrives as a PDF or image).
SUPPORTED_EXT = {"pdf", "tif", "tiff", "png", "jpg", "jpeg", "heic", "heif"}
DEFAULT_ENGINE = "client360-ocr"
_TERMINAL_OK = "completed"


class OcrBackendUnavailable(RuntimeError):
    """No OCR engine is configured on this host — a supported document cannot be processed yet."""


class OcrTimeout(RuntimeError):
    """OCR exceeded its per-page or per-document time budget. Distinct from a generic extraction failure
    so a timed-out document is recorded separately and the batch moves on to the next document."""


def _ext(name: str | None) -> str:
    return (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


def is_ocr_supported(name: str | None) -> bool:
    return _ext(name) in SUPPORTED_EXT


def _local_path(row) -> Path | None:
    for key in ("storage_uri", "storage_path"):
        val = row.get(key)
        if val:
            p = Path(str(val))
            if p.exists():
                return p
    return None


def default_extractor(row, path):
    """Production extractor stub: delegates to a real engine when one is installed on the host.

    No OCR engine ships in the repo (CI stays dependency-free), so this raises cleanly and the document
    is left in a retryable ``failed`` state until the deployment wires an engine. Deployments replace or
    configure this by passing their own ``extractor`` to :func:`run_ocr`."""
    raise OcrBackendUnavailable(
        "No OCR engine configured on this host; pass an extractor or install the OCR backend.")


def _normalize(result) -> tuple[str, str, int | None]:
    if isinstance(result, dict):
        return (result.get("text") or "", result.get("engine") or DEFAULT_ENGINE,
                result.get("page_count"))
    return (result or "", DEFAULT_ENGINE, None)


def _candidates(conn, *, mode, document_ids, max_attempts, batch_size):
    """Select canonical documents to OCR for the given run mode (record-scope is enforced by the
    caller's document_ids when present; sweeps run firm-wide by design, like the source-sync jobs)."""
    j = documents.outerjoin(document_ocr, document_ocr.c.document_id == documents.c.id)
    cols = [documents.c.id, documents.c.original_name, documents.c.sha256,
            documents.c.storage_uri, documents.c.storage_path,
            document_ocr.c.status.label("ocr_state"), document_ocr.c.attempts,
            document_ocr.c.source_hash]
    stmt = select(*cols).select_from(j).where(documents.c.status != "deleted")
    if document_ids is not None:
        stmt = stmt.where(documents.c.id.in_(tuple(document_ids) or (-1,)))
    elif mode == "retry":
        stmt = stmt.where(document_ocr.c.status.in_(("failed", "timed_out")),
                          document_ocr.c.attempts < max_attempts)
    elif mode == "reprocess":
        stmt = stmt.where(or_(document_ocr.c.document_id.is_(None),
                              document_ocr.c.source_hash != documents.c.sha256,
                              document_ocr.c.status != _TERMINAL_OK))
    else:  # initial / incremental — not-yet-attempted (failed rows are the retry mode's job, so a
        # resumable batch sweep terminates instead of re-selecting a poison document every batch)
        stmt = stmt.where(or_(document_ocr.c.document_id.is_(None),
                              document_ocr.c.status.in_(("pending", "processing"))))
    return conn.execute(stmt.order_by(documents.c.id).limit(batch_size)).mappings().all()


def _new_summary(mode, dry_run):
    return {"mode": mode, "candidates": 0, "completed": 0, "failed": 0, "timed_out": 0, "skipped": 0,
            "unsupported": 0, "chars_extracted": 0, "errors": [], "dry_run": dry_run,
            "status": "started"}


def run_ocr(*, document_ids=None, extractor=None, mode="incremental", actor_user_id=None,
            request_id=None, max_attempts=3, batch_size=200, dry_run=False) -> dict:
    """Run OCR over canonical documents. ``mode``: ``initial``/``incremental`` (process anything not
    completed), ``retry`` (failed rows under ``max_attempts``), ``reprocess`` (force, or content changed).
    Idempotent: a completed, content-unchanged document is skipped unless ``mode='reprocess'``. Returns
    a summary of counts."""
    extractor = extractor or default_extractor
    summary = _new_summary(mode, dry_run)
    with engine.connect() as conn:
        cands = _candidates(conn, mode=mode, document_ids=document_ids,
                            max_attempts=max_attempts, batch_size=batch_size)
    summary["candidates"] = len(cands)

    force = mode == "reprocess"      # reprocess re-OCRs completed docs; other modes skip unchanged
    for row in cands:
        try:
            _ocr_one(row, extractor, force, dry_run, summary)
        except Exception as exc:      # noqa: BLE001 — record & continue
            summary["errors"].append(f"doc {row['id']}: {exc}")
    _audit(summary, actor_user_id, request_id, dry_run)
    summary["status"] = "dry_run" if dry_run else ("completed_with_errors" if summary["errors"]
                                                   else "completed")
    return summary


def _ocr_one(row, extractor, force, dry_run, summary):
    doc_id, name, sha = row["id"], row["original_name"], row["sha256"]

    if not is_ocr_supported(name):
        summary["unsupported"] += 1
        if not dry_run:
            _write_state(doc_id, status="unsupported", engine_name=None, source_hash=sha)
        return

    # Idempotent: a completed, content-unchanged document is not re-OCR'd unless forced.
    if not force and row["ocr_state"] == _TERMINAL_OK and row["source_hash"] == sha:
        summary["skipped"] += 1
        return

    if dry_run:
        summary["candidates"] = summary["candidates"]  # counted; no writes in dry-run
        return

    path = _local_path(row)
    try:
        text, engine_name, page_count = _normalize(extractor(dict(row), path))
    except OcrTimeout as exc:     # a bounded OCR that overran — recorded distinctly, retryable, never fatal
        _write_state(doc_id, status="timed_out", last_error=str(exc)[:2000], bump_attempt=True)
        summary["timed_out"] += 1
        return
    except Exception as exc:      # noqa: BLE001 — a failed extraction is retryable, not fatal
        _write_state(doc_id, status="failed", last_error=str(exc)[:2000], bump_attempt=True)
        summary["failed"] += 1
        return

    _write_state(doc_id, status="completed", text=text, engine_name=engine_name,
                 page_count=page_count, source_hash=sha, bump_attempt=True,
                 completed=True)
    summary["completed"] += 1
    summary["chars_extracted"] += len(text or "")


def _write_state(doc_id, *, status, text=None, engine_name=None, page_count=None,
                 source_hash=None, last_error=None, bump_attempt=False, completed=False):
    """Upsert the document_ocr row and mirror the status onto documents.ocr_status (idempotent —
    one document_ocr row per canonical document, enforced by uq_document_ocr_document)."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        existing = conn.execute(select(document_ocr.c.id, document_ocr.c.attempts).where(
            document_ocr.c.document_id == doc_id)).mappings().first()
        values = {"status": status, "updated_at": now}
        if text is not None:
            values["text"] = text
            values["char_count"] = len(text)
        if engine_name is not None:
            values["engine"] = engine_name
        if page_count is not None:
            values["page_count"] = page_count
        if source_hash is not None:
            values["source_hash"] = source_hash
        if last_error is not None:
            values["last_error"] = last_error
        if completed:
            values["ocr_completed_at"] = now
        if existing is None:
            conn.execute(document_ocr.insert().values(
                document_id=doc_id, ocr_started_at=now,
                attempts=1 if bump_attempt else 0, **values))
        else:
            if bump_attempt:
                values["attempts"] = (existing["attempts"] or 0) + 1
            if status == "processing" or (status == "completed" and "ocr_started_at" not in values):
                values.setdefault("ocr_started_at", now)
            conn.execute(document_ocr.update().where(
                document_ocr.c.id == existing["id"]).values(**values))
        conn.execute(documents.update().where(documents.c.id == doc_id).values(ocr_status=status))


def _audit(summary, actor_user_id, request_id, dry_run):
    if dry_run:
        return
    from app.security.audit import write_audit_event
    write_audit_event(
        action="document.ocr_run", entity_type="document", entity_id=None,
        actor_user_id=actor_user_id, request_id=request_id or f"ocr-{uuid.uuid4()}",
        metadata={k: summary[k] for k in ("mode", "candidates", "completed", "failed",
                                          "skipped", "unsupported", "chars_extracted")})


def extract_text(document_id: int, *, extractor=None, actor_user_id=None, request_id=None) -> dict:
    """Convenience: OCR a single canonical document now (forces reprocessing of that id)."""
    return run_ocr(document_ids=[document_id], extractor=extractor, mode="reprocess",
                   actor_user_id=actor_user_id, request_id=request_id)


def ocr_for_documents(document_ids) -> dict[int, dict]:
    """Read the OCR record (status/completed_at/text availability) for the given canonical documents,
    keyed by document_id — powers the Documents tab without a per-row query."""
    ids = [i for i in document_ids if i]
    if not ids:
        return {}
    out: dict[int, dict] = {}
    with engine.connect() as conn:
        for r in conn.execute(select(
                document_ocr.c.document_id, document_ocr.c.status, document_ocr.c.ocr_completed_at,
                document_ocr.c.char_count, document_ocr.c.page_count, document_ocr.c.engine)
                .where(document_ocr.c.document_id.in_(ids))).mappings():
            out[r["document_id"]] = dict(r)
    return out


# --- CLI ---------------------------------------------------------------------

def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="python -m app.services.document_ocr",
                                description="Run OCR over canonical documents (enrichment only).")
    p.add_argument("--mode", choices=("initial", "incremental", "retry", "reprocess"),
                   default="incremental")
    p.add_argument("--batch-size", type=int, default=200)
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    summary = run_ocr(mode=args.mode, batch_size=args.batch_size, max_attempts=args.max_attempts,
                      dry_run=args.dry_run)
    for k in ("mode", "candidates", "completed", "failed", "skipped", "unsupported",
              "chars_extracted", "status"):
        print(f"  {k}: {summary[k]}")
    if summary["errors"]:
        print(f"  errors ({len(summary['errors'])}):")
        for e in summary["errors"][:20]:
            print(f"    - {e}")
    return 1 if summary["status"] == "completed_with_errors" else 0


if __name__ == "__main__":
    raise SystemExit(main())
