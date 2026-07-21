"""Compliance Review workspace routes (Phase D.7).

Read-only queue + review detail, and the human-controlled review actions (submit,
assign, decide). Every endpoint is gated server-side by a distinct capability
(``compliance.review.read/submit/assign/decide``) via ``require_capability`` — the
primary enforcement across the app. `/compliance` is deliberately NOT registered as a
firm-wide collection (the queue is book-scoped in the service), and NOT given a
middleware ``.read`` rule (the ``.read→.write`` inference would demand a nonexistent
``compliance.review.write`` on POST); route-level capabilities are the gate.

Decision controls render only to a principal holding ``compliance.review.decide``.
No bulk approvals, no inline approval from the queue, no approval from the Advisor
Workspace, no silent status changes.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.compliance import reviews as svc

router = APIRouter(prefix="/compliance", tags=["compliance"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/reviews", response_class=HTMLResponse)
def review_queue(
    request: Request,
    q: str | None = None, status: str | None = None, gate: str | None = None,
    rec_type: str | None = None, sort: str = "submitted_at", desc: bool = True,
    page: int = 1,
    principal: Principal = Depends(require_capability("compliance.review.read")),
):
    result = svc.list_reviews(
        principal, search=q, status=status, policy_gate=gate,
        recommendation_type=rec_type, sort=sort, descending=desc, page=page)
    return templates.TemplateResponse(request=request, name="compliance/queue.html", context={
        "principal": principal, "result": result,
        "filters": {"q": q or "", "status": status or "", "gate": gate or "",
                    "rec_type": rec_type or "", "sort": sort, "desc": desc},
        "can_decide": principal.can("compliance.review.decide"),
    })


@router.get("/reviews/{review_id}", response_class=HTMLResponse)
def review_detail(
    request: Request, review_id: int,
    principal: Principal = Depends(require_capability("compliance.review.read")),
):
    review = svc.get_review(principal, review_id)
    if review is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="compliance/review_detail.html", context={
        "principal": principal, "r": review,
        "can_decide": principal.can("compliance.review.decide"),
        "can_assign": principal.can("compliance.review.assign"),
        "decision_types": ("approved", "approved_with_conditions", "returned", "declined"),
    })


@router.post("/reviews")
async def submit(
    request: Request,
    principal: Principal = Depends(require_capability("compliance.review.submit")),
):
    form = parse_qs((await request.body()).decode("utf-8"))

    def _one(key):
        return form.get(key, [""])[0].strip()

    try:
        person_id = int(_one("person_id"))
    except ValueError as exc:
        raise HTTPException(400, "person_id required") from exc
    try:
        review = svc.submit_review(
            principal, person_id=person_id, recommendation_id=_one("recommendation_id"),
            actor_user_id=principal.user_id)
    except svc.IneligibleRecommendationError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(url=f"/compliance/reviews/{review['id']}", status_code=303)


@router.post("/reviews/{review_id}/assign")
async def assign(
    request: Request, review_id: int,
    principal: Principal = Depends(require_capability("compliance.review.assign")),
):
    form = parse_qs((await request.body()).decode("utf-8"))

    def _one(key):
        return form.get(key, [""])[0].strip()

    reviewer_principal_id = _one("reviewer_principal_id")
    try:
        svc.assign_reviewer(
            principal, review_id, expected_status=_one("expected_status"),
            reviewer_principal_id=int(reviewer_principal_id) if reviewer_principal_id else None,
            reviewer_role=_one("reviewer_role") or "compliance_reviewer",
            reviewer_name=_one("reviewer_name") or None, actor_user_id=principal.user_id)
    except svc.StaleReviewError as exc:
        raise HTTPException(409, str(exc)) from exc
    except svc.InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(url=f"/compliance/reviews/{review_id}", status_code=303)


@router.post("/reviews/{review_id}/decision")
async def decide(
    request: Request, review_id: int,
    principal: Principal = Depends(require_capability("compliance.review.decide")),
):
    form = parse_qs((await request.body()).decode("utf-8"))

    def _one(key):
        return form.get(key, [""])[0].strip()

    try:
        svc.record_decision(
            principal, review_id, decision=_one("decision"),
            expected_status=_one("expected_status"), actor_user_id=principal.user_id,
            scope_reviewed=_one("scope_reviewed") or None,
            comments=_one("comments") or None, exceptions=_one("exceptions") or None,
            reviewer_role=_one("reviewer_role") or None,
            reviewer_name=_one("reviewer_name") or None)
    except svc.DecisionValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except svc.StaleReviewError as exc:
        raise HTTPException(409, str(exc)) from exc
    except svc.InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except svc.ApprovalBlockedError as exc:
        # The review is moved to blocked_pending_authorized_reviewer; explain why.
        return RedirectResponse(
            url=f"/compliance/reviews/{review_id}?blocked={exc}", status_code=303)
    return RedirectResponse(url=f"/compliance/reviews/{review_id}", status_code=303)
