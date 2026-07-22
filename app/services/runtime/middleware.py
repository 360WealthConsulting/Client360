"""RuntimeContextMiddleware (Phase D.28) — attaches one immutable runtime context per request.

Runs after ``AuthenticationMiddleware`` (registered before it, so it is inner in the request path and
``request.state.principal`` is already set). It attaches a lazily-built, cached ``RuntimeContext`` to
``request.state.runtime_context`` so the whole request shares one resolution — no repeated
configuration resolution during a request. It is fully guarded: a runtime failure never affects the
request (the context degrades to ``EMPTY_CONTEXT``). It NEVER edits metadata.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware

from .context import EMPTY_CONTEXT


class RuntimeContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Attach a lazily-resolved runtime context accessor. The heavy work happens only if a route
        # actually reads the context (via the dependency), keeping public/static paths cheap.
        request.state.runtime_context = None
        try:
            request.state.runtime_context_ready = True
        except Exception:
            request.state.runtime_context = EMPTY_CONTEXT
        return await call_next(request)


def resolve_request_context(request):
    """Build (once) and cache the immutable runtime context for this request. Idempotent per request;
    safe to call from a dependency. Never raises."""
    existing = getattr(request.state, "runtime_context", None)
    if existing is not None:
        return existing
    try:
        from . import engine as runtime_engine
        principal = getattr(request.state, "principal", None)
        ctx = runtime_engine.context_for(principal)
    except Exception:
        ctx = EMPTY_CONTEXT
    try:
        request.state.runtime_context = ctx
    except Exception:
        pass
    return ctx
