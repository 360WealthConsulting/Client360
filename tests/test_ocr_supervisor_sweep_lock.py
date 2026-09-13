"""The supervisor must own the in-app sweep's lock (511005002) for its whole life.

THE DEFECT THESE TESTS PIN
    ``ocr_parallel`` deliberately does not take ``ocr_runner``'s per-batch lock 511005002, reasoning
    that "per-document claiming is a strictly stronger guarantee". That holds only among claim
    PARTICIPANTS. The application's own ``ocr-incremental-sweep`` job is not one: it runs
    ``ocr_runner.run_sweep`` -> ``document_ocr.run_ocr`` and never touches ``ocr_document_claims``.
    Its candidate predicate is the same population the supervisor's initial lane sweeps.

    The legacy ``worker.py`` was safe only by accident: it performed OCR *through* ``run_sweep``, so
    it took 511005002 per chunk and the two serialised. A parallel runner calling ``run_ocr`` directly
    takes that lock never — so the cutover would have let the scheduled sweep OCR documents the
    workers held claims on, every 30 minutes, double-bumping ``attempts`` and spawning extraction
    subprocesses outside ``ocr_throttle``'s admission control.

    No duplicate-claim assertion can catch that, which is why these tests exist: the sweep takes no
    claim to duplicate. ``ocr_claims.duplicate_live_claims`` is documented as *always* empty.

These use real PostgreSQL advisory locks, because that is the mechanism under test. A mock would
prove only that the mock was called.
"""
import uuid

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.pool import NullPool

from app.db import document_ocr, documents, engine
from app.jobs import ocr_parallel, ocr_runner, ocr_supervisor
from tests.health_double import HealthDouble

_TAG = "SWEEPLOCK"
_DBL = "tests.ocr_doubles"


# --- fixtures -------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            ids = list(c.scalars(select(documents.c.id).where(
                documents.c.original_name.like(f"%{_TAG}%"))))
            if ids:
                c.execute(text("DELETE FROM ocr_document_claims WHERE document_id = ANY(:i)"),
                          {"i": ids})
                c.execute(text("DELETE FROM document_classifications WHERE document_id = ANY(:i)"),
                          {"i": ids})
                c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(ids)))
                c.execute(delete(documents).where(documents.c.id.in_(ids)))
    _wipe()
    yield
    _wipe()


@pytest.fixture(autouse=True)
def _ops_dir_is_temporary(tmp_path, monkeypatch):
    """The supervisor's default ops dir is a real production path; keep every write in the test's."""
    monkeypatch.setenv("OCR_SUPERVISOR_DIR", str(tmp_path / "ops"))


@pytest.fixture(autouse=True)
def _healthy_endpoints(monkeypatch):
    """The admission gate fails closed by design, so give it something healthy to talk to."""
    with HealthDouble() as health:
        monkeypatch.setenv("CLIENT360_HEALTH_URLS", health.urls_csv)
        yield health


@pytest.fixture(autouse=True)
def _no_lock_leaks():
    """Every lock this module cares about must be free before AND after each test.

    A leaked advisory lock is invisible until some later test refuses itself for no apparent reason,
    which is exactly the kind of cross-test interference that is impossible to debug afterwards.
    """
    keys = (ocr_supervisor.SUPERVISOR_LOCK_KEY, ocr_supervisor.SWEEP_LOCK_KEY,
            ocr_parallel.LEGACY_WORKER_LOCK_KEY)
    for key in keys:
        assert not _held(key), f"lock {key} was already held before the test"
    yield
    for key in keys:
        assert not _held(key), f"lock {key} leaked out of the test"


# --- helpers --------------------------------------------------------------------------------------

def _held(key) -> bool:
    """True when some OTHER session holds this advisory lock."""
    probe = create_engine(ocr_supervisor._lock_url(engine), poolclass=NullPool)
    try:
        with probe.connect() as conn:
            if conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                return False
            return True
    finally:
        probe.dispose()


