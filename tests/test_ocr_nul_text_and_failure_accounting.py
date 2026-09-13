"""OCR: NUL-bearing extracted text, and failures that must never look like an empty success.

Regression cover for a production stall on 360SRV. Extraction worked perfectly — 41,119 characters
out of a scanned return — but four of those characters were NUL, PostgreSQL ``text`` cannot store
U+0000, and the driver refused the parameter outright. The write raised before any row existed, the
per-document handler swallowed the exception without incrementing a counter, and the sweep reported
``completed=0 failed=0`` on a full batch. The corpus runner therefore logged clean zero-work batches
for hours: no row was ever written, so every document kept its un-attempted state and was re-selected,
re-OCR'd and re-rejected on the next pass, forever.

Three separate defects, covered here in that order:
  * NUL in extracted text (and in error text) must not make a document unwritable;
  * a failure that escapes the per-document path must be COUNTED, not just labelled;
  * a batch that recorded nothing must stop the sweep rather than loop on the same documents.

The last group covers the spawned-process contract these paths depend on.
"""
import logging
import multiprocessing as mp
import sys
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.db import document_ocr, documents, engine
from app.jobs import ocr_parallel, ocr_runner
from app.services import document_ocr as ocr

_TAG = "OCRNULTEST"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            ids = list(c.scalars(select(documents.c.id).where(
                documents.c.original_name.like(f"%{_TAG}%"))))
            if ids:
                c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(ids)))
                c.execute(delete(documents).where(documents.c.id.in_(ids)))
    _wipe()
    yield
    _wipe()


def _doc(name=f"scan {_TAG}.pdf", *, sha=None):
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=name, stored_name=f"{name}-{uuid.uuid4().hex[:8]}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/scan",
            size_bytes=10, sha256=sha or (uuid.uuid4().hex + uuid.uuid4().hex),
            status="active", archived=False).returning(documents.c.id)).scalar_one()


def _row(doc_id):
    with engine.connect() as c:
        return c.execute(select(document_ocr).where(
            document_ocr.c.document_id == doc_id)).mappings().first()


def _text(msg):
    return lambda row, path: msg


# --- NUL in extracted text ---------------------------------------------------------------

def test_nul_in_extracted_text_is_stripped_and_the_document_completes():
    """The production failure, reduced: good text with a few NULs in it."""
    did = _doc(sha="b" * 64)
    extracted = "Form 1040 line 1 wages\x00 and more\x00 text\x00\x00"
    s = ocr.run_ocr(isolate=False, document_ids=[did], extractor=_text(extracted))

    assert s["completed"] == 1, f"expected the document to complete, got {s}"
    assert s["failed"] == 0 and s["failed_unrecorded"] == 0
    row = _row(did)
    assert row is not None, "a row must exist — the old code wrote nothing at all"
    assert "\x00" not in row["text"]
    assert row["text"] == "Form 1040 line 1 wages and more text"
    assert row["char_count"] == len("Form 1040 line 1 wages and more text")
    assert row["status"] == "completed"


def test_a_document_that_is_only_nul_still_records_a_terminal_state():
    did = _doc(sha="c" * 64)
    s = ocr.run_ocr(isolate=False, document_ids=[did], extractor=_text("\x00\x00\x00"))
    assert s["failed_unrecorded"] == 0
    assert _row(did) is not None, "a pathological document must still leave the backlog"


def test_nul_bearing_write_is_idempotent_and_writes_exactly_one_row():
    did = _doc(sha="d" * 64)
    ocr.run_ocr(isolate=False, document_ids=[did], extractor=_text("page one\x00"))
    ocr.run_ocr(isolate=False, document_ids=[did], extractor=_text("page one\x00"))
    with engine.connect() as c:
        n = c.scalar(select(func.count()).select_from(document_ocr).where(
            document_ocr.c.document_id == did))
    assert n == 1, "the upsert must stay idempotent (uq_document_ocr_document)"
    assert _row(did)["text"] == "page one"


