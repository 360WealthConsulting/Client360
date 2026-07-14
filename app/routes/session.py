from fastapi import APIRouter, Depends

from app.security.dependencies import current_principal
from app.security.models import Principal


router = APIRouter(prefix="/api/v1", tags=["session"])


@router.get("/session")
def session(principal: Principal = Depends(current_principal)):
    """Return the authenticated identity and request-effective capabilities."""
    return {
        "user_id": principal.user_id,
        "email": principal.email,
        "display_name": principal.display_name,
        "capabilities": sorted(principal.capabilities),
    }
