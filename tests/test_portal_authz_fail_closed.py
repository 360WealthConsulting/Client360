"""Fail-closed authorization for PORTAL mutations (Phase 1F).

The staff fork (Phase 1A) denies any uncovered mutating staff route. This closes the same gap on the
CLIENT fork: a portal mutation used to run once it merely authenticated with ``current_portal`` and
cleared the master portal gate (the ``portal_access`` kill switch). Now a mutating portal route that is
NOT one of {feature-gated (``client_can``), an approved in-service-scoped mutation, an exempt
auth/bootstrap path} is DENIED by the middleware — authentication alone is never sufficient.

Two layers, proved here:
  * RUNTIME — the real ``AuthenticationMiddleware`` denies an authenticated-but-uncovered portal mutation
    (driven via a direct ASGI ``dispatch``; ``call_next`` is mocked, so "reached call_next (200)" means the
    middleware AUTHORIZED the request through to the route's own service check).
  * STATIC — ``portal_gate.mutation_is_covered`` classifies every registered portal mutation; a new
    unprotected portal mutation makes the coverage guard fail.
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.security.middleware import AuthenticationMiddleware
from app.services.features import portal_gate

# --- runtime behaviour (real middleware via direct ASGI dispatch) ------------

def _build_app():
    app = FastAPI()

    @app.post("/api/v1/portal/consents")                      # covered: in-service self-protected registry
    def _self_protected():
        return {"ok": True}

    @app.post("/api/v1/portal/messages")                      # covered: feature-mapped (secure_messaging)
    def _feature_mapped():
        return {"ok": True}

    @app.post("/api/v1/portal/synthetic/danger")              # UNCOVERED mutation (the footgun)
    def _uncovered():
        return {"ok": True}

    @app.get("/api/v1/portal/synthetic/read")                 # read — must stay open
    def _read():
        return {"ok": True}

    @app.post("/api/v1/portal/auth/logout")                   # exempt auth/bootstrap
    def _logout():
        return {"ok": True}

    return app


_APP = _build_app()


def _status(monkeypatch, method, path, *, authenticated=True, audits=None):
    """Run AuthenticationMiddleware.dispatch for a portal request. The master gate + client_can are forced
    open, so the ONLY thing that can still deny a mutation is the Phase 1F coverage check. Returns the
    status code (the middleware's authorization decision); call_next returns 200 when reached."""
    principal = SimpleNamespace(account_id=7, person_id=7) if authenticated else None
    monkeypatch.setattr("app.portal.service.resolve_portal_session", lambda token: principal)
    monkeypatch.setattr("app.services.features.portal_gate.portal_access_state", lambda p: (True, "open"))
    monkeypatch.setattr("app.services.features.portal_gate.client_can", lambda p, f, **k: True)
    recorded = audits if audits is not None else []
    monkeypatch.setattr("app.security.middleware.write_audit_event",
                        lambda **kw: recorded.append(kw))

    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"accept", b"application/json")], "session": {},
        "app": _APP, "client": ("127.0.0.1", 1234), "server": ("testserver", 80), "scheme": "http",
    }

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _call_next(request):
        return JSONResponse({"ok": True})     # reached only if the middleware AUTHORIZED the request

    mw = AuthenticationMiddleware(_APP)

    async def _go():
        return await mw.dispatch(Request(scope, _receive), _call_next)

    return asyncio.run(_go()).status_code


def test_uncovered_portal_mutation_is_denied(monkeypatch):
    assert _status(monkeypatch, "POST", "/api/v1/portal/synthetic/danger") == 403    # FAIL CLOSED


def test_authentication_alone_is_insufficient(monkeypatch):
    # A fully-authenticated client whose master gate is open is STILL denied on an uncovered mutation —
    # proving current_portal authentication by itself does not authorize a mutation.
    assert _status(monkeypatch, "POST", "/api/v1/portal/synthetic/danger", authenticated=True) == 403


