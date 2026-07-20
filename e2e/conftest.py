"""End-to-end (real browser) test harness.

These tests boot the actual ASGI application under uvicorn and drive it with a
real Chromium browser via Playwright — the layer the unit suite (which calls
handlers directly) cannot cover: HTML rendering, static assets, redirects, and
client-side behaviour.

This directory is deliberately OUTSIDE ``tests/`` (pytest ``testpaths=["tests"]``)
so the browser suite never runs as part of the unit run. Invoke it explicitly:

    python -m pytest e2e/ -q

It requires Playwright + browsers (``requirements-e2e.txt`` + ``playwright
install``) and a migrated disposable test database (same ``DATABASE_URL`` guard
as the unit suite). If Playwright is not installed the whole directory is skipped
rather than failing, so ``python -m pytest`` in a browser-less environment is a
no-op here.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

# Skip the entire browser suite cleanly when the toolchain is absent.
pytest.importorskip(
    "playwright",
    reason="Playwright not installed; `pip install -r requirements-e2e.txt && playwright install`.",
)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="session")
def live_server():
    """Boot the real app under uvicorn against the disposable test DB; yield its base URL."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "DATABASE_URL": os.environ.get("DATABASE_URL", "postgresql://localhost/client360_test"),
        "SESSION_SECRET": os.environ.get("SESSION_SECRET", "test-session-secret-not-for-production"),
        "CLIENT360_ENVIRONMENT": "development",
        "SESSION_HTTPS_ONLY": "false",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(60):
            if proc.poll() is not None:
                output = proc.stdout.read().decode() if proc.stdout else ""
                raise RuntimeError(f"uvicorn exited early (code {proc.returncode}):\n{output}")
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("uvicorn did not become ready within the timeout")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
