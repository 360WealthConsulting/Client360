"""The four stages, each a thin orchestration over a service that already exists.

    extract  ->  ocr  ->  classify  ->  ownership

Nothing here re-implements extraction, OCR, classification, matching or the ownership rules. Every
stage delegates, and what this module contributes is the ROUTING between them and the honest
classification of what went wrong:

* ``extract`` pulls embedded text (PDF text layer, Excel, Word, plaintext) through
  ``document_owner_proposal.extract_document_text`` and caches it as a terminal OCR state. When it
  succeeds it SKIPS the OCR stage entirely — which is the single biggest saving in the pipeline,
  because most of a firm's corpus is born-digital and has never needed OCR.
* ``ocr`` first looks for a byte-identical document whose text is already extracted (SHA-256 dedupe)
  and copies it; only a genuinely new set of bytes reaches the engine. It also DEFERS while one of the
  existing operational OCR sweeps holds the corpus advisory lock, so the continuous pipeline can never
  fight the migration runner over the same documents.
* ``classify`` runs the existing analysis pipeline (``document_pipeline.analyze_and_persist``), which
  persists the document type, the year and a NON-AUTHORITATIVE owner proposal exactly the way the
  Knowledge pipeline already does.
* ``ownership`` applies the three lanes (see :mod:`.ownership`) and is the only stage that can write
  an owner — through the one canonical write path, which refuses to overwrite.

Every stage returns a :class:`StageResult`. A stage NEVER writes queue state: the worker owns the task
row, and keeping that in one place is why a stage can be tested without a queue at all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from app.db import documents
from app.services.document_pipeline_continuous import backpressure, ownership, queue
from app.services.document_pipeline_continuous.model import (
    OUTCOME_UNRESOLVED,
    STAGE_CLASSIFY,
    STAGE_DONE,
    STAGE_EXTRACT,
    STAGE_OCR,
    STAGE_OWNERSHIP,
    PipelinePermanentError,
    PipelineTransientError,
)

log = logging.getLogger(__name__)

#: Extraction methods that produced real, usable text without the OCR engine.
_NATIVE_METHODS = {"excel", "xls", "pdf_text", "plaintext", "docx", "ics", "eml"}
#: Extraction methods meaning "the text is already in document_ocr" — nothing more to do.
_CACHED_METHODS = {"ocr_cache", "ocr"}
#: Extraction methods meaning "there is no text yet, and OCR is the way to get it".
_NEEDS_OCR_METHODS = {"pdf_no_text", "image_no_text"}
#: Below this many characters, "extracted text" is a page number and a scanning artefact.
MIN_USABLE_CHARS = 20


@dataclass
class StageResult:
    """What a stage concluded. ``next_stage`` is the routing decision; ``outcome`` is set only by the
    final stage."""
    next_stage: str
    note: str | None = None
    outcome: str | None = None
    detail: dict = field(default_factory=dict)


def _document_row(conn, document_id: int):
    return conn.execute(
        select(documents.c.id, documents.c.original_name, documents.c.status, documents.c.sha256,
               documents.c.storage_uri, documents.c.storage_path, documents.c.content_type,
               documents.c.tags, documents.c.person_id, documents.c.household_id,
               documents.c.organization_id, documents.c.category, documents.c.classification,
               documents.c.subcategory)
        .where(documents.c.id == document_id)).mappings().first()


def _source_path(row) -> Path | None:
    """Resolve a document's bytes on disk, using the same precedence as the proposal engine."""
    if row["storage_uri"] and Path(row["storage_uri"]).is_absolute():
        return Path(row["storage_uri"])
    if row["storage_path"]:
        return Path(row["storage_path"])
    return None


# --- extract -------------------------------------------------------------------------------------

