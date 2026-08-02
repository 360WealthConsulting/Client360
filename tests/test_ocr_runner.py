"""OCR operational runners (PR 5B) — sweep coverage.

Resumable batching to completion, per-batch progress, run modes (initial/incremental/retry/reprocess),
advisory-lock concurrency protection, and the backend-unavailable status. Uses an injected extractor so
no OCR libraries are required. Temp/test rows only.
"""
import uuid

import pytest
from sqlalchemy import delete, select, text

from app.db import document_ocr, documents, engine, people
from app.jobs import ocr_runner
from app.services.document_ocr import OcrBackendUnavailable

_TAG = "OCRRUN"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            doc_ids = list(c.scalars(select(documents.c.id).where(
                documents.c.original_name.like(f"%{_TAG}%"))))
            if doc_ids:
                c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(doc_ids)))
                c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            if pids:
                c.execute(delete(people).where(people.c.id.in_(pids)))
    _wipe()
    yield
    _wipe()


def _docs(n, *, ext="pdf"):
    ids = []
    with engine.begin() as c:
        for i in range(n):
            ids.append(c.execute(documents.insert().values(
                original_name=f"{_TAG} doc {i}.{ext}", stored_name=f"{_TAG}-{uuid.uuid4().hex[:8]}",
                storage_path="/x", storage_provider="Client360 Local", storage_uri="/x",
                size_bytes=10, sha256=uuid.uuid4().hex + uuid.uuid4().hex, status="active",
                archived=False).returning(documents.c.id)).scalar_one())
    return ids


def _ok(text_out="extracted"):
    return lambda row, path: {"text": text_out, "engine": "fake", "page_count": 1}


def _boom(row, path):
    raise RuntimeError("extract failed")


def _status(did):
    with engine.connect() as c:
        return c.scalar(select(document_ocr.c.status).where(document_ocr.c.document_id == did))


# --- resumable batching ------------------------------------------------------

def test_initial_sweep_processes_whole_corpus_in_batches():
    ids = _docs(5)
    seen = []
    r = ocr_runner.run_sweep("initial", extractor=_ok(), batch_size=2,
                             progress=lambda t: seen.append(t["batches"]))
    # Firm-wide sweep (shared DB may hold other pending docs) — assert OUR corpus is fully processed.
    assert r["status"] == "completed" and r["completed"] >= 5
    assert r["batches"] >= 3                                   # 5 docs / batch_size 2 → multiple batches
    assert all(_status(i) == "completed" for i in ids)


def test_incremental_skips_completed_on_rerun():
    ids = _docs(3)
    ocr_runner.run_sweep("initial", extractor=_ok(), batch_size=10)
    r = ocr_runner.run_incremental(extractor=_ok(), batch_size=10)
    # Everything is already completed → nothing to do on the incremental pass.
    assert r["completed"] == 0 and all(_status(i) == "completed" for i in ids)


def test_retry_runs_one_batch_and_recovers():
    ids = _docs(2)
    ocr_runner.run_sweep("initial", extractor=_boom, batch_size=10)   # both fail
    assert all(_status(i) == "failed" for i in ids)
    r = ocr_runner.run_retry(extractor=_ok(), batch_size=10)
    assert r["status"] == "completed" and r["completed"] == 2
    assert all(_status(i) == "completed" for i in ids)


def test_reprocess_forces_reextraction():
    ids = _docs(2)
    ocr_runner.run_sweep("initial", extractor=_ok("v1"), batch_size=10)
    # Targeted force-reprocess re-OCRs the chosen documents even though they are completed + unchanged.
    r = ocr_runner.run_reprocess(document_ids=ids, extractor=_ok("v2"), batch_size=10)
    assert r["completed"] == 2
    with engine.connect() as c:
        texts = set(c.scalars(select(document_ocr.c.text).where(
            document_ocr.c.document_id.in_(ids))))
    assert texts == {"v2"}


# --- concurrency protection --------------------------------------------------

def test_advisory_lock_prevents_concurrent_run():
    _docs(1)
    lock = engine.connect()
    lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ocr_runner._OCR_LOCK_KEY})
    try:
        r = ocr_runner.run_sweep("initial", extractor=_ok(), batch_size=10)
        assert r["status"] == "locked" and r["completed"] == 0   # another run holds the lock
    finally:
        lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ocr_runner._OCR_LOCK_KEY})
        lock.close()


def test_lock_released_after_run_allows_next_run():
    ids = _docs(1)
    ocr_runner.run_sweep("initial", extractor=_ok(), batch_size=10)
    # A subsequent run acquires the lock (it was released) and finds nothing to do.
    r = ocr_runner.run_incremental(extractor=_ok(), batch_size=10)
    assert r["status"] == "completed" and _status(ids[0]) == "completed"


# --- backend unavailable -----------------------------------------------------

def test_backend_unavailable_status(monkeypatch):
    _docs(1)

    def _raise():
        raise OcrBackendUnavailable("no engine")
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor", _raise)
    r = ocr_runner.run_sweep("initial")                        # no extractor → tries production backend
    assert r["status"] == "backend_unavailable"


def test_scheduler_registers_ocr_jobs():
    import inspect

    from app.jobs import scheduler
    src = inspect.getsource(scheduler.start_scheduler)
    assert "ocr-incremental-sweep" in src and "ocr-retry-sweep" in src
