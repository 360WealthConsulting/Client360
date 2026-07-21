"""Advisor Work Management routes (Phase D.9).

A separately namespaced ``/advisor-work`` workspace (the existing ``/work`` and
``work.read`` system is untouched). Every endpoint is gated server-side by a distinct
``advisor_work.read/create/assign/update`` capability. Route-level gating (no middleware
``.read`` rule — the ``.read→.write`` inference would demand a nonexistent
``advisor_work.write``); the queue is book-scoped in the service (not firm-wide).
No bulk actions, no deletion, no silent status changes.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import advisor_work as svc

router = APIRouter(prefix="/advisor-work", tags=["advisor-work"])
templates = Jinja2Templates(directory="app/templates")


async def _read_form(request: Request):
    form = parse_qs((await request.body()).decode("utf-8"))

    def one(key):
        return form.get(key, [""])[0].strip()

    return one


@router.get("", response_class=HTMLResponse)
def work_queue(
    request: Request, q: str | None = None, status: str | None = None,
    priority: str | None = None, owner: int | None = None, rec_type: str | None = None,
    rule: str | None = None, gate: str | None = None, sort: str = "created_at",
    desc: bool = True, page: int = 1,
    principal: Principal = Depends(require_capability("advisor_work.read")),
):
    result = svc.list_work(
        principal, search=q, status=status, priority=priority, owner=owner,
        recommendation_type=rec_type, governing_rule=rule, policy_gate=gate,
        sort=sort, descending=desc, page=page)
    return templates.TemplateResponse(request=request, name="advisor_work/queue.html", context={
        "principal": principal, "result": result,
        "filters": {"q": q or "", "status": status or "", "priority": priority or "",
                    "rec_type": rec_type or "", "rule": rule or "", "gate": gate or "",
                    "sort": sort, "desc": desc},
        "can_create": principal.can("advisor_work.create"),
        "can_assign": principal.can("advisor_work.assign"),
        "can_update": principal.can("advisor_work.update"),
    })


@router.get("/{item_id}", response_class=HTMLResponse)
def work_detail(
    request: Request, item_id: int,
    principal: Principal = Depends(require_capability("advisor_work.read")),
):
    item = svc.get_work(principal, item_id)
    if item is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="advisor_work/detail.html", context={
        "principal": principal, "w": item,
        "can_assign": principal.can("advisor_work.assign"),
        "can_update": principal.can("advisor_work.update"),
    })


@router.post("")
async def create(
    request: Request,
    principal: Principal = Depends(require_capability("advisor_work.create")),
):
    _one = await _read_form(request)
    try:
        person_id = int(_one("person_id"))
    except ValueError as exc:
        raise HTTPException(400, "person_id required") from exc
    try:
        item = svc.create_from_recommendation(
            principal, person_id=person_id, recommendation_id=_one("recommendation_id"),
            actor_user_id=principal.user_id, due_date=_one("due_date") or None)
    except svc.IneligibleRecommendationError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(url=f"/advisor-work/{item['id']}", status_code=303)


@router.post("/{item_id}/assign")
async def assign(
    request: Request, item_id: int,
    principal: Principal = Depends(require_capability("advisor_work.assign")),
):
    _one = await _read_form(request)
    owner = _one("owner_principal_id")
    try:
        svc.assign(principal, item_id, expected_status=_one("expected_status"),
                   owner_principal_id=int(owner) if owner else None,
                   actor_user_id=principal.user_id)
    except svc.StaleWorkError as exc:
        raise HTTPException(409, str(exc)) from exc
    except svc.InvalidTransitionError as exc:
        return RedirectResponse(url=f"/advisor-work/{item_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/advisor-work/{item_id}", status_code=303)


def _status_route(action, target=None):
    async def handler(
        request: Request, item_id: int,
        principal: Principal = Depends(require_capability("advisor_work.update")),
    ):
        _one = await _read_form(request)
        expected = _one("expected_status")
        note = _one("note") or None
        try:
            if action == "complete":
                svc.complete(principal, item_id, completion_notes=_one("completion_notes") or None,
                             expected_status=expected, actor_user_id=principal.user_id)
            else:
                svc.update_status(principal, item_id, new_status=target, expected_status=expected,
                                  actor_user_id=principal.user_id, note=note)
        except svc.StaleWorkError as exc:
            raise HTTPException(409, str(exc)) from exc
        except svc.InvalidTransitionError as exc:
            return RedirectResponse(url=f"/advisor-work/{item_id}?error={exc}", status_code=303)
        return RedirectResponse(url=f"/advisor-work/{item_id}", status_code=303)
    return handler


router.add_api_route("/{item_id}/start", _status_route("update", "in_progress"), methods=["POST"])
router.add_api_route("/{item_id}/wait", _status_route("update", "waiting"), methods=["POST"])
router.add_api_route("/{item_id}/complete", _status_route("complete"), methods=["POST"])
router.add_api_route("/{item_id}/cancel", _status_route("update", "cancelled"), methods=["POST"])
router.add_api_route("/{item_id}/archive", _status_route("update", "archived"), methods=["POST"])
