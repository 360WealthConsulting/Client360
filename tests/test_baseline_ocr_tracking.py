"""Regression tests: the SharePoint baseline/resume OCR loop (``microsoft_ingestion._ocr_documents``)
publishes a persistent status/heartbeat artifact via the shared ``OcrRunTracker``.

Proves the tracker denominator is the TRUE baseline batch size (``len(document_ids)``, not a firm-wide DB
count), that it advances across cached, successful, timed-out, and subsequent documents, that the
live-OCR heartbeat is wired through the existing isolation plumbing, and that tracking is observational
only (a tracking failure never changes the OCR loop).
"""
from __future__ import annotations

from app.db import engine
from app.jobs import ocr_status
from app.services import document_ocr as doc_ocr
from app.services import document_owner_proposal as dop
from app.services import microsoft_ingestion as mi

_DUMMY_EXTRACTOR = lambda: (lambda row, path: {"text": "x"})  # noqa: E731


def _read(path):
    return ocr_status.read_status(path)


# --- true denominator + advancement across cached/success/timeout/subsequent -

def test_tracker_uses_true_baseline_denominator_and_advances(tmp_path, monkeypatch):
    path = str(tmp_path / "baseline.json")
    monkeypatch.setenv("OCR_STATUS_FILE", path)
    # A realistic mix: cached/native completed, a fresh completed, a timeout, an unsupported, then another
    # completed AFTER the timeout (proves the loop continues past a bad document).
    statuses = {201: "completed", 202: "completed", 203: "timed_out", 204: "unsupported", 205: "completed"}
    monkeypatch.setattr(mi, "_ocr_analyze", lambda did: statuses[did])
    monkeypatch.setattr(mi, "_doc_name", lambda did: f"doc{did}.pdf")

    counts = mi._ocr_documents(list(statuses))

    # Baseline counters are unchanged by tracking.
    assert counts == {"ocr_analyzed": 3, "ocr_failed": 0, "ocr_timed_out": 1, "ocr_other": 1}

    s = _read(path)
    assert s["mode"] == "sharepoint-baseline"
    assert s["total"] == 5                                   # TRUE len(document_ids), not a firm-wide count
    assert s["current_index"] == 5                           # advanced through every document
    assert s["state"] == "COMPLETED"
    assert s["counts"]["completed"] == 3 and s["counts"]["timed_out"] == 1
    assert s["counts"]["unsupported"] == 1 and s["counts"]["failed"] == 0
    # last successful OCR advanced to the final completed document (index 5), not the timeout at index 3.
    assert s["last_success_index"] == 5 and s["last_success_document_id"] == 205


def test_cached_document_advances_progress(tmp_path, monkeypatch):
    # A document whose status is already 'completed' (cache/native — no live OCR) must still advance the
    # baseline index and count as completed.
    path = str(tmp_path / "cached.json")
    monkeypatch.setenv("OCR_STATUS_FILE", path)
    monkeypatch.setattr(mi, "_ocr_analyze", lambda did: "completed")
    monkeypatch.setattr(mi, "_doc_name", lambda did: "cached.png")

    mi._ocr_documents([301])
    s = _read(path)
    assert s["current_index"] == 1 and s["counts"]["completed"] == 1 and s["state"] == "COMPLETED"


# --- live-OCR heartbeat is wired through the isolation plumbing --------------

def test_heartbeat_observer_is_published_during_the_loop(tmp_path, monkeypatch):
    path = str(tmp_path / "hb.json")
    monkeypatch.setenv("OCR_STATUS_FILE", path)
    seen = {}

    def _capture(did):
        seen[did] = doc_ocr.live_ocr_observer.get()          # what _live_ocr would forward to run_ocr
        return "completed"

    monkeypatch.setattr(mi, "_ocr_analyze", _capture)
    monkeypatch.setattr(mi, "_doc_name", lambda did: "x.pdf")

    mi._ocr_documents([401, 402])

    for did in (401, 402):
        obs = seen[did]
        assert isinstance(obs, mi._HeartbeatObserver)        # a heartbeat observer was active during the doc
        assert callable(obs.heartbeat)
    # The context var is cleared once the batch ends (no leakage to other callers).
    assert doc_ocr.live_ocr_observer.get() is None


def test_live_ocr_forwards_context_observer_to_run_ocr(monkeypatch):
    # Close the loop: _live_ocr forwards whatever observer the baseline published into run_ocr (which then
    # feeds it to the subprocess-isolation on_heartbeat — covered by test_ocr_status).
    monkeypatch.setenv("OCR_SUBPROCESS_ISOLATION", "1")
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor", _DUMMY_EXTRACTOR)
    captured = {}
    monkeypatch.setattr(doc_ocr, "run_ocr", lambda **kw: captured.update(kw) or {})

    sentinel = mi._HeartbeatObserver(tracker=None)
    token = doc_ocr.live_ocr_observer.set(sentinel)
    try:
        with engine.connect() as conn:
            dop._live_ocr(conn, 4242)
    finally:
        doc_ocr.live_ocr_observer.reset(token)
    assert captured["observer"] is sentinel
    assert captured["isolate"] is True and captured["factory_ref"]


# --- observational only: tracking failure never breaks the loop -------------

def test_tracking_failure_does_not_change_baseline_behavior(tmp_path, monkeypatch):
    class _BoomTracker:
        def __init__(self, *a, **k):
            raise RuntimeError("status backend exploded")

    monkeypatch.setattr("app.jobs.ocr_status.OcrRunTracker", _BoomTracker)
    monkeypatch.setattr(mi, "_ocr_analyze", lambda did: "completed")
    monkeypatch.setattr(mi, "_doc_name", lambda did: "x.pdf")

    counts = mi._ocr_documents([501, 502, 503])              # must not raise despite the tracker blowing up
    assert counts["ocr_analyzed"] == 3
    # And the context var must be left clean.
    assert doc_ocr.live_ocr_observer.get() is None


def test_tracking_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_STATUS_ENABLED", "0")
    monkeypatch.setattr(mi, "_ocr_analyze", lambda did: "completed")
    monkeypatch.setattr(mi, "_doc_name", lambda did: "x.pdf")
    assert mi._new_ocr_tracker(3) is None                    # disabled → no tracker
    counts = mi._ocr_documents([601])
    assert counts["ocr_analyzed"] == 1                       # loop still works with tracking off
