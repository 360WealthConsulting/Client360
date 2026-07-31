"""Staff UI authentication entry — unauthenticated browser pages redirect to the login flow, while
API/JSON requests keep a 401. Regression for the blocker where GET /, /home, /work returned
``{"detail":"Authentication required"}`` (JSON) instead of a browser login redirect because the
decision was made purely on the ``Accept: text/html`` header.

Exercises the real ``AuthenticationMiddleware.dispatch`` directly (no HTTP client / cookies needed).
"""
import asyncio

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.security.middleware import PUBLIC_EXACT, AuthenticationMiddleware

_mw = AuthenticationMiddleware(lambda scope, receive, send: None)


async def _next(_req):
    return PlainTextResponse("PAGE OK")


def _dispatch(path, *, accept="*/*", method="GET", query=b""):
    scope = {"type": "http", "method": method, "path": path, "query_string": query,
             "headers": [(b"accept", accept.encode())], "session": {}}
    req = Request(scope)
    req.scope["session"] = {}
    return asyncio.get_event_loop().run_until_complete(_mw.dispatch(req, _next))


# --- public login routes -----------------------------------------------------

def test_staff_login_route_is_public():
    assert "/auth/login" in PUBLIC_EXACT and "/auth/callback" in PUBLIC_EXACT


def test_portal_login_remains_public():
    assert "/portal/login" in PUBLIC_EXACT


def test_login_page_reaches_the_route_not_a_401():
    # /auth/login is public → dispatch passes through to the route (200/303/503), never a 401.
    resp = _dispatch("/auth/login", accept="text/html")
    assert resp.status_code != 401


# --- browser redirect from protected pages -----------------------------------

def test_browser_redirect_from_root_home_work():
    for path in ("/", "/home", "/work"):
        for accept in ("*/*", "text/html,application/xhtml+xml"):
            resp = _dispatch(path, accept=accept)
            assert resp.status_code == 303, (path, accept, resp.status_code)
            assert resp.headers["location"].startswith("/auth/login?next=")


def test_login_path_redirects_to_the_entry_route():
    resp = _dispatch("/login", accept="*/*")
    assert resp.status_code == 303 and resp.headers["location"].startswith("/auth/login")


def test_redirect_preserves_return_url():
    resp = _dispatch("/work", accept="*/*", query=b"tab=vault")
    # next carries the original path + query, url-encoded
    assert resp.headers["location"] == "/auth/login?next=%2Fwork%3Ftab%3Dvault"


# --- API stays JSON 401 ------------------------------------------------------

def test_api_request_returns_json_401_even_with_html_accept():
    for accept in ("*/*", "text/html", "application/json"):
        resp = _dispatch("/api/home/summary", accept=accept)
        assert resp.status_code == 401
        assert b"Authentication required" in resp.body


def test_mutation_returns_json_401_not_a_redirect():
    resp = _dispatch("/home", accept="*/*", method="POST")
    assert resp.status_code == 401 and b"Authentication required" in resp.body


def test_json_preferring_fetch_gets_401_not_redirect():
    # A JS fetch that explicitly wants JSON (no text/html) should get 401, not an HTML redirect.
    resp = _dispatch("/home", accept="application/json")
    assert resp.status_code == 401


# --- return-url safety (open-redirect guard) ---------------------------------

def test_login_next_rejects_offsite_targets():
    from app.routes.auth import _safe_next
    assert _safe_next("/home") == "/home"
    assert _safe_next("/work?tab=vault") == "/work?tab=vault"
    assert _safe_next("//evil.example/x") is None
    assert _safe_next("https://evil.example") is None
    assert _safe_next(None) is None