def run_extract(conn, task) -> StageResult:
    """Extract embedded text. Routes past the OCR stage whenever the document already has text."""
    document_id = int(task["document_id"])
    row = _document_row(conn, document_id)
    if row is None:
        raise PipelinePermanentError("document_missing", f"document {document_id} no longer exists")
    if (row["status"] or "") == "deleted":
        raise PipelinePermanentError("document_deleted",
                                     f"document {document_id} was deleted after it was queued")

    from app.services.document_owner_proposal import extract_document_text

    path = _source_path(row)
    try:
        # ocr=False: this stage is EMBEDDED text only. Sending it to the engine here would make the
        # OCR stage — and its dedupe, its backpressure and its deferral to the migration sweep —
        # unreachable, which is a lot of safety to lose for one saved function call.
        text, method = extract_document_text(conn, row, path, ocr=False)
    except (OSError, PermissionError) as exc:
        raise PipelineTransientError(f"could not read document {document_id}: {exc}") from exc

    from app.services import document_ocr

    if method in _CACHED_METHODS:
        return StageResult(STAGE_CLASSIFY, note=f"text already cached ({method})",
                           detail={"method": method, "chars": len(text or "")})

    if method in _NATIVE_METHODS and len((text or "").strip()) >= MIN_USABLE_CHARS:
        document_ocr.record_extracted_text(document_id, text=text, engine_name=f"embedded:{method}",
                                           source_hash=row["sha256"])
        return StageResult(STAGE_CLASSIFY, note=f"embedded text extracted ({method})",
                           detail={"method": method, "chars": len((text or "").strip())})

    if method in _NEEDS_OCR_METHODS or document_ocr.is_ocr_supported(row["original_name"]):
        return StageResult(STAGE_OCR, note=f"no embedded text ({method})",
                           detail={"method": method})

    # Not an OCR-capable type and nothing native came out: a container or a binary with no text in it.
    # Terminal for extraction, but the document still has a filename and a folder, and those are
    # evidence — so it continues to classification rather than becoming a blocker.
    document_ocr.record_not_extractable(document_id, reason=f"no extractable text ({method})",
                                        source_hash=row["sha256"])
    return StageResult(STAGE_CLASSIFY, note=f"no extractable text ({method})",
                       detail={"method": method})


# --- ocr -----------------------------------------------------------------------------------------

@dataclass
class OcrPlan:
    """What the OCR stage decided to do, before it does the slow part.

    The stage is split in three (:func:`plan_ocr`, :func:`execute_ocr`, :func:`settle_ocr`) for one
    reason: OCR of a large scan takes minutes, and a database transaction held open across it is an
    idle-in-transaction connection that blocks vacuum and consumes pool capacity staff need. Planning
    and settling are short transactions; execution holds no connection at all."""
    action: str                     # 'reuse' | 'defer' | 'run'
    document_id: int
    sha256: str | None = None
    reused: dict | None = None


def plan_ocr(conn, task, *, defer_to_legacy_sweep: bool = True) -> OcrPlan:
    """Decide, in a short transaction, whether this document needs the OCR engine at all."""
    document_id = int(task["document_id"])
    row = _document_row(conn, document_id)
    if row is None:
        raise PipelinePermanentError("document_missing", f"document {document_id} no longer exists")

    # Deduplicate by content hash BEFORE touching the engine. Same bytes can only produce the same
    # text, so running OCR again would spend minutes recomputing a value already stored.
    reused = queue.completed_document_with_text(conn, row["sha256"], exclude_document_id=document_id)
    if reused is not None:
        return OcrPlan("reuse", document_id, sha256=row["sha256"], reused=reused)

    if defer_to_legacy_sweep and backpressure.legacy_ocr_sweep_active(conn):
        # A migration-scale OCR sweep is running. It takes no per-document lease, so the only way not
        # to collide with it is not to run. Deferring costs a retry delay; colliding costs duplicated
        # work on a machine that is already saturated.
        return OcrPlan("defer", document_id, sha256=row["sha256"])

    return OcrPlan("run", document_id, sha256=row["sha256"])


def execute_ocr(plan: OcrPlan, *, extractor=None, factory_ref=None, isolate=None) -> dict:
    """Run the OCR engine for one document. Holds NO database connection — see :class:`OcrPlan`."""
    return _invoke_ocr(plan.document_id, extractor=extractor, factory_ref=factory_ref,
                       isolate=isolate)