def test_feature_mapped_mutation_reaches_service(monkeypatch):
    assert _status(monkeypatch, "POST", "/api/v1/portal/messages") == 200            # covered → call_next


def test_self_protected_mutation_reaches_its_own_check(monkeypatch):
    # A registry-listed in-service-scoped mutation passes the middleware through to its own service
    # authorization (here call_next → 200).
    assert _status(monkeypatch, "POST", "/api/v1/portal/consents") == 200


def test_exempt_auth_mutation_is_allowed(monkeypatch):
    assert _status(monkeypatch, "POST", "/api/v1/portal/auth/logout") == 200         # exempt bootstrap


def test_unauthenticated_mutation_is_denied(monkeypatch):
    assert _status(monkeypatch, "POST", "/api/v1/portal/synthetic/danger", authenticated=False) == 401


def test_portal_read_is_unaffected(monkeypatch):
    # An uncovered READ must NOT be turned into a mutation-style denial.
    assert _status(monkeypatch, "GET", "/api/v1/portal/synthetic/read") == 200


def test_uncovered_denial_is_audited(monkeypatch):
    audits = []
    assert _status(monkeypatch, "POST", "/api/v1/portal/synthetic/danger", audits=audits) == 403
    denial = [a for a in audits if a.get("action") == "authorization.uncovered_portal_mutation_denied"]
    assert denial and denial[0]["outcome"] == "denied"
    assert denial[0]["metadata"]["method"] == "POST"
    assert denial[0]["entity_id"] == "/api/v1/portal/synthetic/danger"


# --- static coverage guard ---------------------------------------------------

def _portal_mutations(app):
    mut = {"POST", "PUT", "PATCH", "DELETE"}
    out = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None) or set()
        if not path:
            continue
        if not (path.startswith("/portal") or path.startswith("/api/v1/portal")
                or path.startswith("/api/portal")):
            continue
        for m in sorted(methods & mut):
            out.append((m, path))
    return out


def test_every_registered_portal_mutation_is_covered():
    # CI guard: every mutating portal route in the real app must be feature-gated, an approved in-service
    # mutation, or an exempt bootstrap path — else the fail-closed middleware would deny it at runtime.
    from app.main import app
    uncovered = [(m, p) for (m, p) in _portal_mutations(app)
                 if not portal_gate.mutation_is_covered(re.sub(r"\{[^}]+\}", "1", p), m)]
    assert uncovered == [], (
        "Uncovered portal mutation(s) — add a portal_gate feature rule, list the route in "
        f"_MUTATION_SELF_PROTECTED, or exempt it: {uncovered}")


def test_static_guard_fails_on_a_new_unprotected_portal_mutation():
    # Introducing a new portal mutation with no authorization coverage must be caught statically.
    probe = FastAPI()

    @probe.post("/api/v1/portal/newthing/danger")
    def _danger():
        return {"ok": True}

    uncovered = [(m, p) for (m, p) in _portal_mutations(probe)
                 if not portal_gate.mutation_is_covered(re.sub(r"\{[^}]+\}", "1", p), m)]
    assert uncovered == [("POST", "/api/v1/portal/newthing/danger")]


# --- in-service isolation preserved (wrong-client / wrong-scope still denied) -

def test_wrong_scope_covered_mutation_still_denied_in_service():
    # The fail-closed layer does NOT replace or weaken in-service scope: a covered self-protected mutation
    # invoked for a resource outside the client's scope is still denied by its own service check.
    from fastapi import HTTPException

    from app.routes.tax_intake import portal_sync
    from tests._portal_util import seed_portal_account, seed_staff_user
    _, principal, _, _ = seed_portal_account(seed_staff_user())     # fresh account: no tax intakes in scope
    with pytest.raises(HTTPException) as ei:
        portal_sync(999_000_777, principal=principal)              # a return_id outside the client's scope
    assert ei.value.status_code == 403
