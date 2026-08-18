"""Deployment smoke test — the live HTTP probe must NOT follow redirects.

A gated staff route (e.g. /home, /work) answers 303 (redirect to login) for an unauthenticated request.
The probe must record that 303, not the 200 of the login page it would otherwise be redirected to —
otherwise a correctly-secured route reads as a FAIL (and, worse, a genuinely-open route would read as a
false pass). Verified with a tiny in-process HTTP server. Also confirms the service-level checks still pass.
"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.deploy import smoke


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/gated":
            self.send_response(303)
            self.send_header("Location", "/portal/login")
            self.end_headers()
        elif self.path == "/public":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_probe_reports_redirect_status_not_followed(server):
    # Gated route → the probe must see 303, NOT the 200 it would land on after following the redirect.
    assert smoke._http_status(f"{server}/gated") == 303
    assert smoke._http_status(f"{server}/public") == 200


def test_gated_route_status_satisfies_expectation(server):
    # 303 is within the smoke test's accepted gated set {401, 302, 303}.
    assert smoke._http_status(f"{server}/gated") in {401, 302, 303}


def test_service_level_checks_still_pass():
    assert smoke.route_registration()["ok"] is True
    assert smoke.auth_gating()["ok"] is True


# --- DB-backed readiness in the smoke gate (Phase 1B) ------------------------

def test_smoke_readiness_passes_when_database_available():
    # With the (disposable) test DB reachable and migrated, the in-process readiness probe is ready.
    result = smoke.readiness_check()
    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["database"] == "ok"


def test_smoke_run_reports_and_gates_on_readiness_when_ready():
    result = smoke.run()                       # no --url: service-level checks only
    assert result["readiness"]["ok"] is True
    assert result["ok"] is True


def test_smoke_fails_on_readiness_failure(monkeypatch):
    # A deployment whose database is unreachable MUST fail smoke — not pass on static process health.
    import app.routes.ops as ops

    def _boom():
        raise RuntimeError("connection refused: postgresql://app:PWD@db.internal/client360")

    monkeypatch.setattr(ops.engine, "connect", _boom)

    rc = smoke.readiness_check()
    assert rc["ok"] is False
    assert rc["status_code"] == 503
    assert rc["database"] == "error"
    # The failure label must not carry the connection string / credentials from the raised error.
    assert "PWD" not in str(rc) and "db.internal" not in str(rc)

    # Route/auth checks are unaffected by the DB being down, but the overall gate still FAILS on readiness.
    result = smoke.run()
    assert result["route_registration"]["ok"] is True
    assert result["auth_gating"]["ok"] is True
    assert result["readiness"]["ok"] is False
    assert result["ok"] is False


class _ReadinessDownHandler(BaseHTTPRequestHandler):
    """A running deployment that is UP but NOT READY: /readiness answers 503, everything else is healthy."""

    def do_GET(self):
        code = {"/health": 200, "/readiness": 503, "/portal/login": 200,
                "/static/css/main.css": 200, "/home": 303, "/work": 303}.get(self.path, 404)
        self.send_response(code)
        if code in (302, 303):
            self.send_header("Location", "/portal/login")
        self.end_headers()

    def log_message(self, *a):
        pass


def test_smoke_http_readiness_503_fails_smoke():
    # The live HTTP probe must treat a 503 (not-ready) as a smoke FAILURE, not an acceptable "up" state.
    httpd = HTTPServer(("127.0.0.1", 0), _ReadinessDownHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        out = smoke.http_smoke(f"http://127.0.0.1:{httpd.server_address[1]}")
        assert out["results"]["/readiness"]["status"] == 503
        assert out["results"]["/readiness"]["ok"] is False   # 503 is a failure now
        assert out["ok"] is False
    finally:
        httpd.shutdown()
