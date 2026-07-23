"""Advisor AI Assist routes (Phase D.42) — all READ-ONLY.

The query endpoint uses POST only to carry a request body; it creates NO business record and never
mutates. Briefs consume the scope-guarded D.38–D.41 summaries; person/household briefs 404 out of scope.
Diagnostics reuse ``observability.audit``. Every response is labelled "Advisor Assist — Review Required".
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.authorization import record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.ai_assist import assistant
from app.services.ai_assist.common import as_json

router = APIRouter(tags=["ai-assist"])
templates = Jinja2Templates(directory="app/templates")


def _page(request, principal, *, brief, query_result=None):
    return templates.TemplateResponse(request=request, name="ai_assist/assist.html", context={
        "principal": principal, "brief": brief, "query_result": query_result})


def _brief_page(request, principal, brief, title):
    return templates.TemplateResponse(request=request, name="ai_assist/brief.html", context={
        "principal": principal, "brief": brief, "title": title})


@router.get("/workspace/assist", response_class=HTMLResponse)
def assist_home(request: Request, principal: Principal = Depends(require_capability("client.read"))):
    """Advisor daily AI Assist — a read-only briefing entry point (does not replace any widget/home)."""
    return _page(request, principal, brief=assistant.daily_brief(principal))


@router.post("/workspace/assist/query")
async def assist_query(request: Request,
                       principal: Principal = Depends(require_capability("client.read"))):
    """Bounded factual question answering — READ-ONLY (POST carries the body; creates no record)."""
    form = await request.form()
    question = str(form.get("question") or "").strip()
    person_id = int(form["person_id"]) if str(form.get("person_id") or "").isdigit() else None
    household_id = int(form["household_id"]) if str(form.get("household_id") or "").isdigit() else None
    result = assistant.answer(principal, question, person_id=person_id, household_id=household_id)
    if str(form.get("format") or "") == "json":
        return JSONResponse(as_json(result))
    return _page(request, principal, brief=assistant.daily_brief(principal), query_result=result)


@router.get("/workspace/assist/diagnostics")
def assist_diagnostics_route(
        principal: Principal = Depends(require_capability("observability.audit"))):
    """AI Assist diagnostics + governance (JSON). No prompt contents, secrets, or client payloads."""
    from app.services.ai_assist.diagnostics import assist_diagnostics
    from app.services.ai_assist.governance import validate_ai_assist
    return JSONResponse(as_json({"diagnostics": assist_diagnostics(principal),
                                 "governance": validate_ai_assist(principal)}))


@router.get("/client/{person_id}/brief", response_class=HTMLResponse)
def client_brief_page(request: Request, person_id: int,
                      principal: Principal = Depends(require_capability("client.read"))):
    """Read-only AI client brief — consumes the Client 360 snapshot. 404 out of record scope."""
    if not record_in_scope(principal, "person", person_id):
        raise HTTPException(404, "Not found")
    return _brief_page(request, principal, assistant.client_brief(principal, person_id), "Client Brief")


@router.get("/client/household/{household_id}/brief", response_class=HTMLResponse)
def household_brief_page(request: Request, household_id: int,
                         principal: Principal = Depends(require_capability("client.read"))):
    """Read-only AI household brief — consumes the Household 360 snapshot. 404 out of record scope."""
    if not record_in_scope(principal, "household", household_id):
        raise HTTPException(404, "Not found")
    return _brief_page(request, principal, assistant.household_brief(principal, household_id),
                       "Household Brief")


@router.get("/workspace/meetings/{person_id}/brief", response_class=HTMLResponse)
def meeting_brief_page(request: Request, person_id: int, event: int | None = None,
                       principal: Principal = Depends(require_capability("client.read"))):
    """Read-only AI meeting-prep brief — consumes the minimized meeting brief. 404 out of record scope."""
    if not record_in_scope(principal, "person", person_id):
        raise HTTPException(404, "Not found")
    return _brief_page(request, principal, assistant.meeting_prep(principal, person_id, event_id=event),
                       "Meeting Prep")


@router.get("/work/{item_type}/{item_id}/explain", response_class=HTMLResponse)
def work_explain_page(request: Request, item_type: str, item_id: str,
                      principal: Principal = Depends(require_capability("work.read"))):
    """Read-only explanation of a work item (why it surfaced + the authoritative next step)."""
    return _brief_page(request, principal, assistant.work_explanation(principal, item_type, item_id),
                       "Work Explanation")
