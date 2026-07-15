from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.services.dashboard import get_dashboard_data
from app.services.exception_reporting import dashboard_summary


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/health")
def health():
    return {
        "status": "ok",
        "application": "Client360",
    }


@router.get("/api/stats")
def stats():
    return get_dashboard_data()


@router.get("/")
def advisor_dashboard(request: Request):
    dashboard = get_dashboard_data()
    principal = getattr(request.state, "principal", None)

    return templates.TemplateResponse(
        request=request,
        name="dashboard/index.html",
        context={
            "dashboard": dashboard,
            "exception_summary": dashboard_summary(principal, audience="advisor"),
        },
    )