def settle_ocr(conn, plan: OcrPlan, summary: dict | None) -> StageResult:
    """Turn the engine's summary (or the plan's shortcut) into a stage result and a cached text row."""
    from app.services import document_ocr

    if plan.action == "reuse":
        reused = plan.reused or {}
        document_ocr.record_extracted_text(
            plan.document_id, text=reused.get("text"),
            engine_name=f"reused:{reused.get('document_id')}", source_hash=plan.sha256,
            page_count=reused.get("page_count"))
        return StageResult(STAGE_CLASSIFY,
                           note=f"reused OCR from document {reused.get('document_id')}",
                           detail={"reused_from": reused.get("document_id"),
                                   "chars": int(reused.get("char_count") or 0)})

    if plan.action == "defer":
        raise PipelineTransientError("an operational OCR sweep holds the corpus lock; deferring")

    summary = summary or {}
    if summary.get("status") == "backend_unavailable":
        # The engine is not installed on this host. Genuinely transient: it becomes available the
        # moment somebody installs it, and the document is still perfectly processable.
        document_ocr.record_ocr_unavailable(plan.document_id,
                                            summary.get("error") or "backend unavailable")
        raise PipelineTransientError(f"OCR backend unavailable: {summary.get('error')}")
    if summary.get("encrypted"):
        raise PipelinePermanentError("encrypted_document",
                                     "the document is password-protected and cannot be read")
    if summary.get("unsupported"):
        return StageResult(STAGE_CLASSIFY, note="OCR does not apply to this file type",
                           detail={"ocr": "unsupported"})
    if summary.get("timed_out"):
        raise PipelineTransientError("OCR timed out on this document")
    if summary.get("failed") or summary.get("errors"):
        raise PipelineTransientError(f"OCR failed: {(summary.get('errors') or ['unknown'])[0]}")
    return StageResult(STAGE_CLASSIFY, note="OCR completed",
                       detail={"chars": int(summary.get("chars_extracted") or 0)})


def run_ocr_stage(conn, task, *, extractor=None, factory_ref=None, isolate=None,
                  defer_to_legacy_sweep: bool = True) -> StageResult:
    """The three OCR phases composed against one connection.

    Correct, and convenient for tests and one-off calls; the worker uses the three phases separately
    so it holds no transaction while the engine runs."""
    plan = plan_ocr(conn, task, defer_to_legacy_sweep=defer_to_legacy_sweep)
    summary = (execute_ocr(plan, extractor=extractor, factory_ref=factory_ref, isolate=isolate)
               if plan.action == "run" else None)
    return settle_ocr(conn, plan, summary)


def _invoke_ocr(document_id, *, extractor=None, factory_ref=None, isolate=None) -> dict:
    """Call the existing OCR service for exactly one document.

    Isolation is fail-closed in ``document_ocr.run_ocr``: production must pass a picklable factory
    reference so extraction happens in a killable child process. Tests pass an in-process extractor
    with ``isolate=False``. This helper makes that choice explicit rather than letting it default."""
    from app.jobs.ocr_runner import _PRODUCTION_FACTORY, _isolation_enabled
    from app.services import document_ocr
    from app.services.document_ocr import OcrBackendUnavailable

    if extractor is None:
        try:
            from app.services.ocr_backend import build_production_extractor
            extractor = build_production_extractor()
        except OcrBackendUnavailable as exc:
            return {"status": "backend_unavailable", "error": str(exc)}
        if factory_ref is None and (isolate is None or isolate):
            factory_ref = _PRODUCTION_FACTORY
        if isolate is None:
            isolate = _isolation_enabled()
    elif isolate is None:
        isolate = factory_ref is not None

    return document_ocr.run_ocr(document_ids=[int(document_id)], mode="incremental",
                                extractor=extractor, batch_size=1,
                                request_id="document-pipeline-ocr",
                                isolate=bool(isolate and factory_ref), factory_ref=factory_ref)


# --- classify ------------------------------------------------------------------------------------

