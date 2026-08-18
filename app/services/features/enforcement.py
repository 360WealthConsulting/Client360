"""Server-side feature enforcement for portal routes.

``require_client_feature(feature_key)`` is the reusable dependency future (and existing) portal routes
attach to enforce client feature access on the SERVER — never UI hiding. It reads the portal principal
the middleware already resolved onto ``request.state`` (so it composes with the portal auth fork without
importing the routes module, avoiding an import cycle) and delegates the decision to the single
``client_can`` engine. Default-deny: an unauthenticated caller gets 401, a disallowed feature gets 403,
and an unregistered/unknown feature fails closed (403).

    @router.get("/api/v1/portal/wealth/monte-carlo")
    def monte_carlo(principal: PortalPrincipal = Depends(require_client_feature("monte_carlo"))):
        ...
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.services.features.service import client_can


def require_client_feature(feature_key: str):
    """FastAPI dependency: allow the request only if the portal client may use ``feature_key``."""
    def dependency(request: Request):
        principal = getattr(request.state, "portal_principal", None)
        if principal is None:
            raise HTTPException(401, "Portal authentication required")
        if not client_can(principal, feature_key):
            # 403, not 404: the client is authenticated; the feature is simply not available to them.
            raise HTTPException(403, "This feature is not available on your account.")
        return principal

    dependency.__qualname__ = f"require_client_feature[{feature_key}]"
    dependency.__feature_key__ = feature_key
    return dependency
