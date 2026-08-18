"""Static staff-route authorization coverage (Phase 1A — fail-closed staff authz).

Shared introspection used by BOTH the fail-closed AuthenticationMiddleware and the RBAC-coverage tests.
A staff route is authorized two ways (server-side): the middleware's central ``RULES`` map (URL prefix →
capability), or a ``require_capability`` / ``require_any_capability`` dependency on the route itself
(tagged with ``CAPABILITY_DEP_ATTR``). Historically the middleware failed OPEN when a route matched neither
— an uncovered mutation route was only authentication-gated. This module lets the middleware detect that a
route self-protects with require_capability so it can DENY an *uncovered mutating* staff route by default
instead. Reads are unaffected; portal routes are handled in the middleware's portal fork.
"""
from __future__ import annotations

from app.security.dependencies import CAPABILITY_DEP_ATTR

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _dep_calls(route):
    """Every dependency callable in a route's dependant tree (route deps + nested)."""
    out, stack = [], [getattr(route, "dependant", None)]
    while stack:
        d = stack.pop()
        if d is None:
            continue
        call = getattr(d, "call", None)
        if call is not None:
            out.append(call)
        stack.extend(getattr(d, "dependencies", []) or [])
    return out


def route_required_capabilities(route) -> tuple:
    """Capability codes a route enforces via require_capability/require_any_capability (empty if none)."""
    caps: list = []
    for call in _dep_calls(route):
        codes = getattr(call, CAPABILITY_DEP_ATTR, None)
        if codes:
            caps.extend(codes)
    return tuple(caps)


def is_self_protected(route) -> bool:
    """True if the route carries a require_capability / require_any_capability dependency."""
    return bool(route_required_capabilities(route))


def build_self_protected_matchers(routes):
    """(compiled path regex, frozenset(methods)) for every route that self-protects with
    require_capability. The route's OWN ``path_regex`` is used — accurate template matching, never a
    re-derived pattern. Built once from the app's routes and cached by the middleware."""
    matchers = []
    for r in routes:
        path_regex = getattr(r, "path_regex", None)
        methods = getattr(r, "methods", None)
        if path_regex is None or not methods:
            continue
        if is_self_protected(r):
            matchers.append((path_regex, frozenset(methods)))
    return matchers


def path_is_self_protected(matchers, path: str, method: str) -> bool:
    """Whether ``method path`` is served by a route that self-protects with require_capability."""
    return any(method in methods and path_regex.match(path) for path_regex, methods in matchers)
