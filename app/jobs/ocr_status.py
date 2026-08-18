"""Persistent OCR run-status / heartbeat tracker (operational only).

Writes a small JSON status artifact that is updated **atomically** throughout an OCR sweep, so an operator
can tell — at any moment, and even after a terminal disconnect or a runner crash — whether the run is
making progress or is frozen. The heartbeat advances both *between* documents and *during* a long-running
document (driven by the subprocess-isolation worker's heartbeats), which is what distinguishes "OCR is
currently working on a big document" from "the entire runner is wedged".

This module is PURELY observational. It never influences OCR candidate selection, extraction, timeouts,
or the subprocess kill behavior. Every write is wrapped so that a tracking failure can never break a
migration run. The file is published with ``os.replace`` (atomic on POSIX and Windows), so a crash mid
write can never leave a half-written tracker file — a reader sees either the previous complete version or
the new complete version, never a torn one.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
from datetime import UTC, datetime

STARTING = "STARTING"
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"

_COUNT_KEYS = ("completed", "failed", "timed_out", "unsupported", "skipped", "encrypted")
# run_ocr reports an already-completed (reused) document as "reused"; it is a "skipped" count here.
# ``encrypted`` (password-protected PDFs) is tracked distinctly so operators can tell it apart from
# ordinary unsupported file types even though the persisted document_ocr.status is 'unsupported'.
_OUTCOME_ALIAS = {"reused": "skipped"}

DEFAULT_STALE_SECONDS = 90.0


def default_status_path() -> str:
    """Resolve the status-artifact path. ``OCR_STATUS_FILE`` wins; otherwise a stable per-host location
    under ``OCR_STATUS_DIR`` (or the system temp dir). One well-known path so the CLI can always find it."""
    explicit = os.getenv("OCR_STATUS_FILE")
    if explicit:
        return explicit
    base = os.getenv("OCR_STATUS_DIR") or os.path.join(tempfile.gettempdir(), "client360")
    return os.path.join(base, "ocr_run_status.json")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OcrRunTracker:
    """Mutable, single-writer view of one OCR run that persists itself atomically on every change.

    Lifecycle: :meth:`start` → :meth:`mark_running` → per document
    (:meth:`on_document_start` → zero or more :meth:`heartbeat` → :meth:`on_document_result`) →
    :meth:`finish`. All mutators publish the whole state atomically; none of them ever raise."""

    def __init__(self, path=None, *, run_id, mode=None, total=None, clock=None,
                 pid=None, host=None):
        self._path = path or default_status_path()
        self._clock = clock or _utcnow
        self._started_at = None
        self._index = 0
        self._state = {
            "run_id": run_id,
            "state": STARTING,
            "mode": mode,
            "pid": os.getpid() if pid is None else pid,
            "host": host or socket.gethostname(),
            "started_at": None,
            "last_heartbeat": None,
            "elapsed_seconds": 0.0,
            "current_document_id": None,
            "current_document_name": None,
            "current_index": 0,
            "total": total,
            "current_document_started_at": None,
            "last_success_document_id": None,
            "last_success_index": None,
            "counts": {k: 0 for k in _COUNT_KEYS},
            "last_error": None,
            "finished_at": None,
        }

    @property
    def path(self) -> str:
        return self._path

    def snapshot(self) -> dict:
        """A deep-ish copy of the current state (for tests / in-process readers)."""
        state = dict(self._state)
        state["counts"] = dict(self._state["counts"])
        return state

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        now = self._clock()
        self._started_at = now
        self._state["started_at"] = now.isoformat()
        self._state["state"] = STARTING
        self._touch(now)
        self._safe_write()

    def mark_running(self) -> None:
        self._state["state"] = RUNNING
        self._touch(self._clock())
        self._safe_write()

    def finish(self, state=COMPLETED, *, error=None) -> None:
        now = self._clock()
        self._state["state"] = state
        self._state["finished_at"] = now.isoformat()
        if error is not None:
            self._state["last_error"] = str(error)[:2000]
        self._touch(now)
        self._safe_write()

    # --- per-document / heartbeat -------------------------------------------

    def on_document_start(self, doc_id, name) -> None:
        now = self._clock()
        self._index += 1
        self._state["current_document_id"] = doc_id
        self._state["current_document_name"] = name
        self._state["current_index"] = self._index
        self._state["current_document_started_at"] = now.isoformat()
        self._touch(now)
        self._safe_write()

    def heartbeat(self) -> None:
        """Advance the heartbeat without changing the current document — called repeatedly *while* a
        single document is being OCR'd (via the isolation worker's heartbeats) so a long document reads
        as live progress, not a freeze."""
        self._touch(self._clock())
        self._safe_write()

    def on_document_result(self, doc_id, name, outcome) -> None:
        bucket = _OUTCOME_ALIAS.get(outcome, outcome)
        if bucket in self._state["counts"]:
            self._state["counts"][bucket] += 1
        if bucket == "completed":
            self._state["last_success_document_id"] = doc_id
            self._state["last_success_index"] = self._state["current_index"]
        self._touch(self._clock())
        self._safe_write()

    def record_error(self, message) -> None:
        self._state["last_error"] = str(message)[:2000]
        self._touch(self._clock())
        self._safe_write()

    # --- internals ----------------------------------------------------------

    def _touch(self, now) -> None:
        self._state["last_heartbeat"] = now.isoformat()
        if self._started_at is not None:
            self._state["elapsed_seconds"] = round((now - self._started_at).total_seconds(), 3)

    def _safe_write(self) -> None:
        try:
            self._atomic_write(self.snapshot())
        except Exception:  # noqa: BLE001 — tracking must NEVER break the OCR run
            pass

    def _atomic_write(self, state) -> None:
        directory = os.path.dirname(self._path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ocr_status-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)          # atomic publish (POSIX + Windows); never torn
        except Exception:
            try:
                os.unlink(tmp)                   # a failed write leaves no orphan temp behind
            except OSError:
                pass
            raise


# --- read-only helpers (used by the status CLI) -----------------------------

def read_status(path=None) -> dict:
    """Read the status artifact. Raises ``FileNotFoundError`` if no run has ever written one."""
    with open(path or default_status_path(), encoding="utf-8") as fh:
        return json.load(fh)


def heartbeat_age_seconds(status, *, now=None) -> float | None:
    hb = status.get("last_heartbeat")
    if not hb:
        return None
    now = now or _utcnow()
    return (now - datetime.fromisoformat(hb)).total_seconds()


def is_stale(status, *, threshold_seconds=DEFAULT_STALE_SECONDS, now=None) -> bool:
    """A RUNNING run whose heartbeat is older than ``threshold_seconds`` is stale — the runner is
    frozen. A finished run (COMPLETED/FAILED) is never 'stale'."""
    if status.get("state") not in (STARTING, RUNNING):
        return False
    age = heartbeat_age_seconds(status, now=now)
    return age is not None and age > threshold_seconds


def _fmt_secs(seconds) -> str:
    if seconds is None:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_status(status, *, now=None, threshold_seconds=DEFAULT_STALE_SECONDS) -> str:
    """A concise, human-readable, read-only rendering of a status dict."""
    now = now or _utcnow()
    lines = []
    state = status.get("state", "?")
    lines.append(f"OCR run {status.get('run_id')} [{state}]  mode={status.get('mode')}")
    lines.append(f"  started    {status.get('started_at')}  (elapsed {_fmt_secs(status.get('elapsed_seconds'))})")

    age = heartbeat_age_seconds(status, now=now)
    if state in (STARTING, RUNNING):
        if is_stale(status, threshold_seconds=threshold_seconds, now=now):
            liveness = f"⚠ STALE — no heartbeat for {_fmt_secs(age)} (runner may be FROZEN)"
        else:
            liveness = f"live ({_fmt_secs(age)} ago)"
    else:
        liveness = f"finished {status.get('finished_at')}"
    lines.append(f"  heartbeat  {status.get('last_heartbeat')}  [{liveness}]")

    idx, total = status.get("current_index"), status.get("total")
    pos = f"#{idx}" + (f"/{total}" if total else "")
    doc_age = None
    started = status.get("current_document_started_at")
    if started:
        doc_age = (now - datetime.fromisoformat(started)).total_seconds()
    lines.append(f"  document   {pos}  id={status.get('current_document_id')} "
                 f"\"{status.get('current_document_name')}\"  (on doc {_fmt_secs(doc_age)})")

    c = status.get("counts", {})
    lines.append("  progress   " + "  ".join(f"{k}={c.get(k, 0)}" for k in _COUNT_KEYS))
    lines.append(f"  last ok    #{status.get('last_success_index')} id={status.get('last_success_document_id')}")
    if status.get("last_error"):
        lines.append(f"  last error {status['last_error']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.jobs.ocr_status",
                                description="Print the current OCR run status (read-only; never modifies "
                                            "the run or the status file).")
    p.add_argument("--file", default=None, help="Status file path (default: OCR_STATUS_FILE / temp).")
    p.add_argument("--stale-seconds", type=float, default=DEFAULT_STALE_SECONDS,
                   help="Heartbeat age (s) beyond which a RUNNING run is reported STALE.")
    args = p.parse_args(argv)
    try:
        status = read_status(args.file)
    except FileNotFoundError:
        print(f"No OCR status file at {args.file or default_status_path()} — no run has started.")
        return 1
    except (OSError, ValueError) as exc:
        print(f"Could not read OCR status: {exc}")
        return 1
    print(format_status(status, threshold_seconds=args.stale_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
