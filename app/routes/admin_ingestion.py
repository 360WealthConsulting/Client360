"""Admin ingestion-status page (Phase G) — one compact view of source sync runs.

Read-only. New router under ``/admin`` (so the ``identity.manage`` middleware gate applies — admin-only),
the route additionally requiring ``identity.manage``. No username checks; no giant dashboard.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.routes.admin import templates
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.microsoft_ingestion import ingestion_status

router = APIRouter(prefix="/admin", tags=["administration"])


@router.get("/ingestion")
def ingestion_status_page(request: Request,
                          principal: Principal = Depends(require_capability("identity.manage"))):
    return templates.TemplateResponse(
        request=request, name="admin/ingestion.html",
        context={"principal": principal, "sources": ingestion_status()})