class Holder:
    """An independent session holding one advisory lock, like a real competing process."""

    def __init__(self, key):
        self.key = key
        self._engine = create_engine(ocr_supervisor._lock_url(engine), poolclass=NullPool)
        self._conn = None

    def __enter__(self):
        self._conn = self._engine.connect()
        got = self._conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self.key}).scalar()
        assert got, f"could not take lock {self.key} for the test"
        return self

    def kill(self):
        """Drop the session WITHOUT unlocking - the crash-safety path."""
        self._conn.close()
        self._engine.dispose()
        self._conn = None

    def __exit__(self, *exc):
        if self._conn is not None:
            try:
                self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self.key})
            finally:
                self._conn.close()
        self._engine.dispose()


def _docs(n, marker=""):
    ids = []
    with engine.begin() as c:
        for i in range(n):
            ids.append(c.execute(documents.insert().values(
                original_name=f"{_TAG} {marker}doc {i}.pdf",
                stored_name=f"{_TAG}-{uuid.uuid4().hex[:8]}",
                storage_path="/x", storage_provider="Client360 Local", storage_uri="/x",
                size_bytes=10, sha256=uuid.uuid4().hex + uuid.uuid4().hex, status="active",
                archived=False).returning(documents.c.id)).scalar_one())
    return ids


class _RecordingPipeline:
    def __call__(self, **kw):
        return {"classified": 0, "candidates": 0, "status": "ok"}


def _spy_run_parallel(record):
    """Stand in for the worker pool, recording the lock state at the moment workers would start."""
    def fake(**kw):
        record.append({
            "workers": kw.get("workers"),
            "mode": kw.get("mode"),
            "supervisor_lock_held": _held(ocr_supervisor.SUPERVISOR_LOCK_KEY),
            "sweep_lock_held": _held(ocr_supervisor.SWEEP_LOCK_KEY),
        })
        return {"status": "completed", "completed": 0, "failed": 0, "timed_out": 0,
                "skipped": 0, "unsupported": 0, "encrypted": 0}
    return fake


# --- acquisition ------------------------------------------------------------------------------------

def test_both_lifetime_locks_are_held_before_any_worker_starts(monkeypatch):
    """Requirement 1 and 4 in one observation: the spy runs where the workers would, and sees both."""
    seen = []
    monkeypatch.setattr(ocr_supervisor.ocr_parallel, "run_parallel", _spy_run_parallel(seen))
    ids = _docs(2)
    result = ocr_supervisor.run_supervisor(workers=1, max_passes=1, document_ids=ids,
                                           pipeline=_RecordingPipeline(),
                                           factory_ref=f"{_DBL}.ok_factory")
    assert seen, f"the worker pool was never reached: {result}"
    for call in seen:
        assert call["supervisor_lock_held"], "511005888 was not held while workers ran"
        assert call["sweep_lock_held"], "511005002 was not held while workers ran"


def test_the_acquisition_order_is_identity_then_resource():
    """Fixed and documented so it stays deadlock-free if either ever becomes a blocking acquire."""
    assert ocr_supervisor.LIFETIME_LOCK_ORDER == (
        ocr_supervisor.SUPERVISOR_LOCK_KEY, ocr_supervisor.SWEEP_LOCK_KEY)


def test_locks_are_taken_in_the_declared_order_and_on_one_session(monkeypatch):
    """The order is observed, not merely declared - and both land on the SAME connection, so one
    session dying frees both."""
    order, conns = [], []
    real = ocr_supervisor.try_lock

    def spy(conn, key):
        order.append(key)
        conns.append(id(conn))
        return real(conn, key)

    monkeypatch.setattr(ocr_supervisor, "try_lock", spy)
    ocr_supervisor.run_supervisor(workers=1, max_passes=1, document_ids=[],
                                  pipeline=_RecordingPipeline())
    assert order == list(ocr_supervisor.LIFETIME_LOCK_ORDER), f"acquired out of order: {order}"
    assert len(set(conns)) == 1, "the two locks were taken on different sessions"


def test_the_sweep_lock_is_the_in_app_runner_key():
    """If these ever drift apart the exclusion silently stops working."""
    assert ocr_supervisor.SWEEP_LOCK_KEY == ocr_runner._OCR_LOCK_KEY == 511_005_002


# --- refusal ------------------------------------------------------------------------------------------