def test_nul_in_error_text_does_not_block_the_failure_row():
    """A failure must be recordable even when the error message itself carries a NUL."""
    did = _doc(sha="e" * 64)

    def _boom(row, path):
        raise RuntimeError("engine crashed\x00 at page 2")

    s = ocr.run_ocr(isolate=False, document_ids=[did], extractor=_boom)
    assert s["failed"] == 1
    row = _row(did)
    assert row is not None and row["status"] == "failed"
    assert "\x00" not in (row["last_error"] or "")


def test_pg_text_leaves_clean_values_untouched():
    assert ocr._pg_text("already clean") == "already clean"
    assert ocr._pg_text(None) is None
    assert ocr._pg_text("") == ""


# --- a failure must be counted, not merely labelled --------------------------------------

def test_write_failure_escaping_the_document_path_is_counted_and_logged(monkeypatch, caplog):
    """The concealment defect: every document raising must not read as an idle batch."""
    did = _doc(sha="f" * 64)

    def _explode(*a, **kw):
        raise RuntimeError("A string literal cannot contain NUL (0x00) characters.")

    monkeypatch.setattr(ocr, "_write_state", _explode)
    with caplog.at_level(logging.ERROR, logger="app.services.document_ocr"):
        s = ocr.run_ocr(isolate=False, document_ids=[did], extractor=_text("hello"))

    assert s["failed"] == 1, "the failure must increment the counter, not just the outcome label"
    assert s["failed_unrecorded"] == 1, "no row was written, and the summary must say so"
    assert s["errors"] and str(did) in s["errors"][0]
    assert s["status"] == "completed_with_errors"
    logged = [r.getMessage() for r in caplog.records]
    assert any("no document_ocr row was written" in m and str(did) in m for m in logged), \
        f"the operator needs one actionable line naming the document; got {logged}"
    assert _row(did) is None, "precondition: this failure genuinely wrote nothing"


def test_a_fully_failing_batch_is_never_reported_as_a_clean_zero_work_run(monkeypatch):
    dids = [_doc(sha=f"{i:064d}") for i in range(3)]
    monkeypatch.setattr(ocr, "_write_state",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("write refused")))
    s = ocr.run_ocr(isolate=False, document_ids=dids, extractor=_text("x"))
    assert (s["completed"], s["failed"], s["failed_unrecorded"]) == (0, 3, 3)
    assert s["status"] != "completed"


# --- the sweep must stop rather than loop on a batch that recorded nothing ----------------

def test_sweep_stops_when_a_batch_records_nothing(monkeypatch):
    """Counting the failure must not turn the old silent stall into a hot retry loop."""
    calls = []

    def _fake_run_ocr(**kw):
        calls.append(kw)
        return {"candidates": 50, "completed": 0, "failed": 50, "failed_unrecorded": 50,
                "timed_out": 0, "skipped": 0, "unsupported": 0, "encrypted": 0,
                "chars_extracted": 0, "errors": ["doc 1: write refused"] * 50,
                "status": "completed_with_errors"}

    monkeypatch.setattr(ocr, "run_ocr", _fake_run_ocr)   # run_sweep imports the module, not the fn
    monkeypatch.setattr(ocr_runner, "_isolation_enabled", lambda: False)
    out = ocr_runner.run_sweep(mode="initial", document_ids=[1, 2, 3], loop=True,
                               max_batches=25, status=False, extractor=_text("x"))

    assert len(calls) == 1, f"the sweep must stop after one unproductive batch, ran {len(calls)}"
    assert out["failed"] == 50 and out["failed_unrecorded"] == 50
    assert out["status"] == "completed_with_errors"


def test_sweep_keeps_looping_while_real_progress_is_recorded(monkeypatch):
    """The stop must be specific to unrecorded failures — genuine failures still advance a run."""
    seq = [
        {"candidates": 50, "completed": 10, "failed": 40, "failed_unrecorded": 0},
        {"candidates": 0, "completed": 0, "failed": 0, "failed_unrecorded": 0},
    ]

    def _fake_run_ocr(**kw):
        base = {"timed_out": 0, "skipped": 0, "unsupported": 0, "encrypted": 0,
                "chars_extracted": 0, "errors": [], "status": "completed"}
        return {**base, **seq.pop(0)}

    monkeypatch.setattr(ocr, "run_ocr", _fake_run_ocr)   # run_sweep imports the module, not the fn
    monkeypatch.setattr(ocr_runner, "_isolation_enabled", lambda: False)
    out = ocr_runner.run_sweep(mode="initial", loop=True, max_batches=25, status=False,
                               extractor=_text("x"))
    assert out["batches"] == 2 and out["completed"] == 10