def run_classify(conn, task, *, idx=None) -> StageResult:
    """Classify the document and persist a non-authoritative owner proposal.

    Delegates wholesale to ``document_pipeline.analyze_and_persist``, which is SAVEPOINT-isolated and
    writes the document type plus a versioned ``owner_proposal`` fact. It never writes ownership — the
    ownership stage does that, through a different path, under different rules."""
    document_id = int(task["document_id"])
    from app.services.document_pipeline import analyze_and_persist

    result = analyze_and_persist(document_id, conn=conn, idx=idx, ocr=False)
    if result is None:
        # analyze_and_persist swallowed a failure and recorded an ERROR proposal. Retrying is worth
        # one round: the common causes (a file briefly locked by a sync client) clear on their own.
        raise PipelineTransientError(f"analysis failed for document {document_id}")
    return StageResult(STAGE_OWNERSHIP, note=f"classified as {result.get('doc_type') or 'unknown'}",
                       detail={"doc_type": result.get("doc_type"), "year": result.get("year"),
                               "route": result.get("route")})


# --- ownership -----------------------------------------------------------------------------------

def run_ownership(conn, task, *, actor_user_id=None, request_id=None) -> StageResult:
    """Apply the ownership lanes. The only stage that can change what the firm believes."""
    document_id = int(task["document_id"])
    from app.services.document_pipeline import proposal_for_document

    proposal = proposal_for_document(document_id) or {}
    verdict = ownership.resolve(conn, document_id, proposal=proposal, actor_user_id=actor_user_id,
                                request_id=request_id)
    return StageResult(STAGE_DONE, note=f"{verdict.get('outcome')} via {verdict.get('lane')}",
                       outcome=verdict.get("outcome") or OUTCOME_UNRESOLVED, detail=verdict)


# --- dispatch ------------------------------------------------------------------------------------

#: Stage name -> executor. The worker walks this table; adding a stage is adding an entry and a
#: ``next_stage``, not editing a loop.
EXECUTORS = {
    STAGE_EXTRACT: run_extract,
    STAGE_OCR: run_ocr_stage,
    STAGE_CLASSIFY: run_classify,
    STAGE_OWNERSHIP: run_ownership,
}


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Map an exception to ``(error_class, reason_code)``.

    The distinction that matters: a ``permanent`` failure leaves the queue for the blocker queue on the
    FIRST occurrence, because retrying an encrypted PDF or a deleted file learns nothing. Everything
    unrecognised is treated as transient — the conservative choice, since a wrongly-transient failure
    costs a few retries and a wrongly-permanent one silently drops a document."""
    from app.services.ocr_exceptions import OcrEncryptedPdf, OcrTimeout

    if isinstance(exc, PipelinePermanentError):
        return ("permanent", exc.reason_code)
    if isinstance(exc, OcrEncryptedPdf):
        return ("permanent", "encrypted_document")
    if isinstance(exc, FileNotFoundError):
        return ("permanent", "source_file_missing")
    if isinstance(exc, OcrTimeout):
        return ("transient", "ocr_timeout")
    if isinstance(exc, PipelineTransientError):
        return ("transient", "transient")
    if isinstance(exc, (PermissionError, OSError)):
        return ("transient", "io_error")
    return ("transient", exc.__class__.__name__)


def run_stage(conn, task, **kwargs) -> StageResult:
    """Execute the task's current stage. Unknown stages are a programming error, not a data error."""
    stage = task["stage"]
    executor = EXECUTORS.get(stage)
    if executor is None:
        raise PipelinePermanentError("unknown_stage", f"no executor for stage {stage!r}")
    accepted = {"run_ocr_stage": ("extractor", "factory_ref", "isolate", "defer_to_legacy_sweep"),
                "run_classify": ("idx",),
                "run_ownership": ("actor_user_id", "request_id")}.get(executor.__name__, ())
    return executor(conn, task, **{k: v for k, v in kwargs.items() if k in accepted})


def first_stage_of(task) -> str:
    """A freshly-discovered task has not started any stage yet; its first real stage is extraction."""
    return STAGE_EXTRACT if task["stage"] in (None, "discovered") else task["stage"]


__all__ = ["EXECUTORS", "MIN_USABLE_CHARS", "OcrPlan", "StageResult", "classify_error",
           "execute_ocr", "first_stage_of", "plan_ocr", "run_classify", "run_extract",
           "run_ocr_stage", "run_ownership", "run_stage", "settle_ocr"]
