"""ProgressReporter — reusable long-phase telemetry (deterministic clock + captured sink)."""
from app.services.progress import OUTCOMES, ProgressReporter


class _Clock:
    """Controllable monotonic clock: advance() between calls; each read returns the current value."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _reporter(total=None, **kw):
    lines, clk = [], _Clock()
    rep = ProgressReporter("phase", total=total, sink=lines.append, now=clk, **kw)
    return rep, lines, clk


def test_snapshot_has_every_required_field():
    rep, _, _ = _reporter(total=10)
    snap = rep.snapshot()
    for field in ("phase", "processed", "total", "percentage", "elapsed_seconds",
                  "throughput_per_sec", "eta_seconds", "completed", "failed", "unsupported",
                  "timed_out", "reused", "checkpoint"):
        assert field in snap, field
    assert snap["phase"] == "phase" and snap["total"] == 10 and snap["processed"] == 0


def test_percentage_and_counters_accumulate():
    rep, _, clk = _reporter(total=4, every=10 ** 9)               # no throttled emits
    for outcome in ("completed", "completed", "failed", "reused"):
        clk.advance(1)
        rep.advance(outcome=outcome)
    s = rep.snapshot()
    assert s["processed"] == 4 and s["percentage"] == 100.0
    assert s["completed"] == 2 and s["failed"] == 1 and s["reused"] == 1 and s["unsupported"] == 0


def test_percentage_none_and_eta_none_without_total():
    rep, _, clk = _reporter(total=None, every=10 ** 9)
    clk.advance(1)
    rep.advance(outcome="completed")
    s = rep.snapshot()
    assert s["total"] is None and s["percentage"] is None and s["eta_seconds"] is None


def test_rolling_throughput_and_eta():
    # 100 items over 10s in the window -> 10 items/s; 900 remaining -> ETA 90s.
    rep, _, clk = _reporter(total=1000, every=10 ** 9, window_seconds=60.0)
    for _ in range(100):
        clk.advance(0.1)                                          # 100 * 0.1s = 10s total
        rep.advance(outcome="completed")
    s = rep.snapshot()
    assert s["processed"] == 100 and s["elapsed_seconds"] == 10.0
    assert abs(s["throughput_per_sec"] - 10.0) < 0.2             # ~10/s
    assert abs(s["eta_seconds"] - 90.0) < 2.0                    # 900 remaining / 10/s


def test_rolling_window_drops_old_samples_and_tracks_current_rate():
    # Slow first (1/s for 10s), then fast (10/s). Rolling rate reflects the RECENT fast phase, not the avg.
    rep, _, clk = _reporter(total=None, every=10 ** 9, window_seconds=5.0)
    for _ in range(10):                                          # slow: 1 item/s
        clk.advance(1.0)
        rep.advance()
    for _ in range(50):                                          # fast: 10 items/s
        clk.advance(0.1)
        rep.advance()
    rate = rep.throughput()
    assert rate > 5.0                                            # window dropped the slow start -> near 10/s


def test_emits_every_n_items():
    rep, lines, clk = _reporter(total=100, every=5, interval=10 ** 9)   # count-only trigger
    for _ in range(12):
        clk.advance(1)
        rep.advance(outcome="completed")
    assert len(lines) == 2                                       # at 5 and 10 processed
    assert all(ln.startswith("[phase]") and "completed=" in ln for ln in lines)


def test_emits_on_interval_even_with_few_items():
    rep, lines, clk = _reporter(total=100, every=10 ** 9, interval=60.0)   # time-only trigger
    clk.advance(61.0)
    rep.advance(outcome="completed")
    assert len(lines) == 1 and lines[0].startswith("[phase] 1/100")        # emitted by the interval alone


def test_format_and_checkpoint_state():
    rep, lines, clk = _reporter(total=2, every=10 ** 9)
    rep.set_checkpoint("NOT_ADVANCED")
    clk.advance(1)
    rep.advance(outcome="completed")
    rep.emit()
    line = lines[-1]
    assert "[phase] 1/2 (50.0%)" in line and "checkpoint=NOT_ADVANCED" in line
    assert "completed=1" in line and "failed=0" in line and "reused=0" in line


def test_record_bumps_outcome_without_advancing_processed():
    rep, _, _ = _reporter(total=5, every=10 ** 9)
    rep.record("reused", 3)
    s = rep.snapshot()
    assert s["reused"] == 3 and s["processed"] == 0               # reused sub-count, not progress


def test_set_total_late_enables_percentage():
    rep, _, clk = _reporter(total=None, every=10 ** 9)
    clk.advance(1)
    rep.advance(outcome="completed")
    assert rep.snapshot()["percentage"] is None
    rep.set_total(2)
    assert rep.snapshot()["percentage"] == 50.0


def test_outcomes_constant_matches_snapshot_keys():
    rep, _, _ = _reporter()
    for o in OUTCOMES:
        assert o in rep.snapshot()
