"""A lane worker must survive losing a claim race, and still end when the lane is really drained.

THE DEFECT THESE TESTS PIN

``ocr_claims.claim_batch`` selects ``ORDER BY d.id LIMIT <batch>`` and wins each row individually, so
under contention a worker can win ZERO rows while thousands of documents remain — its own docstring
says so, and says to call again. ``worker_loop`` instead treated the first empty batch as "lane
empty" and returned for good, and nothing replaces a retired worker.

In the 2026-09-13 18:21 cutover that ended lane ocr1 after eight minutes with 13,589 documents
outstanding. Its signature was a CLEAN exit, not a crash: done=100, claimed=0, stopping on an exact
batch boundary, no Windows error event, the other three lanes unaffected. The four-worker run
finished three-handed and failed its acceptance check.

The fix is a BOUNDED consecutive-empty retry. Bounded matters as much as retry: ``run_parallel``
joins its workers, so a lane that polled forever would never return and the supervisor would never
recount and advance to the retry and classification lanes. These tests pin both halves — that
transient contention does not retire a worker, and that a genuinely exhausted lane still terminates.

No database and no subprocesses: ``claim_batch`` is replaced by scripted sequences, which is the only
way to reproduce an exact interleaving deterministically. Real claiming is covered by
tests/test_ocr_parallel_claims.py.
"""
from __future__ import annotations

import pytest

from app.jobs import ocr_claims, ocr_parallel

_SLEEPS: list[float] = []


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Record sleeps instead of serving them, so a bounded retry costs no wall-clock time."""
    _SLEEPS.clear()
    monkeypatch.setattr(ocr_parallel.time, "sleep", lambda s: _SLEEPS.append(s))


@pytest.fixture(autouse=True)
def _no_priority_change(monkeypatch):
    monkeypatch.setattr(ocr_parallel.ocr_throttle, "lower_priority", lambda: None)


@pytest.fixture(autouse=True)
def _admission_always_open(monkeypatch):
    class _Ok:
        reason = None
        def __bool__(self): return True
    monkeypatch.setattr(ocr_parallel.ocr_throttle, "may_claim", lambda **kw: _Ok())


class _FakeConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture(autouse=True)
def _no_engine(monkeypatch):
    """worker_loop opens `engine.begin()` per batch; give it something inert."""
    import app.db as db
    monkeypatch.setattr(db.engine, "begin", lambda: _FakeConn())
    monkeypatch.setattr(db.engine, "connect", lambda: _FakeConn())


def _claim(doc_id):
    return ocr_claims.Claim(document_id=doc_id, seq=1)


def _run(script, **kw):
    """Run one worker whose claim_batch returns the next entry of ``script`` each call.

    Each entry is a list of document ids (empty list = won nothing). The scripted extractor records
    which documents were processed, and a lease keeper / completion path that never touches a database.
    """
    calls = {"n": 0}
    processed: list[int] = []

    def fake_claim_batch(conn, **kwargs):
        i = calls["n"]; calls["n"] += 1
        if i >= len(script):
            return []                      # past the script: lane is drained
        return [_claim(d) for d in script[i]]

    def fake_run_ocr(**kwargs):
        ids = list(kwargs.get("document_ids") or [])
        processed.extend(ids)
        return {"completed": len(ids), "failed": 0, "timed_out": 0, "skipped": 0,
                "unsupported": 0, "encrypted": 0, "chars_extracted": 0,
                "failed_unrecorded": 0, "errors": []}

    import app.services.document_ocr as svc
    orig_claim, orig_ocr = ocr_claims.claim_batch, svc.run_ocr
    orig_keeper = ocr_parallel._LeaseKeeper
    orig_current, orig_complete = ocr_claims.claim_is_current, ocr_claims.complete

    class _NoKeeper:
        """Matches _LeaseKeeper's real surface: start(document_ids) / stop()."""
        def __init__(self, *a, **k): pass
        def start(self, document_ids): pass
        def stop(self): pass

    ocr_claims.claim_batch = fake_claim_batch
    svc.run_ocr = fake_run_ocr
    ocr_parallel._LeaseKeeper = _NoKeeper
    ocr_claims.claim_is_current = lambda conn, **k: True
    ocr_claims.complete = lambda conn, **k: True
    try:
        totals = ocr_parallel.worker_loop(worker_id="ocr0-test-1-abcd", extractor=object(),
                                          factory_ref=None, **kw)
    finally:
        ocr_claims.claim_batch = orig_claim
        svc.run_ocr = orig_ocr
        ocr_parallel._LeaseKeeper = orig_keeper
        ocr_claims.claim_is_current = orig_current
        ocr_claims.complete = orig_complete
    return totals, processed, calls["n"]


