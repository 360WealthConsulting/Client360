"""Phase 2 — admin bulk-confirm PREVIEW + guarded execute for validated HIGH proposals.

A new router (kept out of the large, locally-modified admin.py) mounted under the same ``/admin`` prefix,
so the existing middleware gate (``identity.manage`` on ``^/admin``) applies, and each route additionally
requires ``client.write`` — the exact permission the per-document unassigned-resolution routes use. No
username checks.

GET  /admin/documents/high-confirm  — READ-ONLY live preview of the currently-clean HIGH proposals.
POST /admin/documents/high-confirm  — executes ONLY on explicit confirm=yes, re-evaluating every selected
                                       document again and assigning ownership through the existing
                                       per-document write path (atomic, audited, never overwriting).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

# Reuse the existing admin helpers/templates rather than duplicating them (no second audit model).
from app.routes.admin import _view_url, audit, templates
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_high_confirm import confirm_documents, preview_high_confirm

router = APIRouter(prefix="/admin", tags=["administration"])


def _with_view(rows):
    for r in rows:
        r["view_url"] = _view_url(r["document_id"], r["filename"])
    return rows


@router.get("/documents/high-confirm")
def high_confirm_preview(request: Request,
                         principal: Principal = Depends(require_capability("client.write"))):
    """READ-ONLY. Live-re-evaluates the HIGH set and shows the exact eligible documents + owners. Nothing
    is written; the page only offers a confirmation form."""
    data = preview_high_confirm()
    _with_view(data["eligible"])
    _with_view(data["review"])
    return templates.TemplateResponse(
        request=request, name="admin/high_confirm.html",
        context={"principal": principal, **data})


@router.post("/documents/high-confirm")
def high_confirm_execute(request: Request, document_id: list[int] = Form(default=[]),
                         confirm: str = Form(""),
                         principal: Principal = Depends(require_capability("client.write"))):
    """Assign ownership for the selected HIGH documents — ONLY on explicit confirm=yes. Every selected
    document is re-evaluated immediately before its atomic write; the same ownership-resolution audit as
    the per-document Confirm path is recorded for each success."""
    if confirm.strip().lower() != "yes" or not document_id:
        # No explicit confirmation (or nothing selected) -> re-show the read-only preview; write nothing.
        data = preview_high_confirm()
        _with_view(data["eligible"])
        _with_view(data["review"])
        return templates.TemplateResponse(
            request=request, name="admin/high_confirm.html",
            context={"principal": principal,
                     "notice": "Select documents and check the confirmation box to assign.", **data})
    result = confirm_documents(document_id, actor_user_id=principal.user_id,
                               request_id=request.state.request_id)
    # Mirror the per-document Confirm route's audit for parity (resolve_document_ownership already wrote
    # the authoritative document.ownership_resolved event for each success).
    for a in result["assigned"]:
        audit(request, principal, "document.single_resolved", "document", a["document_id"],
              {"destination": {"entity_type": a["entity_type"], "entity_id": a["entity_id"],
                               "entity_name": a["entity_name"]}, "bulk": True})
    return templates.TemplateResponse(
        request=request, name="admin/high_confirm_result.html",
        context={"principal": principal, **result})
