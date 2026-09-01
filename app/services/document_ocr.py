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

import contextvars
import logging
import time as _time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, or_, select

from app.db import document_ocr, documents, engine

_log = logging.getLogger(__name__)

# Optional observer for the SharePoint baseline live-OCR path. When the baseline loop
# (microsoft_ingestion._ocr_documents) sets this, _live_ocr forwards it to run_ocr so the existing
# subprocess-isolation heartbeat callback reaches the baseline status tracker DURING a long OCR document.
# Default None → the operational runner and every other caller are completely unaffected.
live_ocr_observer = contextvars.ContextVar("live_ocr_observer", default=None)

# OCR applies to scanned/image documents and PDFs (a scanned document arrives as a PDF or image).
SUPPORTED_EXT = {"pdf", "tif", "tiff", "png", "jpg", "jpeg", "heic", "heif"}
DEFAULT_ENGINE = "client360-ocr"
_TERMINAL_OK = "completed"


# The single image-normalization seam: a HEIC/HEIF document is OCR'd from its JPEG derivative, and a
# document that cannot produce one raises DerivativeUnavailable (see _ocr_source_path).
from app.services.document_derivatives import (  # noqa: E402
    DerivativeUnavailable,
    ai_image_source,
)

# Re-exported from the db-free module so ``from app.services.document_ocr import OcrTimeout`` keeps working
# while the extraction backend + subprocess worker import them without pulling in app.db.
from app.services.ocr_exceptions import (  # noqa: E402,F401
    ENCRYPTED_PDF_LAST_ERROR,
    OcrBackendUnavailable,
    OcrEncryptedPdf,
    OcrIsolationError,
    OcrTimeout,
)

# Sentinel for run_ocr's ``isolate`` parameter: distinguishes "caller omitted an isolation choice" from an
# explicit ``True``/``False``. Omission is FAIL-CLOSED (see _resolve_isolation) — it never silently selects
# the in-process path, so a future production caller cannot accidentally reintroduce an unkillable OCR run.
_ISOLATE_UNSET = object()


def _resolve_isolation(isolate, factory_ref):
    """Resolve run_ocr's isolation decision, failing closed on omission.

      * explicit ``isolate=True``  -> isolated, but REQUIRES a ``factory_ref`` (the child rebuilds the
        extractor from a picklable dotted reference; an in-process extractor object cannot be spawned);
      * explicit ``isolate=False`` -> deliberate in-process path (tests/diagnostics, or the
        OCR_SUBPROCESS_ISOLATION=0 escape hatch) — preserved verbatim;
      * OMITTED with a ``factory_ref`` -> isolated (the safe default when isolation is even possible);
      * OMITTED with no ``factory_ref`` -> refuse (:class:`OcrIsolationError`): the caller must state an
        explicit choice rather than silently running production OCR in-process."""
    if isolate is _ISOLATE_UNSET:
        if factory_ref is not None:
            return True
        raise OcrIsolationError(
            "run_ocr() requires an explicit isolation choice: pass factory_ref=<dotted factory> for the "
            "isolated production path (real wall-clock hard cap + stall watchdog), or isolate=False to "
            "deliberately run in-process (tests/diagnostics — fake extractors that cannot cross the spawn "
            "boundary). Omitting isolation is refused so an unkillable OCR path cannot be reintroduced.")
    if isolate and factory_ref is None:
        raise OcrIsolationError(
            "run_ocr(isolate=True) requires factory_ref=<dotted factory>: the isolated child rebuilds the "
            "extractor from a picklable reference, so an in-process extractor object cannot be used.")
    return bool(isolate)


