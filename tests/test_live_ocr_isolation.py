"""Regression tests: the SharePoint baseline/resume live-OCR path (``document_owner_proposal._live_ocr``)
routes through the hardened subprocess isolation from commit 186badf.

Proves the baseline path — not just the operational runner — now gets a real per-document wall-clock
timeout: a hanging document is killed and control returns to the caller so the SharePoint
``_ocr_documents`` loop can proceed. Queue/scope/cache/reprocess semantics are unchanged.
"""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import document_ocr, documents, engine
from app.jobs import ocr_runner
from app.services import document_ocr as doc_ocr
from app.services import document_owner_proposal as dop

_DBL = "tests.ocr_doubles"
_DUMMY_EXTRACTOR = lambda: (lambda row, path: {"text": "x", "engine": "fake", "page_count": 1})  # noqa: E731


@pytest.fixture(autouse=True)
def _cleanup_created_docs():
    with engine.connect() as c:
        high = c.execute(select(func.max(documents.c.id))).scalar() or 0
    yield
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(select(documents.c.id).where(documents.c.id > high))]
        if ids:
            c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))


def _doc(name="scan.png"):
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            original_name=name, stored_name=f"s-{sfx}", storage_provider="Client360 Local",
            storage_path=f"/nonexistent/{sfx}", size_bytes=1, sha256=(sfx + sfx)[:64],
            status="active", archived=False).returning(documents.c.id)).scalar_one()


def _completed_doc(name="done.png", text="cached ocr text"):
    did = _doc(name)
    with engine.begin() as c:
        sha = c.execute(select(documents.c.sha256).where(documents.c.id == did)).scalar_one()
        c.execute(insert(document_ocr).values(
            document_id=did, status="completed", text=text, char_count=len(text),
            source_hash=sha, attempts=1))
    return did


def _ocr_row(did):
    with engine.connect() as c:
        return c.execute(select(document_ocr.c.status, document_ocr.c.attempts, document_ocr.c.text)
                         .where(document_ocr.c.document_id == did)).mappings().one_or_none()


def _incremental_candidate_ids():
    with engine.connect() as c:
        return {r[0] for r in c.execute(
            select(documents.c.id).select_from(
                documents.outerjoin(document_ocr, document_ocr.c.document_id == documents.c.id))
            .where(documents.c.status != "deleted",
                   (document_ocr.c.document_id.is_(None))
                   | (document_ocr.c.status.in_(("pending", "processing")))))}


# --- 1. live OCR reaches subprocess isolation when enabled -------------------

def test_live_ocr_reaches_isolation_with_production_factory(monkeypatch):
    monkeypatch.setenv("OCR_SUBPROCESS_ISOLATION", "1")
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor", _DUMMY_EXTRACTOR)
    captured = {}
    monkeypatch.setattr(doc_ocr, "run_ocr", lambda **kw: captured.update(kw) or {})
    with engine.connect() as conn:
        dop._live_ocr(conn, 4242)
    assert captured["isolate"] is True
    assert captured["factory_ref"] == ocr_runner._PRODUCTION_FACTORY     # reuses the runner's constant
    # Reprocess semantics + single-document scope are preserved exactly.
    assert captured["document_ids"] == [4242]
    assert captured["mode"] == "reprocess" and captured["batch_size"] == 1


# --- 5. isolation-disabled behavior stays compatible (in-process path) -------

def test_live_ocr_disabled_uses_in_process_path(monkeypatch):
    monkeypatch.setenv("OCR_SUBPROCESS_ISOLATION", "0")
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor", _DUMMY_EXTRACTOR)
    captured = {}
    monkeypatch.setattr(doc_ocr, "run_ocr", lambda **kw: captured.update(kw) or {})
    with engine.connect() as conn:
        dop._live_ocr(conn, 99)
    assert captured["isolate"] is False and captured["factory_ref"] is None
    assert captured["document_ids"] == [99] and captured["mode"] == "reprocess"


