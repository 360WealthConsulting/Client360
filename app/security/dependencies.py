from fastapi import Depends, HTTPException, Request
from app.security.models import Principal

def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None: raise HTTPException(401, "Authentication required")
    return principal

# Dependencies produced by require_capability/require_any_capability carry this attribute (the capability
# codes they enforce). It lets the fail-closed staff authorization layer detect that a route protects
# itself via require_capability even when the route is not covered by the central RULES map — see
# app.security.route_coverage and app.security.middleware (Phase 1A).
CAPABILITY_DEP_ATTR = "__client360_required_capabilities__"

def require_capability(code):
    def dependency(principal: Principal = Depends(current_principal)):
        if not principal.can(code): raise HTTPException(403, f"Capability required: {code}")
        return principal
    setattr(dependency, CAPABILITY_DEP_ATTR, (code,))
    return dependency

def require_any_capability(*codes):
    """Route dependency that admits a principal holding ANY of the given capabilities (RBAC never bypassed).
    Used where an oversight surface is open to more than one authoritative role (e.g. a supervisor OR an
    executive). A single code behaves exactly like ``require_capability``."""
    def dependency(principal: Principal = Depends(current_principal)):
        if not any(principal.can(code) for code in codes):
            raise HTTPException(403, f"Capability required: one of {', '.join(codes)}")
        return principal
    setattr(dependency, CAPABILITY_DEP_ATTR, tuple(codes))
    return dependency