# --- the spawned-process contract these paths depend on -----------------------------------

def _child_report(q):
    """Runs in a SPAWNED child. Reports the environment the child actually got."""
    import importlib
    try:
        mod = importlib.import_module("app.services.ocr_backend")
        factory = bool(getattr(mod, "build_production_extractor", None))
    except Exception as exc:  # noqa: BLE001 — the value under test
        factory = f"{type(exc).__name__}: {exc}"
    q.put({"executable": sys.executable, "prefix": sys.prefix, "factory": factory})


def _child_report_sanitizer(q):
    """Runs in a SPAWNED child: the NUL guard must exist in the child's import of the module."""
    from app.services import document_ocr as child_ocr
    q.put(child_ocr._pg_text("a\x00b"))


def _spawn(target, timeout=120):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=target, args=(q,), daemon=False)
    p.start()
    try:
        return q.get(timeout=timeout)
    finally:
        if p.is_alive():
            p.terminate()
        p.join(30)
        q.close()


def test_spawned_child_runs_the_same_interpreter_and_environment_as_the_parent():
    """A child in a DIFFERENT environment silently loses the OCR stack, which is exactly the
    failure mode that was mistaken for this outage. Pin the invariant so it cannot appear."""
    got = _spawn(_child_report)
    assert got["executable"] == sys.executable
    assert got["prefix"] == sys.prefix


def test_the_production_ocr_factory_imports_inside_a_spawned_child():
    got = _spawn(_child_report)
    assert got["factory"] is True, f"the child could not import the OCR backend: {got['factory']}"


def test_the_nul_guard_is_present_in_the_spawned_child():
    assert _spawn(_child_report_sanitizer) == "ab"


def test_isolation_and_parallel_paths_agree_on_the_spawn_context():
    """Legacy (per-document isolation) and supervisor (per-worker) must spawn the same way, or a
    fix applied to one path silently misses the other."""
    import inspect

    from app.services import ocr_isolation
    assert 'get_context("spawn")' in inspect.getsource(ocr_isolation.run_document)
    assert 'get_context("spawn")' in inspect.getsource(ocr_parallel.run_parallel)
    assert ocr_parallel.mp.get_context("spawn").get_start_method() == "spawn"


# --- a worker that dies before reporting -------------------------------------------------

class _DeadProcess:
    """A child that exits during startup: it never reaches _worker_entry, so it puts nothing."""

    def __init__(self, *a, **kw):
        self.pid = 4242
        self.exitcode = 1

    def start(self):
        return None

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None

    def terminate(self):
        return None


class _EmptyQueue:
    def get(self, timeout=None):
        raise __import__("queue").Empty

    def close(self):
        return None


class _DeadContext:
    Process = _DeadProcess

    def Queue(self):  # noqa: N802 — matches the multiprocessing context API
        return _EmptyQueue()


def test_a_worker_that_dies_before_reporting_is_counted_not_waited_out(monkeypatch, caplog):
    """Startup failures must surface promptly as failures, not as a 15-minute silent wait."""
    monkeypatch.setattr(ocr_parallel.mp, "get_context", lambda _m: _DeadContext())
    monkeypatch.setattr(ocr_parallel.ocr_claims, "new_worker_id", lambda p: p)

    with caplog.at_level(logging.ERROR, logger="app.jobs.ocr_parallel"):
        agg = ocr_parallel.run_parallel(workers=2, allow_beside_legacy=True,
                                        child_result_timeout=30, child_join_timeout=1)

    assert agg["status"] == "completed_with_errors"
    assert agg["startup_failures"] == 2
    assert len(agg["per_worker"]) == 2
    assert all(r["stopped_because"] == "startup_failed" for r in agg["per_worker"])
    assert agg["child_errors"], "a startup failure must be reported as a child error"
    assert any("produced no result" in r.getMessage() for r in caplog.records)
