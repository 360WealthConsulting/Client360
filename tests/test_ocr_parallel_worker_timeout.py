"""run_parallel must never return while a worker of its generation is still alive.

THE PRODUCTION FAILURE THESE TESTS REPRODUCE

``run_parallel`` capped TOTAL GENERATION RUNTIME at ``child_result_timeout=900``. That is the wrong
measure: a worker reports exactly once, when its lane ends, so elapsed time says nothing about
whether it is healthy. Every corpus lane on a real backlog runs longer than fifteen minutes, so the
cap fired every time — ``break`` abandoned live workers, ``join(60s)`` timed out without being fatal,
and the synthesis labelled them ``startup_failed`` with ``exitcode None``.

On 2026-09-13 the initial lane started at 19:39:31; 900s later (19:54:31) the deadline fired, four
joins of 60s each ran to 19:58:31, and the supervisor started the RETRY lane there — beside four
workers that were still claiming and completing documents. Result: eight concurrent workers against
a configured four, `supervisor_lanes.json` reporting "no OCR work was performed" about workers that
had done ~800 documents, and a generation that would have doubled again at the next lane.

The fix measures progress PER WORKER from its own last batch ping, and makes the return path
unconditional on generation liveness: terminate, escalate, join, and refuse to return if anything
survives.

These use a fake process/queue harness rather than real spawns: the failure is a matter of timing and
process state, and only a scripted harness can reproduce "alive past the deadline" deterministically
and in milliseconds. Real spawning is covered by tests/test_ocr_parallel_integration.py.
"""
from __future__ import annotations

import queue as _queue

import pytest

from app.jobs import ocr_parallel

# --- harness -------------------------------------------------------------------------------------

class FakeProc:
    """A multiprocessing.Process stand-in with scriptable liveness."""

    def __init__(self, pid, *, alive=True, exitcode=None, dies_on_terminate=True,
                 dies_on_kill=True):
        self.pid = pid
        self._alive = alive
        self.exitcode = exitcode
        self.terminated = False
        self.killed = False
        self.joined = 0
        #: Ordered log of lifecycle calls. Counts alone cannot show that cleanup happened BEFORE the
        #: raise, or that the second join followed the kill rather than preceding it.
        self.events = []
        self._dies_on_terminate = dies_on_terminate
        self._dies_on_kill = dies_on_kill

    def is_alive(self):
        return self._alive

    def terminate(self):
        self.terminated = True
        self.events.append("terminate")
        if self._dies_on_terminate:
            self._alive = False
            if self.exitcode is None:
                self.exitcode = -15

    def kill(self):
        self.killed = True
        self.events.append("kill")
        if self._dies_on_kill:
            self._alive = False
            if self.exitcode is None:
                self.exitcode = -9

    def join(self, timeout=None):
        self.joined += 1
        self.events.append("join")

    def start(self):
        pass


class FakeQueue:
    """Delivers scripted messages, then raises Empty forever."""

    def __init__(self, messages=()):
        self._msgs = list(messages)

    def get(self, timeout=None):
        if self._msgs:
            return self._msgs.pop(0)
        raise _queue.Empty

    def put(self, msg):
        self._msgs.append(msg)


class FakeCtx:
    def __init__(self, procs, q):
        self._procs = list(procs)
        self._q = q
        self.spawned = 0

    def Queue(self):
        return self._q

    def Process(self, target=None, args=None, daemon=None):
        p = self._procs[self.spawned]
        self.spawned += 1
        return p


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ocr_parallel.time, "sleep", lambda s: None)


@pytest.fixture(autouse=True)
def _no_legacy_lock(monkeypatch):
    monkeypatch.setattr(ocr_parallel, "legacy_worker_running", lambda engine: False)


@pytest.fixture(autouse=True)
def _fake_engine(monkeypatch):
    import app.db as db
    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(db.engine, "connect", lambda: _C())
    monkeypatch.setattr(db.engine, "begin", lambda: _C())


def _ids(n):
    return [f"ocr{i}-host-1-aaaa{i}" for i in range(n)]


def _run(procs, messages, *, workers=None, clock=None, stall=1800.0, monkeypatch=None):
    """Drive run_parallel against fake processes and a scripted queue."""
    workers = workers if workers is not None else len(procs)
    q = FakeQueue(messages)
    ctx = FakeCtx(procs, q)
    monkeypatch.setattr(ocr_parallel.mp, "get_context", lambda kind: ctx)
    wids = _ids(workers)
    it = iter(wids)
    monkeypatch.setattr(ocr_parallel.ocr_claims, "new_worker_id", lambda prefix='ocr': next(it))
    if clock is not None:
        monkeypatch.setattr(ocr_parallel.time, "monotonic", clock)
    return ocr_parallel.run_parallel(workers=workers, mode="initial",
                                     worker_stall_timeout=stall), wids, q