def test_zero_workers_start_when_the_sweep_lock_is_unavailable(monkeypatch):
    """Fail closed. An in-app sweep owning 511005002 must stop the supervisor dead, not degrade it."""
    seen = []
    monkeypatch.setattr(ocr_supervisor.ocr_parallel, "run_parallel", _spy_run_parallel(seen))
    ids = _docs(2)
    with Holder(ocr_supervisor.SWEEP_LOCK_KEY):
        result = ocr_supervisor.run_supervisor(workers=4, max_passes=1, document_ids=ids,
                                               pipeline=_RecordingPipeline(),
                                               factory_ref=f"{_DBL}.ok_factory")
    assert result["status"] == "sweep_lock_unavailable"
    assert result["passes"] == 0
    assert "511005002" in result["error"] or "511005002" in str(result["error"])
    assert seen == [], "workers were started while the in-app sweep held the lock"


def test_zero_workers_start_when_another_supervisor_holds_its_lock(monkeypatch):
    """The pre-existing single-supervisor protection, unchanged."""
    seen = []
    monkeypatch.setattr(ocr_supervisor.ocr_parallel, "run_parallel", _spy_run_parallel(seen))
    ids = _docs(2)
    with Holder(ocr_supervisor.SUPERVISOR_LOCK_KEY):
        result = ocr_supervisor.run_supervisor(workers=4, max_passes=1, document_ids=ids,
                                               pipeline=_RecordingPipeline(),
                                               factory_ref=f"{_DBL}.ok_factory")
    assert result["status"] == "already_running"
    assert seen == [], "workers were started beside another supervisor"


def test_a_refusal_on_the_second_lock_does_not_strand_the_first():
    """All-or-nothing. A supervisor that refuses must leave 511005888 free for the next attempt."""
    with Holder(ocr_supervisor.SWEEP_LOCK_KEY):
        result = ocr_supervisor.run_supervisor(workers=1, max_passes=1, document_ids=[],
                                               pipeline=_RecordingPipeline())
        assert result["status"] == "sweep_lock_unavailable"
        assert not _held(ocr_supervisor.SUPERVISOR_LOCK_KEY), \
            "the supervisor lock was stranded by a refusal on the sweep lock"


def test_acquire_releases_what_it_already_took_when_a_later_lock_fails():
    """The helper's contract, tested directly: on failure it returns nothing held."""
    lock_engine = create_engine(ocr_supervisor._lock_url(engine), poolclass=NullPool)
    conn = lock_engine.connect()
    try:
        with Holder(ocr_supervisor.SWEEP_LOCK_KEY):
            ok, held, blocked = ocr_supervisor.acquire_lifetime_locks(conn)
            assert ok is False and held == [] and blocked == "sweep"
            assert not _held(ocr_supervisor.SUPERVISOR_LOCK_KEY)
    finally:
        conn.close()
        lock_engine.dispose()


# --- release ------------------------------------------------------------------------------------------

def test_both_locks_release_when_every_lane_is_already_empty():
    """The drained path: no worker is ever spawned, and the locks still come back."""
    result = ocr_supervisor.run_supervisor(workers=1, max_passes=1, document_ids=[],
                                           pipeline=_RecordingPipeline())
    assert result["status"] == "drained"
    assert not _held(ocr_supervisor.SUPERVISOR_LOCK_KEY)
    assert not _held(ocr_supervisor.SWEEP_LOCK_KEY)


def test_both_locks_release_after_workers_have_run(monkeypatch):
    """The worker path.

    The pool is stubbed deliberately. What is under test is the lock LIFECYCLE, and a stub makes the
    assertion exact: real spawned workers add a 900-second ``child_result_timeout`` and OS process
    scheduling to a test that would prove nothing extra. Real worker execution is covered by
    tests/test_ocr_supervisor.py; that both locks are held AT the moment workers start is proven by
    test_both_lifetime_locks_are_held_before_any_worker_starts.
    """
    seen = []
    monkeypatch.setattr(ocr_supervisor.ocr_parallel, "run_parallel", _spy_run_parallel(seen))
    result = ocr_supervisor.run_supervisor(workers=2, max_passes=1, document_ids=_docs(2),
                                           pipeline=_RecordingPipeline(),
                                           factory_ref=f"{_DBL}.ok_factory")
    assert seen, f"the worker pool was never reached: {result}"
    assert not _held(ocr_supervisor.SUPERVISOR_LOCK_KEY)
    assert not _held(ocr_supervisor.SWEEP_LOCK_KEY)