# --- the defect: one lost race must not retire a worker ------------------------------------------

def test_a_single_transient_empty_does_not_end_the_lane():
    """The exact ocr1 failure: one zero-win batch with work still available."""
    totals, processed, _ = _run([[1, 2], [], [3, 4]],
                                empty_batch_retries=ocr_parallel.LANE_EMPTY_RETRIES)
    assert processed == [1, 2, 3, 4], "the worker stopped at the transient empty batch"
    assert totals["stopped_because"] == "lane_empty"
    # 1 retry mid-run, then the final drain costs LANE_EMPTY_RETRIES more before the lane ends.
    assert totals["empty_retries"] == 1 + ocr_parallel.LANE_EMPTY_RETRIES


def test_repeated_empties_below_the_threshold_do_not_end_the_lane():
    """Three consecutive misses are tolerated; the fourth would not be."""
    script = [[1]] + [[]] * ocr_parallel.LANE_EMPTY_RETRIES + [[2]]
    totals, processed, _ = _run(script, empty_batch_retries=ocr_parallel.LANE_EMPTY_RETRIES)
    assert processed == [1, 2], f"worker retired during tolerated contention: {totals}"
    # LANE_EMPTY_RETRIES tolerated mid-run, plus the same again draining at the end.
    assert totals["empty_retries"] == 2 * ocr_parallel.LANE_EMPTY_RETRIES


def test_a_successful_batch_resets_the_consecutive_empty_counter():
    """Misses must be CONSECUTIVE. Alternating miss/win can run indefinitely without retiring."""
    script = [[], [1], [], [2], [], [3], [], [4]]
    totals, processed, _ = _run(script, empty_batch_retries=ocr_parallel.LANE_EMPTY_RETRIES)
    assert processed == [1, 2, 3, 4], "an interleaved miss ended the lane"
    # Four interleaved misses, each reset by the win that followed, plus the final drain.
    assert totals["empty_retries"] == 4 + ocr_parallel.LANE_EMPTY_RETRIES
    # The counter only ever reaches the terminal value at the very end, never mid-run.
    assert totals["consecutive_empty"] == ocr_parallel.LANE_EMPTY_RETRIES + 1


# --- the other half: a drained lane must still terminate -------------------------------------------

def test_the_threshold_terminates_a_genuinely_exhausted_lane():
    """Deterministic exit: retries + 1 consecutive empties and the worker returns, so run_parallel
    can join it and the supervisor can recount and advance."""
    totals, processed, calls = _run([[1]], empty_batch_retries=ocr_parallel.LANE_EMPTY_RETRIES)
    assert processed == [1]
    assert totals["stopped_because"] == "lane_empty"
    # one winning batch, then exactly retries+1 empty attempts
    assert calls == 1 + ocr_parallel.LANE_EMPTY_RETRIES + 1
    assert totals["empty_retries"] == ocr_parallel.LANE_EMPTY_RETRIES


def test_the_retry_is_bounded_in_wall_clock_as_well_as_count():
    """Polling forever would stop run_parallel returning at all - worse than the bug it fixes."""
    _run([[1]], empty_batch_retries=ocr_parallel.LANE_EMPTY_RETRIES)
    assert len(_SLEEPS) == ocr_parallel.LANE_EMPTY_RETRIES
    assert all(s == ocr_parallel.DEFAULT_EMPTY_SLEEP for s in _SLEEPS)
    assert sum(_SLEEPS) <= 10, "an exhausted lane must not linger"


# --- existing callers must be untouched --------------------------------------------------------------

def test_the_default_retires_on_the_first_empty_batch_exactly_as_before():
    """empty_batch_retries defaults to 0, so ocr_parallel's CLI and every existing caller behave
    exactly as they did before this change."""
    totals, processed, calls = _run([[1], [], [2]])       # no kwarg passed
    assert processed == [1], "default behaviour changed for existing callers"
    assert totals["stopped_because"] == "lane_empty"
    assert calls == 2 and totals["empty_retries"] == 0
    assert _SLEEPS == []


def test_the_supervisor_opts_in_and_the_cli_does_not():
    import inspect
    sup = inspect.getsource(ocr_parallel).split("def worker_loop", 1)[0]
    assert "LANE_EMPTY_RETRIES = 3" in sup
    from app.jobs import ocr_supervisor
    src = inspect.getsource(ocr_supervisor)
    assert "empty_batch_retries=ocr_parallel.LANE_EMPTY_RETRIES" in src
    sig = inspect.signature(ocr_parallel.worker_loop)
    assert sig.parameters["empty_batch_retries"].default == 0


