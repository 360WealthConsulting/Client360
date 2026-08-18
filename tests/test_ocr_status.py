"""Regression tests for the OCR run-status / heartbeat tracker (operational observability).

Covers the behaviors an operator relies on when resuming the migration: the heartbeat advances between
AND during documents (so "working" is distinguishable from "frozen"), timeouts and successes advance the
right counters/markers, the status file is written atomically (a crash never leaves a torn file), and a
frozen runner is detectable via a stale heartbeat.
"""
from __future__ import annotations

import glob
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import document_ocr, documents, engine
from app.jobs import ocr_status
from app.jobs.ocr_status import COMPLETED, FAILED, RUNNING, OcrRunTracker
from app.services import document_ocr as doc_ocr

_DBL = "tests.ocr_doubles"
_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class Clock:
    """Deterministic injectable clock."""

    def __init__(self, start=_T0):
        self.t = start

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t = self.t + timedelta(seconds=seconds)
        return self.t


def _tracker(tmp_path, clock, *, total=None):
    return OcrRunTracker(str(tmp_path / "ocr_run_status.json"),
                         run_id="run-test", mode="incremental", total=total, clock=clock,
                         pid=4242, host="test-host")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --- atomic writes -----------------------------------------------------------

def test_status_write_is_valid_json_and_leaves_no_temp_files(tmp_path):
    clock = Clock()
    t = _tracker(tmp_path, clock)
    t.start()
    clock.tick(1)
    t.heartbeat()
    status = _read(t.path)
    assert status["run_id"] == "run-test" and status["state"] == "STARTING"
    # No half-written temp artifacts left behind by the atomic publish.
    assert glob.glob(str(tmp_path / ".ocr_status-*.tmp")) == []


def test_crash_during_publish_never_tears_the_file(tmp_path, monkeypatch):
    clock = Clock()
    t = _tracker(tmp_path, clock)
    t.start()                      # writes a complete v1
    v1 = _read(t.path)

    def boom(src, dst):
        raise OSError("simulated crash during os.replace")

    monkeypatch.setattr(os, "replace", boom)
    clock.tick(5)
    t.on_document_start(7959, "Big.pdf")     # publish fails internally; _safe_write swallows it
    # The reader still sees the intact previous version — never a torn/partial file.
    assert _read(t.path) == v1
    # And the failed write cleaned up its temp file rather than orphaning it.
    assert glob.glob(str(tmp_path / ".ocr_status-*.tmp")) == []