# --- 4. cached OCR is reused and never triggers live OCR ---------------------

def test_cached_ocr_is_reused_without_live_ocr(monkeypatch):
    did = _completed_doc(text="Recipient jl@x.com per prior statement")

    def _boom(*a, **k):
        raise AssertionError("_live_ocr must NOT be called when cached OCR text exists")

    monkeypatch.setattr(dop, "_live_ocr", _boom)
    with engine.connect() as conn:
        text, method = dop.extract_document_text(
            conn, {"id": did, "original_name": "done.png"}, None, ocr=True)
    assert method == "ocr_cache" and "jl@x.com" in text                 # reused from the cache, not re-OCR'd


# --- 2. a hanging document times out and control returns ---------------------

def test_live_ocr_hanging_document_times_out_and_returns(monkeypatch):
    monkeypatch.setenv("OCR_SUBPROCESS_ISOLATION", "1")
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor", _DUMMY_EXTRACTOR)
    monkeypatch.setattr(ocr_runner, "_PRODUCTION_FACTORY", f"{_DBL}.sleep_forever_factory")
    monkeypatch.setattr("app.services.ocr_isolation.default_bounds", lambda: (2, 10))   # fast hard cap
    did = _doc("scanHANG.png")

    t0 = time.monotonic()
    with engine.connect() as conn:
        result = dop._live_ocr(conn, did)
    assert result == ""                                                 # no text — killed, not hung
    assert time.monotonic() - t0 < 20                                   # returned near the hard cap
    assert _ocr_row(did)["status"] == "timed_out"                       # recorded distinctly, retryable


# --- 3. after a hang, the next document still processes ----------------------

def test_next_document_processes_after_a_hang(monkeypatch):
    monkeypatch.setenv("OCR_SUBPROCESS_ISOLATION", "1")
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor", _DUMMY_EXTRACTOR)
    monkeypatch.setattr(ocr_runner, "_PRODUCTION_FACTORY", f"{_DBL}.selective_factory")
    monkeypatch.setattr("app.services.ocr_isolation.default_bounds", lambda: (2, 10))
    hang = _doc("clientHANG.png")
    ok = _doc("clientOK.png")

    with engine.connect() as conn:
        r_hang = dop._live_ocr(conn, hang)          # hangs → killed → ""
        r_ok = dop._live_ocr(conn, ok)              # the loop proceeds and this one completes
    assert r_hang == "" and _ocr_row(hang)["status"] == "timed_out"
    assert r_ok.startswith("ok:") and _ocr_row(ok)["status"] == "completed"


# --- accidental generic run: why failed=N while existing rows are untouched --

def test_generic_failure_persists_failed_rows_but_leaves_completed_untouched():
    """Reproduce the accidental `ocr_runner --mode incremental` behavior: fresh candidates fail (each
    isolation child raises → a `failed` row is persisted) while already-completed documents are simply
    not selected as candidates, so their rows are untouched."""
    completed = _completed_doc("already.png", text="prior text")
    fresh = [_doc("a.png"), _doc("b.png"), _doc("c.png")]

    # An already-completed document is NOT an incremental candidate → the generic sweep never touches it.
    assert completed not in _incremental_candidate_ids()

    # The fresh documents, run through isolation with a failing extractor, each persist a `failed` row.
    summary = doc_ocr.run_ocr(mode="reprocess", document_ids=fresh, isolate=True,
                              factory_ref=f"{_DBL}.boom_factory", hard_timeout=5, stall_timeout=8)
    assert summary["failed"] == len(fresh)
    for did in fresh:
        row = _ocr_row(did)
        assert row["status"] == "failed" and row["attempts"] >= 1        # failures DO persist as rows

    # The pre-existing completed row is byte-for-byte untouched (status + text preserved).
    crow = _ocr_row(completed)
    assert crow["status"] == "completed" and crow["text"] == "prior text"
