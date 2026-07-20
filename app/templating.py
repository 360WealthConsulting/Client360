"""Shared server-rendering helpers (Release 0.9.12, Phase 0).

A single Jinja2 environment plus content-negotiated error rendering, so browser
users get styled error pages while API/JSON clients keep the existing JSON
bodies. Error responses fall back to JSON for anything that isn't an HTML
navigation.
"""
from datetime import datetime

from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse


def human_datetime(value):
    """Display a datetime/date for staff: 'Jul 20, 2026 2:03 PM' / 'Jul 20, 2026'.

    Returns '' for falsy values and str(value) for anything without strftime, so it is safe to
    apply to any timeline/notes/task field regardless of type.
    """
    if not value:
        return ""
    try:
        if isinstance(value, datetime):
            return value.strftime("%b %-d, %Y %-I:%M %p")
        return value.strftime("%b %-d, %Y")
    except (ValueError, AttributeError):
        return str(value)


def install_filters(instance: Jinja2Templates) -> None:
    """Register Client360's shared Jinja filters on a Jinja2Templates instance."""
    instance.env.filters["humandt"] = human_datetime


templates = Jinja2Templates(directory="app/templates")
install_filters(templates)

# Statuses that have a styled template under templates/errors/.
_ERROR_TEMPLATES = {403, 404, 500}


def wants_html(request) -> bool:
    """True for browser navigations (HTML accepted) that are not API calls."""
    accept = request.headers.get("accept", "")
    return "text/html" in accept and not request.url.path.startswith("/api")


def render_error(request, status_code: int, *, detail=None):
    """Styled HTML error page for browsers, JSON otherwise."""
    request_id = getattr(request.state, "request_id", None)
    if status_code in _ERROR_TEMPLATES and wants_html(request):
        return templates.TemplateResponse(
            request=request,
            name=f"errors/{status_code}.html",
            context={"detail": detail if isinstance(detail, str) else None, "request_id": request_id},
            status_code=status_code,
        )
    body = {"detail": detail if detail is not None else "Error"}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(body, status_code=status_code)