def test_atomic_write_raises_and_cleans_temp_when_replace_fails(tmp_path, monkeypatch):
    # Directly exercise the low-level writer: it must surface the error (so _safe_write can swallow) and
    # never leave a temp file behind.
    clock = Clock()
    t = _tracker(tmp_path, clock)
    monkeypatch.setattr(os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(OSError):
        t._atomic_write({"x": 1})
    assert glob.glob(str(tmp_path / ".ocr_status-*.tmp")) == []


# --- heartbeat advancement (between and during documents) --------------------

def test_heartbeat_advances_between_calls(tmp_path):
    clock = Clock()
    t = _tracker(tmp_path, clock)
    t.start()
    first = _read(t.path)["last_heartbeat"]
    clock.tick(30)
    t.heartbeat()
    second = _read(t.path)
    assert second["last_heartbeat"] != first
    assert second["elapsed_seconds"] == pytest.approx(30.0)


def test_heartbeat_advances_during_a_single_long_document(tmp_path):
    # The whole point: while ONE document is being OCR'd, the heartbeat keeps advancing but the current
    # document does not change → "OCR is working", not "runner frozen".
    clock = Clock()
    t = _tracker(tmp_path, clock)
    t.start()
    t.mark_running()
    t.on_document_start(7959, "Pathological.pdf")
    beats = []
    for _ in range(4):
        clock.tick(5)
        t.heartbeat()               # driven in production by the isolation worker's heartbeats
        s = _read(t.path)
        beats.append(s["last_heartbeat"])
        assert s["current_document_id"] == 7959          # same document throughout
        assert s["state"] == RUNNING
    assert len(set(beats)) == 4                           # every heartbeat advanced the timestamp


# --- successful / timeout / reused advancement -------------------------------

def test_successful_document_advances_last_success(tmp_path):
    clock = Clock()
    t = _tracker(tmp_path, clock)
    t.start()
    t.on_document_start(101, "a.pdf")
    clock.tick(2)
    t.on_document_result(101, "a.pdf", "completed")
    s = _read(t.path)
    assert s["counts"]["completed"] == 1
    assert s["current_index"] == 1
    assert s["last_success_document_id"] == 101 and s["last_success_index"] == 1


def test_timeout_advances_index_but_not_last_success(tmp_path):
    clock = Clock()
    t = _tracker(tmp_path, clock)
    t.start()
    t.on_document_start(101, "a.pdf")
    t.on_document_result(101, "a.pdf", "completed")     # a good one first
    t.on_document_start(102, "hang.pdf")
    t.on_document_result(102, "hang.pdf", "timed_out")
    s = _read(t.path)
    assert s["counts"]["timed_out"] == 1 and s["counts"]["completed"] == 1
    assert s["current_index"] == 2                        # position advanced past the bad doc
    assert s["last_success_document_id"] == 101 and s["last_success_index"] == 1   # unchanged by timeout


def test_reused_outcome_maps_to_skipped(tmp_path):
    clock = Clock()
    t = _tracker(tmp_path, clock)
    t.start()
    t.on_document_start(103, "done.pdf")
    t.on_document_result(103, "done.pdf", "reused")      # run_ocr's word for an already-completed doc
    assert _read(t.path)["counts"]["skipped"] == 1


# --- stale-heartbeat detection -----------------------------------------------

def test_stale_heartbeat_detection_and_formatting(tmp_path):
    clock = Clock()
    t = _tracker(tmp_path, clock)
    t.start()
    t.mark_running()
    t.on_document_start(7959, "Frozen.pdf")
    status = _read(t.path)

    fresh = _T0 + timedelta(seconds=10)
    frozen = _T0 + timedelta(seconds=600)
    assert ocr_status.is_stale(status, threshold_seconds=90, now=fresh) is False
    assert ocr_status.is_stale(status, threshold_seconds=90, now=frozen) is True
    assert "STALE" in ocr_status.format_status(status, now=frozen, threshold_seconds=90)
    assert "live" in ocr_status.format_status(status, now=fresh, threshold_seconds=90)


def test_finished_run_is_never_stale(tmp_path):
    clock = Clock()
    t = _tracker(tmp_path, clock)
    t.start()
    t.finish(COMPLETED)
    status = _read(t.path)
    way_later = _T0 + timedelta(hours=5)
    assert ocr_status.is_stale(status, threshold_seconds=90, now=way_later) is False


def test_read_missing_status_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        ocr_status.read_status(str(tmp_path / "nope.json"))


# --- plumbing into the isolation worker: heartbeat fires DURING a document ---

def test_isolation_worker_heartbeat_reaches_the_callback():
    from app.services import ocr_isolation

    beats = []
    result = ocr_isolation.run_document(
        f"{_DBL}.slow_ok_factory", {"original_name": "slow.pdf", "id": 9}, None,
        hard_timeout=30, stall_timeout=10, heartbeat_interval=0.1,
        on_heartbeat=lambda: beats.append(1))
    assert result["text"].startswith("ok:")              # document completed normally
    assert len(beats) >= 1                                # heartbeat fired while the document was running


# --- end-to-end: run_sweep drives the tracker over real documents ------------

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


def _doc(name="scan.pdf"):
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            original_name=name, stored_name=f"s-{sfx}", storage_provider="Client360 Local",
            storage_path=f"/nonexistent/{sfx}", size_bytes=1, sha256=(sfx + sfx)[:64],
            status="active", archived=False).returning(documents.c.id)).scalar_one()


def test_count_candidates_matches_selected_batch():
    ids = [_doc() for _ in range(3)]
    # Fresh docs (no OCR row) are all incremental candidates; the read-only count must equal the selection.
    assert doc_ocr.count_candidates(mode="incremental", document_ids=ids) == 3


def test_run_sweep_writes_completed_status_with_counts(tmp_path):
    from app.jobs import ocr_runner

    ids = [_doc("swA.pdf"), _doc("swB.pdf")]
    clock = Clock()
    path = str(tmp_path / "sweep_status.json")
    totals = ocr_runner.run_sweep("incremental", document_ids=ids,
                                  extractor=lambda row, p: {"text": "x", "engine": "fake", "page_count": 1},
                                  status_path=path, clock=clock)
    assert totals["status"] == "completed"
    status = _read(path)
    assert status["state"] == COMPLETED
    assert status["counts"]["completed"] == 2
    assert status["current_index"] == 2 and status["total"] == 2
    assert status["last_success_index"] == 2
    assert status["run_id"] == totals["run_id"]


def test_run_sweep_records_failed_state_on_runner_crash(tmp_path, monkeypatch):
    from app.jobs import ocr_runner

    ids = [_doc("crashA.pdf")]
    path = str(tmp_path / "crash_status.json")

    def boom(**kwargs):
        raise RuntimeError("simulated runner crash")

    monkeypatch.setattr(doc_ocr, "run_ocr", boom)
    with pytest.raises(RuntimeError):
        ocr_runner.run_sweep("incremental", document_ids=ids,
                             extractor=lambda row, p: None, status_path=path, clock=Clock())
    status = _read(path)
    assert status["state"] == FAILED and "simulated runner crash" in status["last_error"]
