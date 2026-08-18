"""Reusable progress telemetry for long-running migration/import/OCR phases (360Plus).

A single small, dependency-free primitive so every long batch reports the SAME comprehensive telemetry:
phase name, processed/total, percentage, elapsed, rolling throughput, ETA, and the standard outcome
counters (completed / failed / unsupported / timed_out / reused), plus a free-form checkpoint state.

Design goals:
  * Cheap — plain counters + a bounded sample deque; emitting is throttled (every N items OR M seconds),
    so telemetry never materially slows the batch it measures.
  * Deterministic + testable — the clock (``now``) and the output ``sink`` are injectable; ``snapshot()``
    returns a structured dict so tests assert values without parsing text.
  * Rolling throughput — items/sec over a recent time window (not just the lifetime average), which makes
    the ETA track the CURRENT rate rather than being dragged down by a slow start.

Typical use::

    rep = ProgressReporter("ocr", total=len(candidates))
    for row in candidates:
        outcome = process(row)                 # "completed" / "failed" / "unsupported" / ...
        rep.advance(outcome=outcome)
    rep.emit(force=True)                        # final line
"""
from __future__ import annotations

import time as _time
from collections import deque

# The standard outcome buckets every long phase reports (superset — a phase uses the ones it has).
OUTCOMES = ("completed", "failed", "unsupported", "timed_out", "reused")


class ProgressReporter:
    """Accumulates progress for one phase and emits a throttled one-line telemetry update.

    ``total`` may be None (unknown up front) and set later via :meth:`set_total`; percentage/ETA are then
    reported as None until a total is known. ``every``/``interval`` control throttling (emit at least every
    ``every`` processed items OR every ``interval`` seconds). ``window_seconds`` is the rolling-throughput
    window. ``sink`` receives each formatted line (defaults to stdout); ``now`` is the monotonic clock."""

    def __init__(self, phase, total=None, *, every=100, interval=60.0, window_seconds=60.0,
                 sink=None, now=None, extra_outcomes=()):
        self.phase = phase
        self.total = int(total) if total is not None else None
        self._every = max(int(every), 1)
        self._interval = float(interval)
        self._window = float(window_seconds)
        self._sink = sink if sink is not None else (lambda line: print(line, flush=True))
        self._now = now or _time.monotonic
        self.processed = 0
        # Standard buckets plus any phase-specific extras (e.g. OCR's ``encrypted``). Extras default to none
        # so every other phase's telemetry is byte-for-byte unchanged.
        self._extra_outcomes = tuple(o for o in extra_outcomes if o not in OUTCOMES)
        self.counts = dict.fromkeys(OUTCOMES + self._extra_outcomes, 0)
        self.checkpoint = None
        self._start = self._now()
        self._last_emit = self._start
        self._last_emit_processed = 0
        self._samples: deque[tuple[float, int]] = deque()      # (time, processed) for rolling throughput
        self._samples.append((self._start, 0))

    # --- mutation -------------------------------------------------------------------------------------
    def advance(self, n=1, *, outcome=None):
        """Advance the processed counter by ``n`` and (optionally) bump one outcome bucket, then emit if a
        throttle threshold is crossed. ``outcome`` outside :data:`OUTCOMES` is counted only in ``processed``."""
        self.processed += n
        if outcome in self.counts:
            self.counts[outcome] += n
        t = self._now()
        self._samples.append((t, self.processed))
        cutoff = t - self._window                              # keep only samples within the rolling window
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()
        self._maybe_emit(t)

    def record(self, outcome, n=1):
        """Bump an outcome bucket WITHOUT advancing ``processed`` (e.g. a sub-count). No emit."""
        if outcome in self.counts:
            self.counts[outcome] += n

    def set_total(self, total):
        self.total = int(total) if total is not None else None

    def set_checkpoint(self, state):
        self.checkpoint = state

    # --- derived metrics ------------------------------------------------------------------------------
    def elapsed(self):
        return self._now() - self._start

    def throughput(self):
        """Rolling items/sec over the recent window (0.0 until there is a measurable interval)."""
        if len(self._samples) < 2:
            return 0.0
        (t0, p0), (t1, p1) = self._samples[0], self._samples[-1]
        dt = t1 - t0
        return (p1 - p0) / dt if dt > 0 else 0.0

    def percentage(self):
        if not self.total:
            return None
        return min(100.0, 100.0 * self.processed / self.total)

    def eta_seconds(self):
        if not self.total:
            return None
        remaining = max(0, self.total - self.processed)
        rate = self.throughput()
        if rate <= 0:
            return None
        return remaining / rate

    # --- output ---------------------------------------------------------------------------------------
    def snapshot(self):
        """A structured dict of every telemetry field (for logging/tests — no text parsing needed)."""
        pct, eta = self.percentage(), self.eta_seconds()
        snap = {
            "phase": self.phase,
            "processed": self.processed,
            "total": self.total,
            "percentage": round(pct, 1) if pct is not None else None,
            "elapsed_seconds": round(self.elapsed(), 1),
            "throughput_per_sec": round(self.throughput(), 2),
            "eta_seconds": round(eta, 1) if eta is not None else None,
            "completed": self.counts["completed"],
            "failed": self.counts["failed"],
            "unsupported": self.counts["unsupported"],
            "timed_out": self.counts["timed_out"],
            "reused": self.counts["reused"],
            "checkpoint": self.checkpoint,
        }
        for o in self._extra_outcomes:               # phase-specific buckets (e.g. OCR ``encrypted``)
            snap[o] = self.counts[o]
        return snap

    def format(self):
        s = self.snapshot()
        pct = f"{s['percentage']}%" if s["percentage"] is not None else "?"
        tot = s["total"] if s["total"] is not None else "?"
        eta = f"{s['eta_seconds']}s" if s["eta_seconds"] is not None else "?"
        extra = "".join(f" {o}={s[o]}" for o in self._extra_outcomes)   # e.g. " encrypted=3"
        return (f"[{s['phase']}] {s['processed']}/{tot} ({pct}) elapsed={s['elapsed_seconds']}s "
                f"rate={s['throughput_per_sec']}/s eta={eta} completed={s['completed']} "
                f"failed={s['failed']} unsupported={s['unsupported']} timed_out={s['timed_out']} "
                f"reused={s['reused']}{extra} checkpoint={s['checkpoint']}")

    def _maybe_emit(self, now=None):
        now = now if now is not None else self._now()
        if (self.processed - self._last_emit_processed) >= self._every or \
                (now - self._last_emit) >= self._interval:
            self.emit()

    def emit(self, *, force=False):
        """Emit a telemetry line now (``force`` ignored — always emits; kept for call-site clarity)."""
        self._last_emit = self._now()
        self._last_emit_processed = self.processed
        self._sink(self.format())
