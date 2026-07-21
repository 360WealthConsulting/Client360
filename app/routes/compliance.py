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
from app.services.compliance import authority_admin as aa
from app.services.compliance import reviews as svc

router = APIRouter(prefix="/compliance", tags=["compliance"])
templates = Jinja2Templates(directory="app/templates")


async def _read_form(request: Request):
    """Parse a urlencoded POST body and return a single-value field reader that trims
    whitespace and defaults to ``""`` (the exact behavior every POST route used)."""
    form = parse_qs((await request.body()).decode("utf-8"))

    def one(key):
        return form.get(key, [""])[0].strip()

    return one


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
    _one = await _read_form(request)

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
    _one = await _read_form(request)

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
    _one = await _read_form(request)

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


# --------------------------------------------------------------------------- #
# Reviewer Authority administration (Phase D.8). Read is a distinct capability
# from decide; every write requires compliance.authority.manage. All actions are
# explicit (no inline/bulk/delete), server-side, with expected_status guards.
# --------------------------------------------------------------------------- #


@router.get("/authorities", response_class=HTMLResponse)
def authority_list(
    request: Request, q: str | None = None, status: str | None = None,
    sort: str = "recorded_at", desc: bool = True, page: int = 1,
    principal: Principal = Depends(require_capability("compliance.authority.read")),
):
    result = aa.list_authorities(search=q, status=status, sort=sort, descending=desc, page=page)
    return templates.TemplateResponse(request=request, name="compliance/authority_list.html", context={
        "principal": principal, "result": result,
        "filters": {"q": q or "", "status": status or "", "sort": sort, "desc": desc},
        "can_manage": principal.can("compliance.authority.manage"),
    })


@router.get("/authorities/new", response_class=HTMLResponse)
def authority_new(
    request: Request,
    principal: Principal = Depends(require_capability("compliance.authority.manage")),
):
    return templates.TemplateResponse(request=request, name="compliance/authority_new.html",
                                      context={"principal": principal})


@router.post("/authorities")
async def authority_create(
    request: Request,
    principal: Principal = Depends(require_capability("compliance.authority.manage")),
):
    _one = await _read_form(request)

    try:
        pid = int(_one("principal_id"))
    except ValueError as exc:
        raise HTTPException(400, "principal_id required") from exc
    scope = [s.strip() for s in _one("authority_scope").replace(",", " ").split() if s.strip()]
    try:
        row = aa.create_draft(
            principal.user_id, principal_id=pid, reviewer_role=_one("reviewer_role"),
            reviewer_name=_one("reviewer_name") or None, authority_scope=scope,
            effective_date=_one("effective_date") or None,
            expiration_date=_one("expiration_date") or None,
            source_reference=_one("source_reference") or None,
            evidence_description=_one("evidence_description") or None)
    except aa.SelfAdministrationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except aa.UnknownPrincipalError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(url=f"/compliance/authorities/{row['id']}", status_code=303)


@router.get("/authorities/{authority_id}", response_class=HTMLResponse)
def authority_detail(
    request: Request, authority_id: int,
    principal: Principal = Depends(require_capability("compliance.authority.read")),
):
    row = aa.get_authority(authority_id)
    if row is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="compliance/authority_detail.html", context={
        "principal": principal, "a": row,
        "can_manage": principal.can("compliance.authority.manage"),
    })


def _authority_action(action):
    async def handler(
        request: Request, authority_id: int,
        principal: Principal = Depends(require_capability("compliance.authority.manage")),
    ):
        _one = await _read_form(request)

        kwargs = {"expected_status": _one("expected_status")}
        try:
            if action == "activate":
                aa.activate(principal.user_id, authority_id, **kwargs)
            elif action == "suspend":
                aa.suspend(principal.user_id, authority_id, reason=_one("reason"), **kwargs)
            elif action == "restore":
                aa.restore(principal.user_id, authority_id, **kwargs)
            elif action == "revoke":
                aa.revoke(principal.user_id, authority_id, reason=_one("reason"), **kwargs)
            elif action == "supersede":
                scope = [s.strip() for s in _one("authority_scope").replace(",", " ").split() if s.strip()]
                aa.supersede(
                    principal.user_id, authority_id,
                    reviewer_role=_one("reviewer_role") or None,
                    reviewer_name=_one("reviewer_name") or None,
                    authority_scope=scope or None,
                    effective_date=_one("effective_date") or None,
                    expiration_date=_one("expiration_date") or None,
                    source_reference=_one("source_reference") or None,
                    evidence_description=_one("evidence_description") or None,
                    reason=_one("reason") or None, **kwargs)
        except aa.SelfAdministrationError as exc:
            raise HTTPException(403, str(exc)) from exc
        except aa.StaleAuthorityError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (aa.InvalidTransitionError, aa.IncompleteEvidenceError, aa.ScopeConflictError, aa.AuthorityError) as exc:
            return RedirectResponse(url=f"/compliance/authorities/{authority_id}?error={exc}", status_code=303)
        return RedirectResponse(url=f"/compliance/authorities/{authority_id}", status_code=303)
    return handler


router.add_api_route("/authorities/{authority_id}/activate", _authority_action("activate"), methods=["POST"])
router.add_api_route("/authorities/{authority_id}/suspend", _authority_action("suspend"), methods=["POST"])
router.add_api_route("/authorities/{authority_id}/restore", _authority_action("restore"), methods=["POST"])
router.add_api_route("/authorities/{authority_id}/revoke", _authority_action("revoke"), methods=["POST"])
router.add_api_route("/authorities/{authority_id}/supersede", _authority_action("supersede"), methods=["POST"])