def _ext(name: str | None) -> str:
    return (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


def is_ocr_supported(name: str | None) -> bool:
    return _ext(name) in SUPPORTED_EXT


# States that are already terminal + truthful — the finalizer never disturbs them.
_TERMINAL_STATES = ("completed", "failed", "timed_out", "unsupported", "skipped")


def finalize_document_ocr_state(document_id):
    """Give a document a TERMINAL, truthful OCR state after pipeline analysis.

    The analysis pipeline extracts text natively for text-layer PDFs and office/plaintext types and only
    calls the OCR backend when native text is inadequate — so those documents never pass through
    ``run_ocr`` and would otherwise linger in a non-terminal ('pending'/none) OCR state even though they
    were fully processed. This records the correct terminal state without re-doing OCR:

      * non-OCR file type (docx/xlsx/txt/...) -> ``unsupported`` (OCR does not apply; handled natively),
      * OCR-capable type with a usable native text layer (PDF) -> ``completed`` (text stored, engine
        'pdf-text-layer'; no OCR was needed),
      * OCR-capable type with NO usable text and still non-terminal -> ``failed`` (genuine gap, retryable).

    Idempotent: a document already in a terminal state is left untouched (so the 42 completed / failed /
    timed_out / unsupported rows are never rewritten). Never raises."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(documents.c.id, documents.c.original_name, documents.c.sha256,
                       documents.c.storage_uri, documents.c.storage_path,
                       document_ocr.c.status.label("ocr_state")).select_from(
                    documents.outerjoin(document_ocr, document_ocr.c.document_id == documents.c.id))
                .where(documents.c.id == document_id)).mappings().first()
        if row is None:
            return None
        if row["ocr_state"] in _TERMINAL_STATES:
            return row["ocr_state"]                          # already terminal + truthful
        name, sha = row["original_name"], row["sha256"]
        if not is_ocr_supported(name):
            _write_state(document_id, status="unsupported", engine_name=None, source_hash=sha)
            return "unsupported"
        # OCR-capable and non-terminal => the pipeline used the native PDF text layer. Confirm + store it.
        text = ""
        path = _local_path(row)
        if _ext(name) == "pdf" and path is not None:
            from app.services.document_owner_proposal import _MIN_NATIVE_CHARS, _pdf_text
            try:
                text = _pdf_text(path) or ""
            except Exception:  # noqa: BLE001 — treat an unreadable native layer as "no text"
                text = ""
            if len(text.strip()) >= _MIN_NATIVE_CHARS:
                _write_state(document_id, status="completed", text=text.strip(),
                             engine_name="pdf-text-layer (no OCR needed)", source_hash=sha, completed=True)
                return "completed"
        # OCR-capable but no usable text and not terminal -> a genuine gap (retryable), recorded truthfully.
        _write_state(document_id, status="failed", source_hash=sha, bump_attempt=True,
                     last_error="analysis produced no OCR state and no usable native text")
        return "failed"
    except Exception:  # noqa: BLE001 — finalization must never break the batch
        return None


def _local_path(row) -> Path | None:
    for key in ("storage_uri", "storage_path"):
        val = row.get(key)
        if val:
            p = Path(str(val))
            if p.exists():
                return p
    return None


def _ocr_source_path(row, path):
    """The image OCR should actually read for this document.

    A HEIC/HEIF original is OCR'd from its NORMALIZED JPEG derivative (produced once and reused) rather
    than decoded ad hoc here, so there is one conversion implementation rather than one per consumer.
    Every other file type — PDF, JPEG, PNG, TIFF — is read exactly as before, byte for byte.

    Degrades gracefully: in an environment where the ``document_derivatives`` table has not been
    migrated yet, this falls back to the original path, which the OCR backend still decodes through
    pillow-heif. A file that genuinely cannot be normalized raises ``DerivativeUnavailable``, which the
    caller records as a truthful failure rather than reporting OCR success on unreadable bytes."""
    from app.services.image_normalization import needs_normalization
    if not needs_normalization(filename=row.get("original_name"),
                               content_type=row.get("content_type")):
        return path
    try:
        return ai_image_source(row["id"], row=row).path
    except DerivativeUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — derivative subsystem unavailable (e.g. un-migrated host)
        _log.warning("Image derivative unavailable for doc=%s (%s); OCR reads the original",
                     row.get("id"), type(exc).__name__)
        return path


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


def _candidate_join():
    return documents.outerjoin(document_ocr, document_ocr.c.document_id == documents.c.id)


def _candidate_conditions(*, mode, document_ids, max_attempts):
    """The exact WHERE predicate that selects OCR candidates for a run mode. Shared by :func:`_candidates`
    (the batch selector) and :func:`count_candidates` (the read-only denominator) so the two can never
    drift apart."""
    conds = [documents.c.status != "deleted"]
    if document_ids is not None:
        conds.append(documents.c.id.in_(tuple(document_ids) or (-1,)))
    elif mode == "retry":
        conds.append(document_ocr.c.status.in_(("failed", "timed_out")))
        conds.append(document_ocr.c.attempts < max_attempts)
    elif mode == "reprocess":
        conds.append(or_(document_ocr.c.document_id.is_(None),
                         document_ocr.c.source_hash != documents.c.sha256,
                         document_ocr.c.status != _TERMINAL_OK))
    else:  # initial / incremental — not-yet-attempted (failed rows are the retry mode's job, so a
        # resumable batch sweep terminates instead of re-selecting a poison document every batch)
        conds.append(or_(document_ocr.c.document_id.is_(None),
                         document_ocr.c.status.in_(("pending", "processing"))))
    return conds


def _candidates(conn, *, mode, document_ids, max_attempts, batch_size):
    """Select canonical documents to OCR for the given run mode (record-scope is enforced by the
    caller's document_ids when present; sweeps run firm-wide by design, like the source-sync jobs)."""
    cols = [documents.c.id, documents.c.original_name, documents.c.sha256,
            documents.c.storage_uri, documents.c.storage_path, documents.c.content_type,
            document_ocr.c.status.label("ocr_state"), document_ocr.c.attempts,
            document_ocr.c.source_hash]
    stmt = (select(*cols).select_from(_candidate_join())
            .where(*_candidate_conditions(mode=mode, document_ids=document_ids, max_attempts=max_attempts))
            .order_by(documents.c.id).limit(batch_size))
    return conn.execute(stmt).mappings().all()


def count_candidates(*, mode="incremental", document_ids=None, max_attempts=3) -> int:
    """Read-only count of the documents a sweep would still process for ``mode`` (ignoring batch size).
    Operational only — gives the status tracker a best-effort denominator; never mutates anything."""
    stmt = (select(func.count()).select_from(_candidate_join())
            .where(*_candidate_conditions(mode=mode, document_ids=document_ids, max_attempts=max_attempts)))
    with engine.connect() as conn:
        return int(conn.execute(stmt).scalar() or 0)


def _new_summary(mode, dry_run):
    return {"mode": mode, "candidates": 0, "completed": 0, "failed": 0, "timed_out": 0, "skipped": 0,
            "unsupported": 0, "encrypted": 0, "chars_extracted": 0, "errors": [], "dry_run": dry_run,
            "status": "started"}


# OCR summary counter -> ProgressReporter outcome bucket (skipped == already-completed == reused).
# ``encrypted`` is its OWN bucket (password-protected PDFs) so operators can distinguish it from ordinary
# unsupported file types in progress/status telemetry, even though the persisted status is 'unsupported'.
_OCR_TELEMETRY_BUCKET = {"completed": "completed", "failed": "failed", "timed_out": "timed_out",
                         "unsupported": "unsupported", "skipped": "reused", "encrypted": "encrypted"}


def _observe(observer, method, *args):
    """Invoke an optional operational observer hook (status/heartbeat tracker). Purely observational and
    fully isolated: a missing hook or any exception it raises is swallowed so tracking can never affect
    OCR processing."""
    if observer is None:
        return
    fn = getattr(observer, method, None)
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 — a broken status sink must never break the OCR run
        pass


def run_ocr(*, document_ids=None, extractor=None, mode="incremental", actor_user_id=None,
            request_id=None, max_attempts=3, batch_size=200, dry_run=False, progress=None,
            report_every=100, report_interval=60.0, isolate=_ISOLATE_UNSET, factory_ref=None,
            hard_timeout=None, stall_timeout=None, observer=None) -> dict:
    """Run OCR over canonical documents. ``mode``: ``initial``/``incremental`` (process anything not
    completed), ``retry`` (failed rows under ``max_attempts``), ``reprocess`` (force, or content changed).
    Idempotent: a completed, content-unchanged document is skipped unless ``mode='reprocess'``. Returns
    a summary of counts. Pass ``progress`` (a sink callable) to emit throttled per-phase telemetry (phase,
    processed/total, %, elapsed, rolling throughput, ETA, completed/failed/unsupported/timed_out/reused).

    ISOLATION IS FAIL-CLOSED (see :func:`_resolve_isolation`): every caller must make an explicit choice.
    Pass ``factory_ref=`` for the isolated production path (killable child process + real wall-clock hard
    cap / stall watchdog), or ``isolate=False`` for the deliberate in-process path (tests/diagnostics).
    Omitting isolation entirely is refused with :class:`OcrIsolationError` so a new production caller can
    never silently reintroduce an unkillable in-process OCR run."""
    from app.services.progress import ProgressReporter
    isolate = _resolve_isolation(isolate, factory_ref)
    extractor = extractor or default_extractor
    summary = _new_summary(mode, dry_run)
    with engine.connect() as conn:
        cands = _candidates(conn, mode=mode, document_ids=document_ids,
                            max_attempts=max_attempts, batch_size=batch_size)
    summary["candidates"] = len(cands)
    rep = (ProgressReporter(f"ocr:{mode}", total=len(cands), sink=progress,
                            every=report_every, interval=report_interval,
                            extra_outcomes=("encrypted",))
           if progress is not None else None)

    if isolate and (hard_timeout is None or stall_timeout is None):
        from app.services.ocr_isolation import default_bounds
        _hard, _stall = default_bounds()
        hard_timeout = hard_timeout if hard_timeout is not None else _hard
        stall_timeout = stall_timeout if stall_timeout is not None else _stall

    force = mode == "reprocess"      # reprocess re-OCRs completed docs; other modes skip unchanged
    for row in cands:
        _observe(observer, "on_document_start", row["id"], row["original_name"])
        before = {k: summary[k] for k in _OCR_TELEMETRY_BUCKET}   # detect this doc's outcome by the delta
        try:
            _ocr_one(row, extractor, force, dry_run, summary, isolate=isolate, factory_ref=factory_ref,
                     hard_timeout=hard_timeout, stall_timeout=stall_timeout, observer=observer)
            outcome = next((_OCR_TELEMETRY_BUCKET[k] for k in _OCR_TELEMETRY_BUCKET
                            if summary[k] > before[k]), None)
        except Exception as exc:      # noqa: BLE001 — record & continue (never blocks the batch)
            summary["errors"].append(f"doc {row['id']}: {exc}")
            outcome = "failed"
        _observe(observer, "on_document_result", row["id"], row["original_name"], outcome)
        if rep is not None:
            rep.advance(outcome=outcome)
    if rep is not None:
        rep.emit()                    # final telemetry line
    _audit(summary, actor_user_id, request_id, dry_run)
    summary["status"] = "dry_run" if dry_run else ("completed_with_errors" if summary["errors"]
                                                   else "completed")
    return summary


def _ocr_one(row, extractor, force, dry_run, summary, *, isolate=False, factory_ref=None,
             hard_timeout=None, stall_timeout=None, observer=None):
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
        path = _ocr_source_path(row, path)
    except DerivativeUnavailable as exc:
        # No readable image exists for this document. Recorded truthfully — retryable when the cause is
        # a host problem (imaging libraries absent), terminal 'unsupported' when the FILE itself cannot
        # yield an image (multi-frame HEIF, corrupt, spoofed extension). The original is untouched and
        # still downloadable either way; OCR never claims success on bytes it could not read.
        state = "failed" if exc.retryable else "unsupported"
        _log.warning("OCR has no usable normalized image: doc=%s file=%s — recording %s",
                     doc_id, name, state)
        _write_state(doc_id, status=state, last_error=str(exc)[:2000], bump_attempt=exc.retryable)
        summary["failed" if exc.retryable else "unsupported"] += 1
        return

    started = _time.monotonic()
    try:
        if isolate:
            # Real wall-clock isolation: run extraction in a child process the parent can kill, so a
            # pathological document can never freeze the batch (see app.services.ocr_isolation).
            from app.services import ocr_isolation
            on_hb = getattr(observer, "heartbeat", None) if observer is not None else None
            result = ocr_isolation.run_document(
                factory_ref, dict(row), str(path) if path else None,
                hard_timeout=hard_timeout, stall_timeout=stall_timeout, doc_id=doc_id, name=name,
                on_heartbeat=on_hb)
        else:
            result = extractor(dict(row), path)
        text, engine_name, page_count = _normalize(result)
    except OcrTimeout as exc:     # overran/stalled — recorded distinctly, retryable, never fatal
        _log.warning("OCR timed out: doc=%s file=%s elapsed=%.1fs — recording timed_out, continuing",
                     doc_id, name, _time.monotonic() - started)
        _write_state(doc_id, status="timed_out", last_error=str(exc)[:2000], bump_attempt=True)
        summary["timed_out"] += 1
        return
    except OcrEncryptedPdf:       # password-protected — TERMINAL, distinct, NOT a retryable failure
        # Recorded as 'unsupported' (the schema's terminal non-retryable state — no migration) with the
        # structured password_required reason so operators can distinguish it from other unsupported files.
        # attempts are NOT bumped: this is a determinate outcome, not a transient failure to retry. The
        # batch continues to the next document immediately.
        _log.info("OCR skipped (encrypted/password-protected PDF): doc=%s file=%s — recording unsupported "
                  "(%s), not retrying", doc_id, name, ENCRYPTED_PDF_LAST_ERROR)
        _write_state(doc_id, status="unsupported", last_error=ENCRYPTED_PDF_LAST_ERROR, bump_attempt=False)
        summary["encrypted"] += 1
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


def record_ocr_unavailable(document_id, reason):
    """Record that OCR was REQUIRED for a document but the backend could not be built/configured (engine
    or libraries not installed on this host). Writes a truthful, RETRYABLE 'failed' state — never a silent
    no-state that leaves a scanned/image document stuck with no OCR row. Retry mode re-attempts it once the
    engine is installed. Never raises."""
    try:
        _write_state(document_id, status="failed", bump_attempt=True,
                     last_error=f"OCR backend unavailable: {reason}"[:2000])
    except Exception:  # noqa: BLE001 — recording must never break the caller
        pass


def _audit(summary, actor_user_id, request_id, dry_run):
    if dry_run:
        return
    from app.security.audit import write_audit_event
    write_audit_event(
        action="document.ocr_run", entity_type="document", entity_id=None,
        actor_user_id=actor_user_id, request_id=request_id or f"ocr-{uuid.uuid4()}",
        metadata={k: summary[k] for k in ("mode", "candidates", "completed", "failed",
                                          "skipped", "unsupported", "encrypted", "chars_extracted")})


def extract_text(document_id: int, *, extractor=None, actor_user_id=None, request_id=None) -> dict:
    """Convenience: OCR a single canonical document now (forces reprocessing of that id).

    Safe by default: with no injected ``extractor`` this runs the PRODUCTION backend under subprocess
    isolation — the same shared OCR_SUBPROCESS_ISOLATION gate + production factory ref the operational
    runner and the SharePoint live-OCR path use — so a pathological document is killed by the wall-clock
    hard cap / stall watchdog and can never wedge the caller. Passing an in-process ``extractor`` object
    selects the EXPLICIT in-process path (tests/diagnostics: a fake extractor cannot cross the spawn
    boundary); production never passes one."""
    if extractor is not None:
        # Explicit in-process path for an injected (fake) extractor — never used by production.
        return run_ocr(document_ids=[document_id], extractor=extractor, mode="reprocess",
                       actor_user_id=actor_user_id, request_id=request_id, isolate=False)
    from app.jobs.ocr_runner import _PRODUCTION_FACTORY, _isolation_enabled
    from app.services.ocr_backend import build_production_extractor
    prod = build_production_extractor()                       # availability check (raises if not installed)
    isolate = _isolation_enabled()
    return run_ocr(document_ids=[document_id], extractor=prod, mode="reprocess",
                   actor_user_id=actor_user_id, request_id=request_id,
                   isolate=isolate, factory_ref=(_PRODUCTION_FACTORY if isolate else None))


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
    # The PRODUCTION CLI must run the real OCR backend (Tesseract/Poppler via TESSERACT_CMD/POPPLER_PATH),
    # not run_ocr's default_extractor stub (which always raises OcrBackendUnavailable and would fail every
    # candidate). If the backend cannot be built, report the config error clearly and abort — never run the
    # whole batch through the stub.
    from app.services.ocr_backend import build_production_extractor
    try:
        extractor = build_production_extractor()
    except OcrBackendUnavailable as exc:
        print(f"OCR backend not available: {exc}")
        print("  Set TESSERACT_CMD and POPPLER_PATH and install the OCR libraries "
              "(pytesseract, pdf2image, pypdf, Pillow), then retry.")
        return 2
    # Production/default: run every document in a killable CHILD PROCESS with the real wall-clock hard cap
    # + stall watchdog (app.services.ocr_isolation), so a pathological PDF/image can never wedge this CLI
    # parent process. Reuses the operational runner's SINGLE isolation gate (OCR_SUBPROCESS_ISOLATION) and
    # its production factory ref — no second config knob, no duplicated timeout mechanism. The non-isolated
    # in-process path is a DIAGNOSTICS-ONLY escape hatch: it is reachable only by deliberately setting
    # OCR_SUBPROCESS_ISOLATION=0 (never by accident) and it warns loudly below.
    from app.jobs.ocr_runner import _PRODUCTION_FACTORY, _isolation_enabled
    isolate = _isolation_enabled()
    if not isolate:
        print("WARNING: OCR subprocess isolation is DISABLED via OCR_SUBPROCESS_ISOLATION=0 — DIAGNOSTICS "
              "ONLY. A pathological document can wedge this process with no wall-clock timeout. Do NOT use "
              "this mode in production.", flush=True)
    summary = run_ocr(mode=args.mode, batch_size=args.batch_size, max_attempts=args.max_attempts,
                      dry_run=args.dry_run, extractor=extractor,
                      isolate=isolate, factory_ref=(_PRODUCTION_FACTORY if isolate else None),
                      progress=lambda line: print(line, flush=True))   # live phase telemetry
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
