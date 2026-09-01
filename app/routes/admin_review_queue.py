"""Admin review queue for MEDIUM + AMBIGUOUS (and HIGH-review) document proposals.

New router under ``/admin`` (so the ``identity.manage`` middleware gate applies — admin-only), each route
additionally requiring ``client.write`` — the same permission the ``/admin/documents/unassigned`` manual
resolution uses. The page shows each document's evidence + candidate owners with direct Approve buttons;
approval goes through the existing atomic write path (households.resolve_document_ownership). No username
checks, no auto-assignment, no bulk selection.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.routes.admin import _view_url, templates
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_review_queue import approve_ownership, review_queue

router = APIRouter(prefix="/admin", tags=["administration"])


def _back(msg):
    return RedirectResponse(f"/admin/documents/review-queue?msg={msg}", status_code=303)


@router.get("/documents/review-queue")
def review_queue_page(request: Request,
                      principal: Principal = Depends(require_capability("client.write"))):
    """READ-ONLY. Live queue of MEDIUM + AMBIGUOUS (+ HIGH-review) proposals with evidence, candidate
    owners, and a View link to the actual document. Nothing is assigned until an Approve button is used."""
    data = review_queue()
    for bucket in ("medium", "ambiguous", "high_review", "ocr_review", "unresolved"):
        for r in data[bucket]:
            r["view_url"] = _view_url(r["document_id"], r["filename"])
    return templates.TemplateResponse(
        request=request, name="admin/review_queue.html",
        context={"principal": principal, "notice": request.query_params.get("msg"), **data})


@router.post("/documents/review-queue/approve")
def review_queue_approve(request: Request, document_id: int = Form(...), entity_type: str = Form(...),
                         entity_id: int = Form(...), confirm: str = Form(""),
                         principal: Principal = Depends(require_capability("client.write"))):
    """Assign the reviewed document to the admin-chosen candidate via the existing atomic write path.
    Requires explicit confirm=yes; re-checks all-NULL ownership + record scope; never overwrites."""
    if confirm.strip().lower() != "yes":
        return _back("Approval requires the confirmation box.")
    r = approve_ownership(document_id, entity_type, entity_id, principal=principal,
                          request_id=request.state.request_id)
    if r.get("ok"):
        d = r["destination"]
        return _back(f"Document {document_id} assigned to {d.get('entity_type')} {d.get('entity_name')}.")
    return _back(f"Document {document_id} not assigned ({r.get('reason', 'error')}).")