def test_both_locks_release_when_the_worker_pool_raises(monkeypatch):
    """A crash must not leave the in-app sweep locked out forever."""
    def boom(**kw):
        raise RuntimeError("worker pool exploded")

    monkeypatch.setattr(ocr_supervisor.ocr_parallel, "run_parallel", boom)
    result = ocr_supervisor.run_supervisor(workers=1, max_passes=1, document_ids=_docs(2),
                                           pipeline=_RecordingPipeline(),
                                           factory_ref=f"{_DBL}.ok_factory")
    assert result["status"] == "crashed"
    assert not _held(ocr_supervisor.SUPERVISOR_LOCK_KEY)
    assert not _held(ocr_supervisor.SWEEP_LOCK_KEY)


def test_both_locks_release_when_startup_itself_fails(monkeypatch):
    """A failure between acquisition and the first pass still runs the finally block."""
    def boom(_engine):
        raise RuntimeError("totals query failed")

    monkeypatch.setattr(ocr_supervisor, "totals", boom)
    result = ocr_supervisor.run_supervisor(workers=1, max_passes=1, document_ids=[],
                                           pipeline=_RecordingPipeline())
    assert result["status"] == "crashed"
    assert not _held(ocr_supervisor.SUPERVISOR_LOCK_KEY)
    assert not _held(ocr_supervisor.SWEEP_LOCK_KEY)


def test_the_database_session_dying_releases_both_locks():
    """The crash-safe fallback. A killed supervisor cannot wedge OCR for the whole firm, because
    PostgreSQL frees session-level advisory locks when the session goes away - which is why both
    locks live on one NullPool connection that really disconnects."""
    lock_engine = create_engine(ocr_supervisor._lock_url(engine), poolclass=NullPool)
    conn = lock_engine.connect()
    ok, held, _ = ocr_supervisor.acquire_lifetime_locks(conn)
    assert ok and len(held) == 2
    assert _held(ocr_supervisor.SUPERVISOR_LOCK_KEY) and _held(ocr_supervisor.SWEEP_LOCK_KEY)

    conn.close()                      # no unlock at all - simulate the process dying
    lock_engine.dispose()

    assert not _held(ocr_supervisor.SUPERVISOR_LOCK_KEY), "511005888 outlived its session"
    assert not _held(ocr_supervisor.SWEEP_LOCK_KEY), "511005002 outlived its session"


# --- the exclusion this whole change exists for -------------------------------------------------------

def test_the_incremental_sweep_does_no_work_while_the_supervisor_holds_the_lock(monkeypatch):
    """The point of the hotfix, proven end to end against the REAL run_sweep.

    Zero candidate selection and zero writes: the sweep must bail at the lock, before it reaches the
    tracker, the candidate query, or a single document.
    """
    from app.services import document_ocr

    selected, ocred = [], []
    monkeypatch.setattr(document_ocr, "_candidates",
                        lambda *a, **k: selected.append(1) or [])
    monkeypatch.setattr(document_ocr, "run_ocr",
                        lambda *a, **k: ocred.append(1) or {})

    def never(*a, **k):
        raise AssertionError("the OCR backend was built while the lock was held")

    monkeypatch.setattr(ocr_runner, "_PRODUCTION_FACTORY", f"{_DBL}.ok_factory")

    with Holder(ocr_supervisor.SWEEP_LOCK_KEY):
        result = ocr_runner.run_sweep(mode="incremental", extractor=never, batch_size=5)

    assert result["status"] == "locked", f"the sweep ran anyway: {result}"
    assert selected == [], "the sweep selected candidates while locked out"
    assert ocred == [], "the sweep OCR'd documents while locked out"
    assert result["completed"] == 0 and result["failed"] == 0


