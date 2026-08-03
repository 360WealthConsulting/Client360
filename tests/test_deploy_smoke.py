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
