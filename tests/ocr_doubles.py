"""Spawn-safe OCR extractor factories for the isolation tests.

These are module-level so a spawned child process can import them by dotted reference (the same way the
production ``build_production_extractor`` is referenced). They intentionally do NOT touch the database or
files — each returns an ``extractor(row, path)`` callable, some of which deliberately hang so the parent's
wall-clock watchdog must kill the child.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time


def ok_factory():
    def extractor(row, path):
        return {"text": f"ok:{row.get('original_name')}", "engine": "fake", "page_count": 1}
    return extractor


def sleep_forever_factory():
    """Blocks releasing the GIL — heartbeats keep flowing, so the parent's HARD CAP must fire."""
    def extractor(row, path):
        time.sleep(10_000)
    return extractor


def spin_forever_factory():
    """A busy loop — simulates a wedged extractor; the parent kills it by wall clock regardless."""
    def extractor(row, path):
        while True:
            time.sleep(0.2)
    return extractor


def freeze_self_factory():
    """POSIX: the child SIGSTOPs itself so it cannot even heartbeat → the STALL watchdog must fire."""
    def extractor(row, path):
        import signal
        os.kill(os.getpid(), signal.SIGSTOP)     # frozen; only SIGKILL from the parent ends it
        time.sleep(10_000)
    return extractor


def grandchild_hang_factory():
    """Spawns a long-lived grandchild subprocess, records its pid, then hangs — proves the whole process
    TREE is killed (no orphan). The parent writes the grandchild pid to ``$OCR_TEST_PIDFILE``."""
    def extractor(row, path):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
        pidfile = os.environ.get("OCR_TEST_PIDFILE")
        if pidfile:
            with open(pidfile, "w", encoding="utf-8") as fh:
                fh.write(str(child.pid))
        while True:
            time.sleep(0.5)
    return extractor


def boom_factory():
    """The extractor raises immediately — simulates an in-child extraction failure (missing file, backend
    unavailable, etc.). Drives the 'runner reported failures' reproduction: each such document is recorded
    as a failed OCR row and the loop continues."""
    def extractor(row, path):
        raise RuntimeError(f"simulated extraction failure for {row.get('original_name')}")
    return extractor


def slow_ok_factory():
    """Succeeds, but only after a short delay — long enough that the worker emits several heartbeats
    first, so the parent's on_heartbeat fires *during* the document (proves live-progress heartbeats)."""
    def extractor(row, path):
        time.sleep(float(os.environ.get("OCR_TEST_SLOW_SECONDS", "0.6")))
        return {"text": f"ok:{row.get('original_name')}", "engine": "fake", "page_count": 1}
    return extractor


def encrypted_pdf_factory():
    """The extractor raises :class:`OcrEncryptedPdf` — simulates a password-protected PDF detected by the
    backend. Drives the distinct terminal 'unsupported (password_required)' outcome + no-retry behavior."""
    def extractor(row, path):
        from app.services.ocr_exceptions import OcrEncryptedPdf
        raise OcrEncryptedPdf(f"encrypted/password-protected PDF: {row.get('original_name')}")
    return extractor


def selective_factory():
    """Hangs on documents whose name contains 'HANG' (hard-cap killed), succeeds otherwise — drives the
    full run_ocr loop: one document times out, the next completes."""
    def extractor(row, path):
        name = (row.get("original_name") or "")
        if "HANG" in name.upper():
            while True:
                time.sleep(0.2)
        return {"text": f"ok:{name}", "engine": "fake", "page_count": 1}
    return extractor