def test_a_real_supervisor_locks_the_incremental_sweep_out_while_it_runs(monkeypatch):
    """Not a stand-in holder: the supervisor itself must be what the sweep collides with."""
    from app.services import document_ocr

    observed = {}

    def fake_run_parallel(**kw):
        # Runs where the workers would. Ask the REAL sweep to run right now and record its answer.
        def never(*a, **k):
            raise AssertionError("the sweep OCR'd a document during supervisor operation")
        monkeypatch.setattr(document_ocr, "run_ocr", never)
        observed["sweep"] = ocr_runner.run_sweep(mode="incremental", extractor=never, batch_size=5)
        return {"status": "completed", "completed": 0, "failed": 0, "timed_out": 0,
                "skipped": 0, "unsupported": 0, "encrypted": 0}

    monkeypatch.setattr(ocr_supervisor.ocr_parallel, "run_parallel", fake_run_parallel)
    ocr_supervisor.run_supervisor(workers=2, max_passes=1, document_ids=_docs(2),
                                  pipeline=_RecordingPipeline(),
                                  factory_ref=f"{_DBL}.ok_factory")
    assert observed.get("sweep", {}).get("status") == "locked", \
        f"the in-app sweep was NOT excluded during supervisor operation: {observed}"


def test_the_sweep_runs_normally_again_once_the_supervisor_exits(monkeypatch):
    """The exclusion must be a lifetime, not a permanent change to the host."""
    ocr_supervisor.run_supervisor(workers=1, max_passes=1, document_ids=[],
                                  pipeline=_RecordingPipeline())
    assert not _held(ocr_supervisor.SWEEP_LOCK_KEY)
    # The lock is free, so run_sweep gets past it and stops for its own reason, not a lock.
    from app.services import document_ocr
    monkeypatch.setattr(document_ocr, "run_ocr",
                        lambda *a, **k: {"candidates": 0, "completed": 0, "failed": 0,
                                         "timed_out": 0, "skipped": 0, "unsupported": 0,
                                         "encrypted": 0, "chars_extracted": 0,
                                         "failed_unrecorded": 0, "errors": []})
    result = ocr_runner.run_sweep(mode="incremental", extractor=lambda *a, **k: ("", 0, 0),
                                  batch_size=5, max_batches=1)
    assert result["status"] != "locked", "the sweep is still locked out after the supervisor exited"


# --- the protections that must not regress ------------------------------------------------------------

def test_the_legacy_worker_exclusion_is_unchanged():
    """511005777 still refuses the parallel runner, and it is still a DIFFERENT key from the others."""
    assert ocr_parallel.LEGACY_WORKER_LOCK_KEY == 511_005_777
    assert len({ocr_parallel.LEGACY_WORKER_LOCK_KEY, ocr_supervisor.SUPERVISOR_LOCK_KEY,
                ocr_supervisor.SWEEP_LOCK_KEY}) == 3
    with Holder(ocr_parallel.LEGACY_WORKER_LOCK_KEY):
        assert ocr_parallel.legacy_worker_running(engine) is True
        refusal = ocr_parallel.run_parallel(workers=1, mode="initial", document_ids=[1])
        assert refusal["status"] == "refused" and "511005777" in refusal["error"]
    assert ocr_parallel.legacy_worker_running(engine) is False


def test_supervisor_running_still_reports_only_the_supervisor_lock():
    """It must not start answering True merely because the sweep lock is held."""
    with Holder(ocr_supervisor.SWEEP_LOCK_KEY):
        assert ocr_supervisor.supervisor_running(engine) is False
    with Holder(ocr_supervisor.SUPERVISOR_LOCK_KEY):
        assert ocr_supervisor.supervisor_running(engine) is True


def test_sweep_lock_held_is_a_probe_that_leaves_no_trace():
    """A diagnostic that acquired the lock it reports on would be worse than none."""
    assert ocr_supervisor.sweep_lock_held(engine) is False
    with Holder(ocr_supervisor.SWEEP_LOCK_KEY):
        assert ocr_supervisor.sweep_lock_held(engine) is True
    assert ocr_supervisor.sweep_lock_held(engine) is False
    assert not _held(ocr_supervisor.SWEEP_LOCK_KEY)
