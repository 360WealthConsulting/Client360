"""A real /health and /readiness server for the OCR tests.

The admission gate fails closed and has no off switch, deliberately: a production-reachable
"disable" flag is exactly the thing that leaves a gate protecting nothing. So tests do not disable
it — they give it something healthy to talk to, over real HTTP on a loopback port.

Real HTTP matters here. The parallel runner's workers are SPAWNED PROCESSES; a monkeypatched
function in the parent would not exist in the child, but a TCP port does. Tests export
``CLIENT360_HEALTH_URLS`` so children inherit it through ``os.environ``.

Usage::

    with HealthDouble() as health:               # healthy by default
        monkeypatch.setenv("CLIENT360_HEALTH_URLS", health.urls_csv)
        ...
        health.set_unhealthy()                   # /readiness starts reporting not_ready
        health.set_healthy()                     # and recovers
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _State:
    """Shared, mutable, and deliberately tiny: what the double should answer right now."""

    def __init__(self):
        self.health_code = 200
        self.readiness_code = 200
        self.health_status = "ok"
        self.readiness_status = "ready"
        self.health_body = None           # set to a raw string to serve malformed output
        self.readiness_body = None


def _make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):        # keep pytest output clean
            return

        def do_GET(self):                  # noqa: N802 — BaseHTTPRequestHandler's contract
            if self.path.rstrip("/").endswith("readiness"):
                code, status, raw = state.readiness_code, state.readiness_status, state.readiness_body
            elif self.path.rstrip("/").endswith("health"):
                code, status, raw = state.health_code, state.health_status, state.health_body
            else:
                self.send_response(404)
                self.end_headers()
                return
            body = (raw if raw is not None
                    else json.dumps({"status": status, "application": "Client360"}))
            payload = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


class HealthDouble:
    """A loopback server answering /health and /readiness. Healthy until told otherwise."""

    def __init__(self):
        self.state = _State()
        self._server = None
        self._thread = None

    def __enter__(self):
        # Port 0 lets the OS pick a free port, so parallel test runs cannot collide; the ACTUAL
        # bound address is read back from the socket and is what children are told.
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self.state))
        self._server.daemon_threads = True          # no lingering handler threads at shutdown
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._await_ready()
        return self

    def _await_ready(self, deadline_seconds=10.0):
        """Block until the socket actually answers, or fail loudly.

        Explicit readiness, not a sleep: a child that probes before the server is accepting would
        fail the fail-closed gate and look like a mysterious zero-work run.
        """
        end = time.monotonic() + deadline_seconds
        last = None
        while time.monotonic() < end:
            try:
                with urllib.request.urlopen(f"{self.base}/health", timeout=1) as r:
                    if r.getcode() == 200:
                        return
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise RuntimeError(f"health double never became ready on {self.base}: {last!r}")

    def __exit__(self, *_exc):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return False

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def urls(self):
        return (f"{self.base}/health", f"{self.base}/readiness")

    @property
    def urls_csv(self) -> str:
        return ",".join(self.urls)

    # --- the states a test needs ------------------------------------------------------------

    def set_healthy(self):
        self.state.health_code = self.state.readiness_code = 200
        self.state.health_status, self.state.readiness_status = "ok", "ready"
        self.state.health_body = self.state.readiness_body = None

    def set_not_ready(self):
        """200, but the body says the application is not ready — what /readiness does when
        migrations are out of sync mid-deploy."""
        self.state.readiness_code = 200
        self.state.readiness_status = "not_ready"
        self.state.readiness_body = None

    def set_health_error(self, code=503):
        self.state.health_code = code

    def set_malformed(self):
        self.state.readiness_body = "<html>not json at all</html>"
