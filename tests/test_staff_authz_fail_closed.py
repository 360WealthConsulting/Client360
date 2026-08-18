"""Fail-closed staff authorization (Phase 1A).

The middleware used to fail OPEN: a staff route matched by neither the RULES map nor a require_capability
dependency ran with only authentication. These tests prove:
  * every MUTATING staff route is centrally covered (RULES) OR self-protected (require_capability) OR an
    exempt session route — the CI guard that a new uncovered mutation cannot silently ship;
  * at RUNTIME the middleware DENIES an uncovered mutation, ALLOWS a self-protected mutation through to its
    own capability check, still gates a RULES-covered mutation, leaves reads untouched, and exempts
    /auth session mutations (logout).

Driven via a direct ASGI ``dispatch`` (no httpx TestClient in this env). ``call_next`` is mocked, so
"reached call_next (200)" means the middleware AUTHORIZED the request; a 403 before call_next means it
denied. The route's own require_capability enforcement is covered by the RBAC-coverage guard + route tests.
"""
from __future__ import annotations

import asyncio
import re

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.security.dependencies import require_capability
from app.security.middleware import (
    PUBLIC_EXACT,
    RULES,
    AuthenticationMiddleware,
    _staff_mutation_exempt,
)
from app.security.models import Principal
from app.security.route_coverage import MUTATING_METHODS, is_self_protected
from tests._portal_util import seed_staff_user

# --- CI coverage: no uncovered mutating staff route --------------------------

def _sample(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def _rules_covers(path):
    return any(pat.search(_sample(path)) for pat, _ in RULES)


def _is_portal(path):
    return (path.startswith("/portal") or path.startswith("/api/v1/portal")
            or path.startswith("/api/portal"))


def _is_public(path):
    return path in PUBLIC_EXACT or path.startswith("/static/") or path.startswith("/dev-auth/")


def test_every_mutating_staff_route_is_covered_or_self_protected_or_exempt():
    from app.main import app
    gaps = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        methods = (r.methods or set()) & MUTATING_METHODS
        if not methods or _is_portal(r.path) or _is_public(r.path):
            continue
        if _rules_covers(r.path) or is_self_protected(r) or _staff_mutation_exempt(r.path):
            continue
        gaps += [(m, r.path) for m in sorted(methods)]
    assert gaps == [], (
        "Mutating staff route(s) that would be DENIED at runtime (add a RULES entry or a "
        f"require_capability dependency): {sorted(gaps)}")


# --- runtime behavior (real middleware via direct ASGI dispatch) -------------

def _build_app():
    app = FastAPI()

    @app.post("/uncovered")                                   # no RULES, no require_capability
    def _u():
        return {"ok": True}

    @app.get("/uncovered-get")                                # read — must stay open
    def _ug():
        return {"ok": True}

    @app.post("/self")                                        # self-protected via require_capability
    def _s(p: Principal = Depends(require_capability("demo.write"))):
        return {"ok": True}

    @app.post("/tasks/probe")                                 # RULES: /tasks -> task.read -> task.write
    def _t():
        return {"ok": True}

    @app.post("/auth/logout-probe")                           # exempt /auth/ session mutation
    def _lo():
        return {"ok": True}

    return app


_APP = _build_app()


def _status(monkeypatch, caps, method, path):
    """Run AuthenticationMiddleware.dispatch for a staff principal holding ``caps``. call_next is mocked
    (returns 200); the returned status is the middleware's authorization decision."""
    principal = Principal(seed_staff_user(), "s@e.test", "S", frozenset(caps))
    monkeypatch.setattr("app.security.middleware.resolve_principal", lambda token: principal)
    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"accept", b"application/json")], "session": {},
        "app": _APP, "client": ("127.0.0.1", 1234), "server": ("testserver", 80), "scheme": "http",
    }

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _call_next(request):
        return JSONResponse({"ok": True})   # reached only if the middleware AUTHORIZED the request

    mw = AuthenticationMiddleware(_APP)

    async def _go():
        return await mw.dispatch(Request(scope, _receive), _call_next)

    return asyncio.run(_go()).status_code


def test_uncovered_mutation_is_denied(monkeypatch):
    assert _status(monkeypatch, set(), "POST", "/uncovered") == 403        # FAIL CLOSED


def test_uncovered_read_is_unaffected(monkeypatch):
    assert _status(monkeypatch, set(), "GET", "/uncovered-get") == 200     # reads still open


def test_self_protected_mutation_is_authorized_by_middleware(monkeypatch):
    # Middleware lets a self-protected route through to its own require_capability (here call_next → 200).
    assert _status(monkeypatch, {"demo.write"}, "POST", "/self") == 200


def test_rules_covered_mutation_allowed_with_capability(monkeypatch):
    assert _status(monkeypatch, {"task.write"}, "POST", "/tasks/probe") == 200


def test_rules_covered_mutation_denied_without_capability(monkeypatch):
    assert _status(monkeypatch, set(), "POST", "/tasks/probe") == 403


def test_auth_session_mutation_is_exempt(monkeypatch):
    # /auth/* session mechanics (e.g. logout) carry no capability by design and must not be fail-closed.
    assert _status(monkeypatch, set(), "POST", "/auth/logout-probe") == 200