def _result(wid, **kw):
    base = {"kind": "result", "worker_id": wid, "stopped_because": "lane_empty",
            "batches": 5, "claimed": 50, "completed": 50, "failed": 0, "timed_out": 0,
            "skipped": 0, "unsupported": 0, "encrypted": 0, "chars_extracted": 0,
            "failed_unrecorded": 0, "lost_claims": 0, "throttled_waits": 0,
            "startup_failures": 0, "empty_retries": 0, "consecutive_empty": 0}
    base.update(kw)
    return base


# --- the production failure -----------------------------------------------------------------------

def test_workers_alive_far_beyond_the_old_900s_deadline_are_not_abandoned(monkeypatch):
    """The exact 2026-09-13 failure. Four healthy workers, 19 minutes of wall clock, no results yet.

    Under the old absolute cap this returned at 900s with four live children. It must now keep
    waiting."""
    procs = [FakeProc(100 + i, alive=True) for i in range(4)]
    wids = _ids(4)
    # Clock runs to 1141s - the elapsed_seconds actually recorded in supervisor_lanes.json that day.
    ticks = iter([0.0] + [float(t) for t in range(1, 1300)])
    # Progress pings keep arriving, so no worker is stalled; then all four report and exit.
    msgs = []
    for r in range(3):
        for w in wids:
            msgs.append({"kind": "progress", "worker_id": w, "batches": r + 1, "completed": (r + 1) * 10})
    def clock():
        try: return next(ticks)
        except StopIteration: return 1300.0
    # After the pings are consumed the procs "finish": flip them dead and queue their results.
    class FinishingQueue(FakeQueue):
        def get(self, timeout=None):
            if self._msgs:
                return self._msgs.pop(0)
            for p in procs:
                p._alive = False
                p.exitcode = 0
            if not getattr(self, "_final", False):
                self._final = True
                for w in wids:
                    self._msgs.append(_result(w))
                return self._msgs.pop(0)
            raise _queue.Empty
    q = FinishingQueue(msgs)
    ctx = FakeCtx(procs, q)
    monkeypatch.setattr(ocr_parallel.mp, "get_context", lambda kind: ctx)
    it = iter(wids)
    monkeypatch.setattr(ocr_parallel.ocr_claims, "new_worker_id", lambda prefix='ocr': next(it))
    monkeypatch.setattr(ocr_parallel.time, "monotonic", clock)

    agg = ocr_parallel.run_parallel(workers=4, mode="initial", worker_stall_timeout=1800.0)

    assert len(agg["per_worker"]) == 4
    assert {w["stopped_because"] for w in agg["per_worker"]} == {"lane_empty"}
    assert not any(w.get("stopped_because") == "startup_failed" for w in agg["per_worker"]), \
        "a live, working worker was labelled startup_failed"
    assert all(not p.is_alive() for p in procs)


def test_run_parallel_does_not_return_while_any_worker_is_alive(monkeypatch):
    """The invariant. Three report and exit; the fourth stays alive - the call must not return with
    it running, and when it stalls the whole generation is torn down first."""
    procs = [FakeProc(200 + i, alive=True) for i in range(4)]
    wids = _ids(4)
    for p in procs[:3]:
        p._alive = False
        p.exitcode = 0
    msgs = [_result(w) for w in wids[:3]]
    t = iter([0.0, 1.0, 2.0] + [5000.0] * 50)       # jump past the stall window for worker 4
    q = FakeQueue(msgs)
    ctx = FakeCtx(procs, q)
    monkeypatch.setattr(ocr_parallel.mp, "get_context", lambda kind: ctx)
    it = iter(wids)
    monkeypatch.setattr(ocr_parallel.ocr_claims, "new_worker_id", lambda prefix='ocr': next(it))
    monkeypatch.setattr(ocr_parallel.time, "monotonic", lambda: next(t))

    agg = ocr_parallel.run_parallel(workers=4, mode="initial", worker_stall_timeout=1800.0)

    assert len(agg["per_worker"]) == 4, "every worker must be accounted for"
    assert all(not p.is_alive() for p in procs), "returned with a live worker - the invariant broke"
    assert procs[3].terminated, "the stalled worker was not terminated"
    assert procs[3].joined >= 1, "the stalled worker was not joined"


