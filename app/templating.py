"""Shared server-rendering helpers (Release 0.9.12, Phase 0).

A single Jinja2 environment plus content-negotiated error rendering, so browser
users get styled error pages while API/JSON clients keep the existing JSON
bodies. Error responses fall back to JSON for anything that isn't an HTML
navigation.
"""
import hashlib
import os
import pathlib
import sys
from datetime import datetime

from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

_STATIC_ROOT = pathlib.Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    """A stable identifier for the currently deployed front-end assets.

    Deterministic by construction: a digest of the stylesheet and script bytes
    themselves, so it is identical for identical assets on every worker and every
    host, and changes if and only if an asset changes. Never per-request — a random
    or timestamped value would defeat caching entirely rather than version it.

    ``CLIENT360_ASSET_VERSION`` overrides it where a deployment already has a build
    identifier it would rather stamp (a release tag or commit sha).
    """
    override = os.getenv("CLIENT360_ASSET_VERSION")
    if override:
        return override
    digest = hashlib.sha256()
    for sub in ("css", "js"):
        directory = _STATIC_ROOT / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob(f"*.{sub}")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


#: Computed once at import. Referenced by base.html as ``?v={{ asset_version }}`` so a
#: deploy can never serve new HTML against a browser's cached copy of the old stylesheet.
ASSET_VERSION = _asset_version()


def install_template_globals(instance: Jinja2Templates) -> None:
    """Install the globals every rendered page needs, on one templates instance."""
    instance.env.globals["asset_version"] = ASSET_VERSION


def install_globals_on_all_templates() -> int:
    """Install the shared globals AND filters on every ``Jinja2Templates`` constructed.

    base.html is rendered by whichever instance the handling route owns, and 63 route
    modules construct their own rather than importing the shared one here — so setting
    a global on this module's instance alone would version the stylesheet URL on some
    pages and not others. Called once from ``app.main`` after every router is imported,
    this reaches all of them. Returns the count for the startup check.

    Filters are installed here too, for the same reason: a route that built its own
    instance without calling :func:`install_filters` had no ``humandt`` and no
    ``datefmt``, so a template using either raised at render time depending only on
    which module happened to own the instance. Registering both together removes that
    coupling instead of asking 63 modules to remember.
    """
    seen, count = set(), 0
    for module in list(sys.modules.values()):
        instance = getattr(module, "templates", None)
        if isinstance(instance, Jinja2Templates) and id(instance) not in seen:
            seen.add(id(instance))
            install_filters(instance)
            count += 1
    return count


#: glibc's "no padding" strftime directives, and how to produce each value ourselves.
#: ``%-d`` and friends are a GNU extension: glibc accepts them, the Microsoft C runtime raises
#: ``ValueError: Invalid format string``. That made /workspace a 500 on Windows and silently
#: degraded every ``humandt`` value to a raw ``str(datetime)`` (the except branch below), so
#: staff saw "2026-09-02 08:55:30.493174-05:00" where a date was intended.
#:
#: Substituting the value in Python rather than switching to the MSVC-only ``%#d`` spelling
#: keeps ONE format string that renders identically on both platforms — no per-call platform
#: branch, and nothing for a template author to remember.
_NO_PAD = {
    "%-d": lambda v: v.day,
    "%-m": lambda v: v.month,
    "%-j": lambda v: v.timetuple().tm_yday,
    "%-Y": lambda v: v.year,
    "%-y": lambda v: v.year % 100,
    "%-H": lambda v: v.hour,
    "%-I": lambda v: (v.hour % 12) or 12,
    "%-M": lambda v: v.minute,
    "%-S": lambda v: v.second,
}


def format_datetime(value, fmt):
    """``strftime`` that accepts glibc's ``%-X`` directives on every platform.

    A token is only substituted when the value actually carries that field, so applying a
    time format to a ``date`` raises the same AttributeError it always would rather than
    inventing a midnight.
    """
    if not value:
        return ""
    try:
        for token, get in _NO_PAD.items():
            if token in fmt:
                # A literal '%' in the substituted text would be read as a new directive.
                fmt = fmt.replace(token, str(get(value)).replace("%", "%%"))
        return value.strftime(fmt)
    except (ValueError, AttributeError):
        return str(value)


def human_datetime(value):
    """Display a datetime/date for staff: 'Jul 20, 2026 2:03 PM' / 'Jul 20, 2026'.

    Returns '' for falsy values and str(value) for anything without strftime, so it is safe to
    apply to any timeline/notes/task field regardless of type.
    """
    if not value:
        return ""
    if isinstance(value, datetime):
        return format_datetime(value, "%b %-d, %Y %-I:%M %p")
    return format_datetime(value, "%b %-d, %Y")


def install_filters(instance: Jinja2Templates) -> None:
    """Register Client360's shared Jinja filters on a Jinja2Templates instance."""
    instance.env.filters["humandt"] = human_datetime
    #: Portable strftime for templates that need their own format string. Use this instead of
    #: calling .strftime() directly whenever the format contains a %-X directive.
    instance.env.filters["datefmt"] = format_datetime
    install_template_globals(instance)


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
