"""Shared server-rendering helpers (Release 0.9.12, Phase 0).

A single Jinja2 environment plus content-negotiated error rendering, so browser
users get styled error pages while API/JSON clients keep the existing JSON
bodies. Error responses fall back to JSON for anything that isn't an HTML
navigation.
"""
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse

templates = Jinja2Templates(directory="app/templates")

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
