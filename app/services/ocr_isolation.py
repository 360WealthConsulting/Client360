"""Per-document OCR isolation — a REAL wall-clock timeout that works on Windows.

Why this exists: the extraction backend's in-process ``_bounded`` timeout is a daemon-thread ``join``.
It cannot preempt a stuck native call or a pure-Python infinite loop (which holds the GIL, so the
timing thread never even wakes), and it never kills abandoned worker threads or their poppler/tesseract
subprocesses. One pathological document can therefore freeze the whole migration indefinitely.

This module runs each document's extraction in a CHILD PROCESS and enforces the timeout in the PARENT,
which is immune to the child's GIL. Two independent bounds:
  * hard wall-clock cap  — absolute ceiling per document;
  * stall watchdog       — the child heartbeats while it can; if the parent sees no message within the
                           stall window (the child is wedged and not even heartbeating), it is killed.
On either trip the child's ENTIRE process tree (child + poppler/tesseract grandchildren) is killed —
POSIX via the child's own session/process group, Windows via ``taskkill /F /T`` — the document is
reported as a timeout, and the parent continues to the next document. No orphan process survives.
"""
from __future__ import annotations

import importlib
import logging
import os
import queue as _queue
import signal
import subprocess
import sys
import time

from app.services.ocr_exceptions import OcrBackendUnavailable, OcrTimeout

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform.startswith("win")


def _resolve(factory_ref):
    """Resolve a ``module.attr`` dotted string (picklable across spawn) to the callable factory."""
    module_name, attr = factory_ref.rsplit(".", 1)
    return getattr(importlib.import_module(module_name), attr)


def _child_main(result_q, factory_ref, row, path, heartbeat_interval):
    """Child-process entrypoint (spawn target). Builds the extractor from ``factory_ref`` and runs it,
    heartbeating while it can. NEVER imports app.db (the factory module must be db-free). On POSIX it
    starts its own session so the parent can kill the whole tree, incl. any poppler/tesseract child."""
    if not _IS_WINDOWS:
        try:
            os.setsid()                       # become session/process-group leader → killable as a group
        except OSError:
            pass
    import threading
    stop = threading.Event()

    def _beat():
        while not stop.wait(heartbeat_interval):
            try:
                result_q.put(("hb", time.monotonic()))
            except Exception:  # noqa: BLE001 — a broken pipe just ends the heartbeat
                return

    hb = threading.Thread(target=_beat, daemon=True)
    hb.start()
    stage = "build"
    try:
        extractor = _resolve(factory_ref)()
        stage = "extract"
        result = extractor(row, path)
        result_q.put(("ok", result))
    except OcrTimeout as exc:
        result_q.put(("err", "OcrTimeout", str(exc), stage))
    except OcrBackendUnavailable as exc:
        result_q.put(("err", "OcrBackendUnavailable", str(exc), stage))
    except BaseException as exc:  # noqa: BLE001 — report any failure to the parent (recorded as failed)
        result_q.put(("err", type(exc).__name__, str(exc)[:2000], stage))
    finally:
        stop.set()


def _kill_tree(proc) -> None:
    """Kill the child and every descendant it spawned. Best-effort; never raises."""
    pid = proc.pid
    if pid is None:
        return
    try:
        if _IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, check=False)
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)   # child is its own group leader (setsid)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    except Exception:  # noqa: BLE001 — fall through to the multiprocessing kill below
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    proc.join(5)


def run_document(factory_ref, row, path, *, hard_timeout, stall_timeout, heartbeat_interval=5.0,
                 doc_id=None, name=None) -> dict:
    """Extract one document in an interruptible child process. Returns the extraction dict, or raises
    :class:`OcrTimeout` on stall/overrun (after killing the tree), or the child's own exception (so the
    caller records it as a failure). The parent process always survives and never leaks a child."""
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    result_q = ctx.Queue()
    proc = ctx.Process(target=_child_main,
                       args=(result_q, factory_ref, row, path, heartbeat_interval), daemon=False)
    started = time.monotonic()
    proc.start()
    try:
        while True:
            elapsed = time.monotonic() - started
            if elapsed > hard_timeout:
                _timeout_log(doc_id, name, elapsed, "hard_cap")
                _kill_tree(proc)
                raise OcrTimeout(f"document OCR exceeded hard cap {hard_timeout:.0f}s")
            try:
                msg = result_q.get(timeout=min(stall_timeout, max(1.0, hard_timeout - elapsed)))
            except _queue.Empty:
                if not proc.is_alive():
                    code = proc.exitcode
                    raise RuntimeError(
                        f"OCR worker exited unexpectedly (code {code}) with no result") from None
                _timeout_log(doc_id, name, time.monotonic() - started, "stalled")
                _kill_tree(proc)
                raise OcrTimeout(
                    f"document OCR made no progress for {stall_timeout:.0f}s (stalled)") from None
            kind = msg[0]
            if kind == "hb":
                continue                                     # progress — reset the stall window
            if kind == "ok":
                return msg[1]
            # ("err", type_name, message, stage)
            _, type_name, message, stage = msg
            if type_name == "OcrTimeout":
                raise OcrTimeout(message)
            if type_name == "OcrBackendUnavailable":
                raise OcrBackendUnavailable(message)
            raise RuntimeError(f"OCR failed at stage '{stage}': {type_name}: {message}")
    finally:
        if proc.is_alive():
            _kill_tree(proc)                                 # guarantee: no orphan worker survives
        else:
            proc.join(1)
        result_q.close()


def _timeout_log(doc_id, name, elapsed, stage) -> None:
    log.warning("OCR TIMEOUT: doc=%s file=%s elapsed=%.1fs stage=%s — killed worker tree, continuing",
                doc_id, name, elapsed, stage)


def default_bounds():
    """(hard_timeout, stall_timeout) derived from the backend's per-document/per-page budgets, with slack
    so the hard cap sits comfortably above the cooperative per-page bounds."""
    from app.services.ocr_backend import ocr_document_timeout, ocr_page_timeout
    doc_to = ocr_document_timeout()
    page_to = ocr_page_timeout()
    hard = doc_to + max(page_to, 30) + 15        # absolute ceiling above the in-child page/doc bounds
    stall = max(page_to * 2, 30)                 # no heartbeat for this long ⇒ wedged (GIL-held) ⇒ kill
    return hard, stall
