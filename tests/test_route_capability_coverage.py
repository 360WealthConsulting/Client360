"""RBAC coverage guard: every STAFF route must be capability-gated.

The app gates staff routes two ways (both server-side): the AuthenticationMiddleware maps a path prefix to a
required capability (``security.middleware.RULES``), and/or a route carries a ``require_capability`` /
``require_any_capability`` dependency. A staff route matched by NEITHER is only authentication-gated — the
structural risk flagged in the readiness audit. This test fails if a NEW staff route ships ungated, so the
gap cannot silently reappear. Portal-fork routes (own scoped principal), auth mechanics, and static/docs are
exempt; a small, reviewed baseline of intentionally authenticated-only landing/whoami endpoints is allowed.
"""
from app.main import app
from app.security import dependencies as deps
from app.security.middleware import PUBLIC_EXACT, RULES

# Exempt: portal fork (scoped portal principal, gated in the portal services), auth mechanics, infra/docs.
_EXEMPT_PREFIXES = ("/portal", "/api/v1/portal", "/api/portal", "/auth", "/static", "/docs", "/redoc",
                    "/openapi")
_EXEMPT_EXACT = set(PUBLIC_EXACT) | {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect",
                                     "/favicon.ico"}

# Reviewed, intentionally authenticated-only (return ONLY the caller's own / capability-self-filtered data,
# never scoped client records). New ungated staff routes must NOT be added here without review.
_BASELINE_AUTHENTICATED_ONLY = {
    ("GET", "/home"),                 # role-aware landing; home_summary() filters panels by the caller's caps
    ("GET", "/api/home/summary"),     # JSON of the same self-filtered landing data
    ("GET", "/api/v1/session"),       # whoami — returns the caller's own principal + capabilities only
}

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _dep_calls(route):
    """All dependency callables in a route's dependant tree (route deps + nested)."""
    out, stack = [], [getattr(route, "dependant", None)]
    while stack:
        d = stack.pop()
        if d is None:
            continue
        if getattr(d, "call", None) is not None:
            out.append(d.call)
        stack.extend(getattr(d, "dependencies", []) or [])
    return out


def _has_capability_dep(route):
    for c in _dep_calls(route):
        q = getattr(c, "__qualname__", "")
        if getattr(c, "__module__", "") == deps.__name__ and (
                "require_capability" in q or "require_any_capability" in q):
            return True
    return False


def _is_exempt(path):
    return path in _EXEMPT_EXACT or any(path == p or path.startswith(p + "/") or path.startswith(p)
                                        for p in _EXEMPT_PREFIXES)


def _ungated_staff_routes():
    ungated = set()
    for r in app.routes:
        path, methods = getattr(r, "path", None), getattr(r, "methods", None)
        if path is None or methods is None or _is_exempt(path):
            continue
        gated = any(pat.search(path) for pat, _ in RULES) or _has_capability_dep(r)
        if gated:
            continue
        for m in methods:
            if m in _HTTP_METHODS:
                ungated.add((m, path))
    return ungated


def test_no_new_ungated_staff_route():
    ungated = _ungated_staff_routes()
    unexpected = ungated - _BASELINE_AUTHENTICATED_ONLY
    assert unexpected == set(), (
        "Staff route(s) with NO middleware RULE and NO require_capability dependency: "
        f"{sorted(unexpected)}. Add a middleware RULE or a require_capability/require_any_capability "
        "dependency; only add to the reviewed baseline if it is intentionally authenticated-only and "
        "returns solely the caller's own / capability-self-filtered data.")


def test_baseline_entries_still_exist_and_require_authentication():
    # Guard the guard: every baselined route must still be mounted AND require an authenticated principal,
    # so a baselined landing page can never silently become fully public.
    mounted = {(m, getattr(r, "path", None)) for r in app.routes
               for m in (getattr(r, "methods", None) or set())}
    for entry in _BASELINE_AUTHENTICATED_ONLY:
        assert entry in mounted, f"stale baseline entry (route removed/renamed): {entry}"
    for r in app.routes:
        for m in (getattr(r, "methods", None) or set()):
            if (m, getattr(r, "path", None)) in _BASELINE_AUTHENTICATED_ONLY:
                calls = _dep_calls(r)
                assert any(getattr(c, "__qualname__", "") == "current_principal" for c in calls), \
                    f"baselined route is not authentication-gated: {(m, r.path)}"