def test_a_stalled_worker_terminates_the_WHOLE_generation(monkeypatch):
    """Requirement 4/5: one hung worker must not leave its siblings running for the next lane."""
    procs = [FakeProc(300 + i, alive=True) for i in range(4)]
    t = iter([0.0, 1.0] + [9999.0] * 50)
    agg, wids, q = _run(procs, [], clock=lambda: next(t), stall=60.0, monkeypatch=monkeypatch)
    assert all(p.terminated for p in procs), "not every generation process was terminated"
    assert all(not p.is_alive() for p in procs)
    assert agg["stalled_workers"], "the stall was not reported"


def test_terminate_escalates_to_kill_and_still_joins(monkeypatch):
    procs = [FakeProc(400, alive=True, dies_on_terminate=False, dies_on_kill=True)]
    t = iter([0.0, 1.0] + [9999.0] * 50)
    agg, wids, q = _run(procs, [], clock=lambda: next(t), stall=60.0, monkeypatch=monkeypatch)
    assert procs[0].terminated and procs[0].killed
    assert not procs[0].is_alive()
    assert procs[0].joined >= 2, "must join again after escalating to kill()"


def test_a_worker_that_survives_kill_makes_run_parallel_REFUSE_to_return(monkeypatch):
    """The last line of defence: returning would let the supervisor start a lane beside it.

    Proving the raise is not enough. The whole point of the cleanup block is that the FULL escalation
    runs BEFORE giving up, so a future reordering that raised first - leaving a live worker
    untouched - must fail here. Everything asserted below was recorded before the exception
    propagated, because the raise ends the function.
    """
    procs = [FakeProc(500, alive=True, dies_on_terminate=False, dies_on_kill=False),
             FakeProc(501, alive=True, dies_on_terminate=False, dies_on_kill=False)]
    t = iter([0.0, 1.0] + [9999.0] * 60)
    with pytest.raises(RuntimeError, match="refusing to return") as excinfo:
        _run(procs, [], clock=lambda: next(t), stall=60.0, monkeypatch=monkeypatch)

    for p in procs:
        assert p.terminated, f"pid {p.pid} was not terminated before the raise"
        assert p.killed, f"pid {p.pid} was not escalated to kill() before the raise"
        assert p.joined >= 2, f"pid {p.pid} was not joined after BOTH terminate and kill"
        # Exact escalation order: terminate -> join -> kill -> join, all before the raise.
        assert p.events[:4] == ["terminate", "join", "kill", "join"], \
            f"pid {p.pid} cleanup ran out of order: {p.events}"
    # The refusal must name the survivors, so an operator knows what is still running.
    msg = str(excinfo.value)
    for p in procs:
        assert str(p.pid) in msg, f"pid {p.pid} missing from the refusal message: {msg}"


def test_the_whole_generation_is_cleaned_up_before_the_raise_not_just_the_stalled_worker(monkeypatch):
    """A sibling that is merely slow must still be torn down before run_parallel gives up."""
    stuck = FakeProc(510, alive=True, dies_on_terminate=False, dies_on_kill=False)
    sibling = FakeProc(511, alive=True, dies_on_terminate=True)
    t = iter([0.0, 1.0] + [9999.0] * 60)
    with pytest.raises(RuntimeError, match="refusing to return"):
        _run([stuck, sibling], [], clock=lambda: next(t), stall=60.0, monkeypatch=monkeypatch)
    assert sibling.terminated and not sibling.is_alive(), "the sibling was left running"
    assert sibling.joined >= 1, "the sibling was never joined"
    assert stuck.events[:4] == ["terminate", "join", "kill", "join"]


# --- accurate diagnostics ----------------------------------------------------------------------------

def test_startup_failure_abnormal_exit_and_runtime_timeout_are_distinguished(monkeypatch):
    """Requirement 3/6: three different causes must not all read 'startup_failed'."""
    never_started = FakeProc(600, alive=False, exitcode=1)      # died before reporting
    clean_no_result = FakeProc(601, alive=False, exitcode=0)    # exited 0, said nothing
    reported = FakeProc(602, alive=False, exitcode=0)
    hung = FakeProc(603, alive=True)                            # alive, silent -> runtime_timeout
    procs = [never_started, clean_no_result, reported, hung]
    wids = _ids(4)
    t = iter([0.0, 1.0] + [9999.0] * 60)
    agg, _w, _q = _run(procs, [_result(wids[2])], clock=lambda: next(t), stall=60.0,
                       monkeypatch=monkeypatch)

    by = {w.get("worker_id"): w.get("stopped_because") for w in agg["per_worker"]}
    assert by[wids[0]] == "startup_failed", by
    assert by[wids[1]] == "exited_without_result", by
    assert by[wids[2]] == "lane_empty", by
    assert by[wids[3]] == "runtime_timeout", by
    # and never the old lie
    assert not any(w.get("stopped_because") == "startup_failed" and w.get("worker_id") == wids[3]
                   for w in agg["per_worker"])


