"""Phase 4 review surface — CONTEXT_HIGH + CONTEXT_LIKELY only.

New router under ``/admin`` (so the ``identity.manage`` middleware gate applies — admin-only), each route
additionally requiring ``client.write`` (the same permission the unassigned manual resolution uses). The
page shows, per document, the proposed owner and the exact contextual evidence, with a single Approve
button. Approval re-runs the context analysis live and assigns through the existing atomic path. CONFLICT,
GENERAL_OR_UNRESOLVED and POSSIBLE_NEW_ENTITY are never assignable here. No username checks, no bulk.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.routes.admin import _view_url, templates
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_nomatch_analysis import approve_context, context_candidates

router = APIRouter(prefix="/admin", tags=["administration"])


def _back(msg):
    return RedirectResponse(f"/admin/documents/context-review?msg={msg}", status_code=303)


@router.get("/documents/context-review")
def context_review_page(request: Request,
                        principal: Principal = Depends(require_capability("client.write"))):
    """READ-ONLY. CONTEXT_HIGH + CONTEXT_LIKELY proposals with evidence, supporting resolved-neighbour
    document ids, the proposed owner, and a View link. Nothing is assigned until an Approve button is used."""
    data = context_candidates()
    for bucket in ("context_high", "context_likely"):
        for r in data[bucket]:
            r["view_url"] = _view_url(r["document_id"], r["filename"])
    return templates.TemplateResponse(
        request=request, name="admin/context_review.html",
        context={"principal": principal, "notice": request.query_params.get("msg"), **data})


@router.post("/documents/context-review/approve")
def context_review_approve(request: Request, document_id: int = Form(...), entity_type: str = Form(...),
                           entity_id: int = Form(...), confirm: str = Form(""),
                           principal: Principal = Depends(require_capability("client.write"))):
    """Approve one CONTEXT_HIGH/LIKELY proposal: explicit confirm=yes, live re-classification, still-A/B,
    still-same-owner, all-NULL recheck, atomic audited write. Never overwrites; never creates/merges."""
    if confirm.strip().lower() != "yes":
        return _back("Approval requires the confirmation box.")
    r = approve_context(document_id, entity_type, entity_id, principal=principal,
                        request_id=request.state.request_id)
    if r.get("ok"):
        d = r["destination"]
        return _back(f"Document {document_id} assigned to {d.get('entity_type')} {d.get('entity_name')}.")
    return _back(f"Document {document_id} not assigned ({r.get('reason', 'error')}).")
