"""RBAC coverage guard: every STAFF route must be capability-gated.

The app gates staff routes two ways (both server-side): the AuthenticationMiddleware maps a path prefix to a
required capability (``security.middleware.RULES``), and/or a route carries a ``require_capability`` /
``require_any_capability`` dependency. A staff route matched by NEITHER is only authentication-gated — the
structural risk flagged in the readiness audit. This test fails if a NEW staff route ships ungated, so the
gap cannot silently reappear. Portal-fork routes (own scoped principal), auth mechanics, and static/docs are
exempt from the STAFF guard; a small, reviewed baseline of intentionally authenticated-only landing/whoami
endpoints is allowed.

A second guard (V1 extension) covers the PORTAL fork: every non-public portal route must carry the
``current_portal`` authentication dependency, with a small reviewed baseline of middleware-only-authenticated
routes. Portal record-scope authorization lives inside the delegated services and is covered separately by
the portal security-review tests — this guard only ensures no portal route ships unauthenticated.
"""
from app.main import app
from app.routes.portal import current_portal
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


# --- portal (external principal) coverage guard (V1 extension) ----------------
# Portal-fork routes don't use staff capabilities; their authorization is the portal scope enforced
# INSIDE the delegated services. Their baseline protection is authentication: the ``current_portal``
# dependency (401 without a portal principal), which is also independently enforced by the middleware
# fork. This guard fails if a NEW non-public portal route ships WITHOUT ``current_portal`` — so a portal
# route can never silently rely on nothing but the middleware. Record-scope correctness inside the
# services is a separate concern (not statically assertable here) and is covered by the portal
# security-review tests.
_PORTAL_PREFIXES = ("/portal", "/api/v1/portal", "/api/portal")

# Reviewed, intentionally authentication-by-middleware-only (no ``current_portal`` dependency), safe to
# invoke without a principal. New portal routes must NOT be added here without review.
_PORTAL_MIDDLEWARE_ONLY = {
    ("POST", "/portal/logout"),   # revokes whatever session is present + redirects to login; idempotent,
                                  # and the middleware still requires a portal principal for /portal* paths.
}


def _is_portal_path(path):
    return any(path == p or path.startswith(p + "/") or path.startswith(p) for p in _PORTAL_PREFIXES)


def _portal_routes_without_auth_dep():
    """Non-public portal routes that do NOT carry the ``current_portal`` dependency."""
    found = set()
    for r in app.routes:
        path, methods = getattr(r, "path", None), getattr(r, "methods", None)
        if path is None or methods is None or not _is_portal_path(path) or path in PUBLIC_EXACT:
            continue
        if current_portal in _dep_calls(r):
            continue
        for m in methods:
            if m in _HTTP_METHODS:
                found.add((m, path))
    return found


def test_no_new_unauthenticated_portal_route():
    found = _portal_routes_without_auth_dep()
    unexpected = found - _PORTAL_MIDDLEWARE_ONLY
    assert unexpected == set(), (
        "Portal route(s) that are neither PUBLIC_EXACT nor carry the current_portal dependency: "
        f"{sorted(unexpected)}. Add `principal: PortalPrincipal = Depends(current_portal)` to the route, "
        "or (only after review) add it to _PORTAL_MIDDLEWARE_ONLY if it is intentionally safe to invoke "
        "without a principal.")


def test_portal_middleware_only_baseline_still_mounted():
    # Guard the guard: each baselined middleware-only portal route must still exist, so the exemption
    # cannot silently apply to a renamed/replaced route.
    mounted = {(m, getattr(r, "path", None)) for r in app.routes
               for m in (getattr(r, "methods", None) or set())}
    for entry in _PORTAL_MIDDLEWARE_ONLY:
        assert entry in mounted, f"stale portal baseline entry (route removed/renamed): {entry}"