def test_no_live_worker_is_ever_labelled_startup_failed_with_exitcode_none(monkeypatch):
    """The precise mislabel from supervisor_lanes.json on 2026-09-13."""
    procs = [FakeProc(700 + i, alive=True) for i in range(4)]
    t = iter([0.0, 1.0] + [9999.0] * 60)
    agg, _w, _q = _run(procs, [], clock=lambda: next(t), stall=60.0, monkeypatch=monkeypatch)
    for w in agg["per_worker"]:
        assert not (w.get("stopped_because") == "startup_failed" and w.get("exitcode") is None), w
        assert w.get("stopped_because") == "runtime_timeout"


def test_every_worker_is_accounted_for_exactly_once(monkeypatch):
    procs = [FakeProc(800 + i, alive=False, exitcode=0) for i in range(4)]
    wids = _ids(4)
    agg, _w, _q = _run(procs, [_result(w) for w in wids], monkeypatch=monkeypatch)
    assert len(agg["per_worker"]) == 4
    assert sorted(w["worker_id"] for w in agg["per_worker"]) == sorted(wids)


def test_progress_pings_are_not_mistaken_for_results(monkeypatch):
    """A ping must not satisfy 'this worker reported'."""
    procs = [FakeProc(900, alive=False, exitcode=0)]
    wid = _ids(1)[0]
    msgs = [{"kind": "progress", "worker_id": wid, "batches": 1, "completed": 10},
            _result(wid, completed=99)]
    agg, _w, _q = _run(procs, msgs, monkeypatch=monkeypatch)
    assert len(agg["per_worker"]) == 1
    assert agg["per_worker"][0]["completed"] == 99
    assert "kind" not in agg["per_worker"][0], "the transport tag leaked into the result"


def test_an_untagged_legacy_message_is_still_treated_as_a_result(monkeypatch):
    procs = [FakeProc(910, alive=False, exitcode=0)]
    wid = _ids(1)[0]
    legacy = {k: v for k, v in _result(wid).items() if k != "kind"}
    agg, _w, _q = _run(procs, [legacy], monkeypatch=monkeypatch)
    assert len(agg["per_worker"]) == 1
    assert agg["per_worker"][0]["stopped_because"] == "lane_empty"


def test_the_absolute_runtime_cap_is_gone(monkeypatch):
    import inspect
    sig = inspect.signature(ocr_parallel.run_parallel)
    assert "child_result_timeout" not in sig.parameters, \
        "the absolute total-runtime cap must not come back"
    assert sig.parameters["worker_stall_timeout"].default == ocr_parallel.DEFAULT_WORKER_STALL_TIMEOUT
    assert ocr_parallel.DEFAULT_WORKER_STALL_TIMEOUT >= 900.0


def test_the_bounded_empty_claim_retry_from_pr309_is_preserved():
    import inspect
    assert ocr_parallel.LANE_EMPTY_RETRIES == 3
    assert ocr_parallel.DEFAULT_EMPTY_SLEEP == 2.0
    assert inspect.signature(ocr_parallel.worker_loop).parameters["empty_batch_retries"].default == 0


def test_the_supervisor_persists_outcome_classification():
    from app.jobs import ocr_supervisor
    published = {}
    class _Pub:
        def publish(self, name, payload): published[name] = payload
    ocr_supervisor._publish_lane_diagnostics(
        _Pub(), "initial", 4,
        {"status": "completed_with_errors", "elapsed_seconds": 1141.63,
         "child_exitcodes": {1: 0, 2: -15}, "child_errors": [], "stalled_workers": ["ocr3-h-1-d"],
         "per_worker": [{"worker_id": "ocr0-h-1-a", "stopped_because": "lane_empty"},
                        {"worker_id": "ocr1-h-1-b", "stopped_because": "abnormal_exit", "exitcode": 1},
                        {"worker_id": "ocr2-h-1-c", "stopped_because": "startup_failed"},
                        {"worker_id": "ocr3-h-1-d", "stopped_because": "runtime_timeout"}]})
    doc = published["supervisor_lanes.json"]
    assert doc["outcomes"] == {"lane_empty": 1, "abnormal_exit": 1,
                               "startup_failed": 1, "runtime_timeout": 1}
    assert doc["stalled_workers"] == ["ocr3-h-1-d"]
    assert doc["child_exitcodes"] == {1: 0, 2: -15}
