from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.tax_document_intelligence import (
    StaleReviewError, checklist_view, compute_missing, documents_view, review_action,
    review_queue,
)
from app.services.tax_domain import list_engagements

router = APIRouter(tags=["tax-document-intelligence"])
templates = Jinja2Templates(directory="app/templates")


class ReviewDecision(BaseModel):
    return_id: Optional[int] = None
    checklist_item_id: Optional[int] = None
    category: Optional[str] = None
    reason: Optional[str] = None


def _authorized(principal, return_id):
    if return_id not in {r["return_id"] for r in list_engagements(principal)}:
        raise HTTPException(404, "Tax return not found")


# --- Staff read views (tax.read) -------------------------------------------

@router.get("/tax/documents")
def workspace(request: Request, principal: Principal = Depends(require_capability("tax.read"))):
    return templates.TemplateResponse(request=request, name="tax/document_review.html",
        context={"data": review_queue(principal), "principal": principal})


@router.get("/api/v1/tax/returns/{return_id}/checklist")
def api_checklist(return_id: int, principal: Principal = Depends(require_capability("tax.read"))):
    _authorized(principal, return_id)
    return checklist_view(return_id)


@router.get("/api/v1/tax/returns/{return_id}/documents")
def api_documents(return_id: int, principal: Principal = Depends(require_capability("tax.read"))):
    _authorized(principal, return_id)
    return documents_view(return_id)


@router.get("/api/v1/tax/documents/review")
def api_review_queue(status: str = "proposed", principal: Principal = Depends(require_capability("tax.read"))):
    return review_queue(principal, status=status)


@router.post("/api/v1/tax/returns/{return_id}/missing/recompute")
def api_recompute(return_id: int, principal: Principal = Depends(require_capability("tax.write"))):
    _authorized(principal, return_id)
    return compute_missing(return_id)


# --- Reviewer actions (tax.document.review) --------------------------------

def _action(link_id, action, payload, request, principal):
    try:
        return review_action(link_id, action, principal=principal, request=request,
            return_id=payload.return_id, checklist_item_id=payload.checklist_item_id,
            category=payload.category, reason=payload.reason)
    except StaleReviewError as exc:
        raise HTTPException(409, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/api/v1/tax/documents/{link_id}/accept")
def api_accept(link_id: int, payload: ReviewDecision, request: Request,
               principal: Principal = Depends(require_capability("tax.document.review"))):
    return _action(link_id, "accept", payload, request, principal)


@router.post("/api/v1/tax/documents/{link_id}/reject")
def api_reject(link_id: int, payload: ReviewDecision, request: Request,
               principal: Principal = Depends(require_capability("tax.document.review"))):
    return _action(link_id, "reject", payload, request, principal)


@router.post("/api/v1/tax/documents/{link_id}/reassign")
def api_reassign(link_id: int, payload: ReviewDecision, request: Request,
                 principal: Principal = Depends(require_capability("tax.document.review"))):
    if payload.return_id is None:
        raise HTTPException(400, "return_id is required for reassignment")
    return _action(link_id, "reassign", payload, request, principal)


@router.post("/api/v1/tax/documents/{link_id}/classify")
def api_classify(link_id: int, payload: ReviewDecision, request: Request,
                 principal: Principal = Depends(require_capability("tax.document.review"))):
    return _action(link_id, "classify", payload, request, principal)


@router.post("/api/v1/tax/documents/{link_id}/duplicate")
def api_duplicate(link_id: int, payload: ReviewDecision, request: Request,
                  principal: Principal = Depends(require_capability("tax.document.review"))):
    return _action(link_id, "duplicate", payload, request, principal)


@router.post("/api/v1/tax/documents/{link_id}/revert")
def api_revert(link_id: int, payload: ReviewDecision, request: Request,
               principal: Principal = Depends(require_capability("tax.document.review"))):
    return _action(link_id, "revert", payload, request, principal)
