"""Release 0.9.12 — content-negotiated authorization denials.

The shell migration changed `_denied` to return a styled HTML 403 for browser
navigations while preserving the JSON 403 for API clients. These tests pin the
security-relevant invariant: the *denial* is unchanged (still 403, still
audited, never a redirect, never a 200) — only the representation is negotiated.
"""
import pytest
from starlette.requests import Request

from app.security.models import Principal


def _request(path, accept):
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "root_path": "",
        "query_string": b"",
        "headers": [(b"accept", accept.encode())],
        "client": ("127.0.0.1", 5000),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    request = Request(scope)
    request.state.request_id = "req-error-page-test"
    return request


@pytest.fixture
def audited(monkeypatch):
    """Capture audit writes instead of hitting the audit table."""
    calls = []
    monkeypatch.setattr(
        "app.security.middleware.write_audit_event",
        lambda **kwargs: calls.append(kwargs),
    )
    return calls


def _deny(request, caps=frozenset()):
    from app.security.middleware import _denied

    principal = Principal(1, "a@e.com", "A", caps)
    return _denied(
        request, principal, "authorization.denied", "route", request.url.path, "Access denied"
    )


BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def test_browser_denial_renders_styled_html_403(audited):
    response = _deny(_request("/organizations", BROWSER))
    body = response.body.decode()
    assert response.status_code == 403
    assert "text/html" in response.headers["content-type"]
    assert "403 · NOT AUTHORIZED" in body
    # the caller-supplied reason and the request id reach the page
    assert "Access denied" in body and "req-error-page-test" in body


def test_api_path_keeps_json_even_when_browser_accept_header_is_sent(audited):
    """An /api client must never be handed an HTML error page."""
    response = _deny(_request("/api/v1/organizations", BROWSER))
    assert response.status_code == 403
    assert "application/json" in response.headers["content-type"]
    assert b"Access denied" in response.body
    assert b"<html" not in response.body.lower()


def test_non_html_client_keeps_json_403(audited):
    response = _deny(_request("/organizations", "application/json"))
    assert response.status_code == 403
    assert "application/json" in response.headers["content-type"]
    assert b"<html" not in response.body.lower()


@pytest.mark.parametrize(
    "path,accept",
    [
        ("/organizations", BROWSER),
        ("/api/v1/organizations", BROWSER),
        ("/organizations", "application/json"),
    ],
)
def test_denial_is_always_403_and_never_a_redirect(path, accept, audited):
    """Content negotiation must not soften the denial into a redirect or a 200."""
    response = _deny(_request(path, accept))
    assert response.status_code == 403
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "path,accept",
    [
        ("/organizations", BROWSER),
        ("/api/v1/organizations", BROWSER),
        ("/organizations", "application/json"),
    ],
)
def test_denial_carries_the_standard_security_headers(path, accept, audited):
    """Denials return early, before dispatch()'s header block.

    Without this the styled HTML 403 would be the only HTML page in the app served
    without `x-frame-options`/CSP `frame-ancestors` — i.e. framable. The styled 404
    already carries them because it passes through call_next.
    """
    response = _deny(_request(path, accept))
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["x-request-id"] == "req-error-page-test"


@pytest.mark.parametrize("path,accept", [("/organizations", BROWSER), ("/api/v1/x", "application/json")])
def test_denial_is_audited_on_both_representations(path, accept, audited):
    """HTML rendering must not bypass the denied-access audit trail."""
    _deny(_request(path, accept))
    assert len(audited) == 1
    event = audited[0]
    assert event["outcome"] == "denied"
    assert event["action"] == "authorization.denied"
    assert event["actor_user_id"] == 1
    assert event["request_id"] == "req-error-page-test"
