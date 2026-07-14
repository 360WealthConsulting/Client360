from fastapi import Depends, HTTPException, Request
from app.security.models import Principal

def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None: raise HTTPException(401, "Authentication required")
    return principal

def require_capability(code):
    def dependency(principal: Principal = Depends(current_principal)):
        if not principal.can(code): raise HTTPException(403, f"Capability required: {code}")
        return principal
    return dependency
