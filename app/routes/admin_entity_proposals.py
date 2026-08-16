"""Admin review surface for NEW-ENTITY proposals from the document pipeline.

New router under ``/admin`` (so the existing ``identity.manage`` middleware gate applies — admin-only),
each route additionally requiring ``client.write``. The reviewer (Michael) can open the source document,
see the proposed entity type + extracted identity + possible existing matches, and APPROVE creation,
REJECT, or ASSIGN an EXISTING entity instead. Nothing is created without an explicit approval POST.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.routes.admin import _view_url, templates
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_entity_proposal import (
    approve_proposal,
    assign_existing_instead,
    detect_new_entity_candidates,
    reject_proposal,
)

router = APIRouter(prefix="/admin", tags=["administration"])


@router.get("/documents/entity-proposals")
def entity_proposals(request: Request,
                     principal: Principal = Depends(require_capability("client.write"))):
    """READ-ONLY. Live-detects pending new-entity proposals (excluding already-decided documents) and
    shows each with a View link, the proposed entity + evidence, and possible existing matches."""
    proposals = detect_new_entity_candidates()
    for p in proposals:
        p["view_url"] = _view_url(p["document_id"], p["filename"])
    return templates.TemplateResponse(
        request=request, name="admin/entity_proposals.html",
        context={"principal": principal, "proposals": proposals,
                 "notice": request.query_params.get("msg")})


def _back(msg):
    return RedirectResponse(f"/admin/documents/entity-proposals?msg={msg}", status_code=303)


@router.post("/documents/entity-proposals/approve")
def approve(request: Request, document_id: int = Form(...), entity_type: str = Form(...),
            confirm: str = Form(""), principal: Principal = Depends(require_capability("client.write"))):
    if confirm.strip().lower() != "yes":
        return _back("Approval requires the confirmation box.")
    r = approve_proposal(document_id, entity_type, principal=principal,
                         request_id=request.state.request_id)
    if r.get("ok"):
        return _back(f"Created {r['created_entity_type']} #{r['created_entity_id']} — {r['name']}.")
    return _back(f"Not approved ({r.get('reason')}).")


@router.post("/documents/entity-proposals/reject")
def reject(request: Request, document_id: int = Form(...), reason: str = Form(""),
           principal: Principal = Depends(require_capability("client.write"))):
    reject_proposal(document_id, principal=principal, request_id=request.state.request_id, reason=reason)
    return _back(f"Rejected document {document_id}; it will not re-propose.")


@router.post("/documents/entity-proposals/assign-existing")
def assign_existing(request: Request, document_id: int = Form(...), entity_type: str = Form(...),
                    entity_id: int = Form(...),
                    principal: Principal = Depends(require_capability("client.write"))):
    r = assign_existing_instead(document_id, entity_type, entity_id, principal=principal,
                                request_id=request.state.request_id)
    if r.get("ok") and r.get("assigned"):
        return _back(f"Assigned document {document_id} to existing {entity_type} #{entity_id}.")
    return _back(f"Not assigned ({r.get('reason', 'error')}).")
