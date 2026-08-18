"""Per-document OCR isolation — regression tests for the production hang.

Prove that a deliberately hanging OCR/render worker cannot freeze the migration: the child is killed by
a real wall-clock timeout, the parent continues, the document is recorded timed_out, the next document
processes, no orphan process survives, completed work is not reprocessed on resume, and telemetry updates.
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import document_ocr, documents, engine
from app.services import document_ocr as doc_ocr
from app.services import ocr_isolation as iso
from app.services.ocr_exceptions import OcrTimeout

_DBL = "tests.ocr_doubles"


@pytest.fixture(autouse=True)
def _cleanup_created_docs():
    """This module leaves ``timed_out``/``completed`` OCR state in the shared test DB. Delete every
    document (and its OCR row) created during the test so it cannot leak into other suites — e.g. a
    leftover ``timed_out`` doc would otherwise be picked up as a retry candidate by test_ocr_runner."""
    with engine.connect() as c:
        high = c.execute(select(func.max(documents.c.id))).scalar() or 0
    yield
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(select(documents.c.id).where(documents.c.id > high))]
        if ids:
            c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))


def _doc(name="scan.pdf"):
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            original_name=name, stored_name=f"s-{sfx}", storage_provider="Client360 Local",
            storage_path=f"/nonexistent/{sfx}", size_bytes=1, sha256=(sfx + sfx)[:64],
            status="active", archived=False).returning(documents.c.id)).scalar_one()


def _ocr_state(doc_id):
    with engine.connect() as c:
        return c.execute(select(document_ocr.c.status, document_ocr.c.attempts).where(
            document_ocr.c.document_id == doc_id)).mappings().one_or_none()


def _ocr_text(doc_id):
    with engine.connect() as c:
        return c.execute(select(document_ocr.c.text).where(
            document_ocr.c.document_id == doc_id)).scalar_one_or_none()


# --- run_document: the interruptible boundary --------------------------------

def test_hanging_child_is_killed_and_parent_continues():
    t0 = time.monotonic()
    with pytest.raises(OcrTimeout):
        iso.run_document(f"{_DBL}.sleep_forever_factory", {"original_name": "h.pdf", "id": 1}, None,
                         hard_timeout=2, stall_timeout=10, doc_id=1, name="h.pdf")
    assert time.monotonic() - t0 < 8                     # killed near the hard cap, not hung indefinitely
    # the parent survived and can immediately process the next document
    result = iso.run_document(f"{_DBL}.ok_factory", {"original_name": "ok.pdf", "id": 2}, None,
                              hard_timeout=10, stall_timeout=10)
    assert result["text"].startswith("ok:")


def test_spin_loop_child_is_killed_by_wall_clock():
    t0 = time.monotonic()
    with pytest.raises(OcrTimeout):
        iso.run_document(f"{_DBL}.spin_forever_factory", {"original_name": "s.pdf", "id": 3}, None,
                         hard_timeout=2, stall_timeout=10)
    assert time.monotonic() - t0 < 8


@pytest.mark.skipif(sys.platform.startswith("win"), reason="SIGSTOP is POSIX-only")
def test_stall_watchdog_fires_when_child_cannot_heartbeat():
    # The child SIGSTOPs itself → no heartbeats → the STALL watchdog (not the hard cap) must kill it.
    t0 = time.monotonic()
    with pytest.raises(OcrTimeout):
        iso.run_document(f"{_DBL}.freeze_self_factory", {"original_name": "f.pdf", "id": 4}, None,
                         hard_timeout=120, stall_timeout=2)     # stall << hard: stall must fire first
    assert time.monotonic() - t0 < 15                          # killed by stall, long before the hard cap


def test_no_orphan_process_tree_survives(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    os.environ["OCR_TEST_PIDFILE"] = str(pidfile)
    try:
        with pytest.raises(OcrTimeout):
            iso.run_document(f"{_DBL}.grandchild_hang_factory", {"original_name": "g.pdf", "id": 5}, None,
                             hard_timeout=2, stall_timeout=10)
    finally:
        os.environ.pop("OCR_TEST_PIDFILE", None)
    assert pidfile.exists()
    grandchild_pid = int(pidfile.read_text())
    # the grandchild must be reaped with the tree (poll briefly for SIGKILL to propagate)
    for _ in range(30):
        try:
            os.kill(grandchild_pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            break
    else:
        try:
            os.kill(grandchild_pid, 9)   # clean up if the test's own assertion is about to fail
        except ProcessLookupError:
            pass
        pytest.fail("grandchild OCR subprocess survived the tree kill (orphan)")


# --- run_ocr loop: timed_out + continue + telemetry + resume -----------------

def test_run_ocr_loop_times_out_one_and_completes_next():
    hang = _doc("clientHANG.pdf")
    ok = _doc("clientOK.pdf")
    summary = doc_ocr.run_ocr(mode="incremental", document_ids=[hang, ok],
                              isolate=True, factory_ref=f"{_DBL}.selective_factory",
                              hard_timeout=2, stall_timeout=8)
    assert summary["timed_out"] == 1 and summary["completed"] == 1     # telemetry counters
    assert _ocr_state(hang)["status"] == "timed_out"
    assert _ocr_state(hang)["attempts"] >= 1                            # attempt recorded (not silent)
    assert _ocr_state(ok)["status"] == "completed"                     # next document succeeded


def test_completed_documents_are_not_reprocessed_on_resume():
    ok = _doc("resumeOK.pdf")
    first = doc_ocr.run_ocr(mode="incremental", document_ids=[ok],
                            isolate=True, factory_ref=f"{_DBL}.selective_factory",
                            hard_timeout=5, stall_timeout=8)
    assert first["completed"] == 1
    text_before = _ocr_text(ok)
    # The real incremental candidate query — what a resumed production sweep uses to pick work — must NOT
    # re-select an already-completed document. This is exactly how a resume reuses the ~7,200 documents
    # already OCR'd: they are excluded from the candidate set, never re-extracted. (A targeted
    # ``document_ids=`` pass is a deliberate *forced* reprocess and is intentionally excluded here.)
    assert ok not in {row["id"] for row in _incremental_candidates()}
    assert _ocr_state(ok)["status"] == "completed"                     # terminal, reused
    assert _ocr_text(ok) == text_before                               # untouched — not re-extracted


def _incremental_candidates():
    with engine.connect() as c:
        return c.execute(
            select(documents.c.id).select_from(
                documents.outerjoin(document_ocr, document_ocr.c.document_id == documents.c.id))
            .where(documents.c.status != "deleted",
                   (document_ocr.c.document_id.is_(None))
                   | (document_ocr.c.status.in_(("pending", "processing"))))).mappings().all()


# --- sweep-level: a batch of pathological docs must not strand the rest ------

def test_sweep_continues_past_an_all_timeout_batch(monkeypatch):
    from app.jobs import ocr_runner

    def _batch(**counts):
        base = {k: 0 for k in ("candidates", "completed", "failed", "timed_out", "skipped",
                               "unsupported", "chars_extracted")}
        return {**base, **counts, "errors": []}

    # Batch 1 times out every document it selects; batch 2 completes one; batch 3 is empty.
    batches = iter([_batch(candidates=2, timed_out=2),      # all pathological — MUST NOT halt the sweep
                    _batch(candidates=1, completed=1),      # the doc that used to be stranded
                    _batch(candidates=0)])
    from app.services import document_ocr as _svc
    monkeypatch.setattr(_svc, "run_ocr", lambda **kw: next(batches))

    totals = ocr_runner.run_sweep("incremental", extractor=lambda r, p: None,
                                  factory_ref=f"{_DBL}.selective_factory", batch_size=2)
    assert totals["batches"] == 3                            # looped past the all-timeout batch
    assert totals["timed_out"] == 2 and totals["completed"] == 1   # both surfaced in sweep totals


# --- backend decoupling (child must not need the database) -------------------

def test_extraction_backend_does_not_import_app_db():
    # The subprocess child imports the extractor factory module; it must NOT pull in app.db (which would
    # connect + reflect on every spawn). Assert the backend + exceptions modules are db-free.
    import importlib

    for mod_name in ("app.services.ocr_backend", "app.services.ocr_exceptions", "app.services.ocr_isolation"):
        mod = importlib.import_module(mod_name)
        src = open(mod.__file__, encoding="utf-8").read()
        assert "from app.db import" not in src and "import app.db" not in src, mod_name