# --- four competing workers ---------------------------------------------------------------------------

def test_four_workers_racing_one_window_all_survive_while_work_remains():
    """A deterministic model of the real contention: four workers draw from ONE ordered pool, and on
    each round at most one wins. Under the old rule the three losers retired on round one."""
    pool = list(range(1, 41))
    retries = ocr_parallel.LANE_EMPTY_RETRIES
    workers = {f"ocr{i}": {"empty": 0, "done": [], "alive": True} for i in range(4)}

    rounds = 0
    while any(w["alive"] for w in workers.values()) and rounds < 200:
        rounds += 1
        for i, (_, w) in enumerate(workers.items()):
            if not w["alive"]:
                continue
            # exactly one worker wins the contested window each round
            if pool and (rounds % 4) == i:
                w["done"].append(pool.pop(0))
                w["empty"] = 0                      # reset on success
            else:
                w["empty"] += 1
                if w["empty"] > retries:
                    w["alive"] = False              # lane_empty

    assert pool == [], "the pool was not drained"
    assert all(len(w["done"]) > 0 for w in workers.values()), \
        "a worker retired without ever completing anything"
    assert all(not w["alive"] for w in workers.values()), \
        "every worker must terminate once the pool is exhausted, so run_parallel can return"


def test_under_the_old_rule_three_of_four_workers_would_retire_immediately():
    """Pins the regression: with retries=0 the losers of round one never work again."""
    pool = list(range(1, 41))
    workers = {f"ocr{i}": {"done": [], "alive": True} for i in range(4)}
    rounds = 0
    while any(w["alive"] for w in workers.values()) and rounds < 200:
        rounds += 1
        for i, (_, w) in enumerate(workers.items()):
            if not w["alive"]:
                continue
            if pool and (rounds % 4) == i:
                w["done"].append(pool.pop(0))
            else:
                w["alive"] = False                  # retries = 0 -> immediate lane_empty
    idle = [k for k, w in workers.items() if not w["done"]]
    assert len(idle) == 3, f"expected 3 workers to retire having done nothing, got {idle}"


def test_no_document_is_claimed_or_processed_twice():
    """The retry must not reprocess: a claim is still won once, by one worker."""
    totals, processed, _ = _run([[1, 2], [], [2, 3], [], [], []],
                                empty_batch_retries=ocr_parallel.LANE_EMPTY_RETRIES)
    # the scripted batches deliberately repeat document 2; claiming is what dedupes in production,
    # so assert the worker processed exactly what it was handed and counted each batch once.
    assert totals["claimed"] == len(processed)
    assert totals["batches"] == 2, "a retry must not be counted as a batch"


def test_a_retry_is_not_counted_as_a_batch_or_a_claim():
    totals, _, _ = _run([[1], [], [2]], empty_batch_retries=ocr_parallel.LANE_EMPTY_RETRIES)
    assert totals["batches"] == 2, "a retry must not be counted as a batch"
    assert totals["claimed"] == 2, "a retry must not be counted as a claim"
    assert totals["empty_retries"] == 1 + ocr_parallel.LANE_EMPTY_RETRIES


# --- the diagnostics that would have made the original diagnosis one log line ----------------------

def test_the_supervisor_publishes_per_worker_stop_reasons():
    from app.jobs import ocr_supervisor

    published = {}

    class _Pub:
        def publish(self, name, payload): published[name] = payload

    ocr_supervisor._publish_lane_diagnostics(
        _Pub(), "initial", 4,
        {"status": "completed", "elapsed_seconds": 12.0,
         "child_exitcodes": {100: 0, 101: 0, 102: 0, 103: 1},
         "child_errors": [],
         "per_worker": [{"worker_id": "ocr0-h-1-a", "stopped_because": "lane_empty", "claimed": 10},
                        {"worker_id": "ocr1-h-1-b", "stopped_because": "throttled", "claimed": 3}]})
    doc = published["supervisor_lanes.json"]
    assert doc["lane"] == "initial" and doc["workers_requested"] == 4
    assert doc["child_exitcodes"] == {100: 0, 101: 0, 102: 0, 103: 1}
    assert [w["stopped_because"] for w in doc["per_worker"]] == ["lane_empty", "throttled"]


def test_publishing_diagnostics_never_raises():
    from app.jobs import ocr_supervisor

    class _Boom:
        def publish(self, *a, **k): raise RuntimeError("ops dir unwritable")

    ocr_supervisor._publish_lane_diagnostics(_Boom(), "initial", 4, {"per_worker": []})
