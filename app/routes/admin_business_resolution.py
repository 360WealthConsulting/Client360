"""Admin review + apply surface for derived business ownership (Release 0.13.0).

Under ``/admin`` so the ``identity.manage`` middleware gate applies (admin-only), with each route
additionally carrying its own capability dependency — ``organization.read`` for the review page and
``organization.write`` for the apply action, the same capability the canonical
``organization_service.record_ownership`` enforces underneath.

The page is the human review step between the preview resolver and the ownership graph: bucket
counts, the SAFE_OWNERSHIP candidates with their proposed canonical owners, evidence/title,
reconciliation detail, household association, and whether each candidate is already applied. Apply
is ONE business per POST — there is no bulk endpoint — and the resolver is re-run server-side before
any write, so a stale page cannot authorize an edge the current evidence no longer supports.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.business_resolution_apply import (
    BusinessResolutionApplyError,
    apply_business_resolution,
    review_overview,
)
from app.templating import install_filters

router = APIRouter(prefix="/admin", tags=["administration"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)

_PAGE = "/admin/business-resolution"


def _back(msg):
    return RedirectResponse(f"{_PAGE}?msg={msg}", status_code=303)


@router.get("/business-resolution")
def business_resolution_review(
        request: Request,
        principal: Principal = Depends(require_capability("organization.read"))):
    """READ-ONLY. Post-hardening bucket counts + every SAFE_OWNERSHIP candidate with its proposed
    canonical owner(s), evidence, reconciliation, household association, and applied state.
    Nothing is written until an Apply button is used."""
    data = review_overview()
    return templates.TemplateResponse(
        request=request, name="admin/business_resolution.html",
        context={"principal": principal, "notice": request.query_params.get("msg"), **data})


@router.post("/business-resolution/{business_id}/apply")
def business_resolution_apply(
        request: Request, business_id: int, confirm: str = Form(""),
        principal: Principal = Depends(require_capability("organization.write"))):
    """Apply the ownership proposals for exactly ONE business. Requires explicit ``confirm=yes``
    (the same mutation guard the document review queue uses, on top of the same-origin CSRF check in
    AuthenticationMiddleware). The service re-resolves before writing and refuses anything that is
    no longer SAFE_OWNERSHIP; record scope is enforced by ``record_ownership``."""
    if confirm.strip().lower() != "yes":
        return _back("Apply requires the confirmation value.")
    try:
        result = apply_business_resolution(
            principal=principal, business_id=business_id, dry_run=False,
            request_id=request.state.request_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except BusinessResolutionApplyError as exc:
        return _back(f"Business {business_id} not applied ({exc}).")
    if not result["ok"]:
        return _back(f"Business {business_id} not applied ({result['reason']}).")
    return _back(
        f"{result['business_name']}: {result['relationships_created']} ownership edge(s) created, "
        f"{result['relationships_reused']} already present.")
