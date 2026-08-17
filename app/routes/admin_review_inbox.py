"""Unified staff review inbox — one landing (`GET /admin/review`) that shows the top-line document-review
backlog and links to every review lane, so a reviewer no longer has to know five separate URLs.

Under ``/admin`` (the ``identity.manage`` middleware gate applies) and additionally requires ``client.read``
(read-only page). The counts are cheap SQL aggregates (`document_review_inbox.inbox_summary`); each lane page
computes its own detailed breakdown when opened.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.routes.admin import templates
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_review_inbox import inbox_summary

router = APIRouter(prefix="/admin", tags=["administration"])


@router.get("/review")
def review_inbox(request: Request,
                 principal: Principal = Depends(require_capability("client.read"))):
    """READ-ONLY unified review inbox: unowned-document backlog + OCR state distribution + links to every
    review lane."""
    return templates.TemplateResponse(
        request=request, name="admin/review_inbox.html",
        context={"principal": principal, **inbox_summary()})
