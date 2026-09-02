"""HTTP transport for the read-only MCP interface (POST /mcp).

MCP's Streamable HTTP transport, in the subset a request/response tool server needs: the client
POSTs a JSON-RPC message (or batch) and reads the JSON reply. Server-initiated streaming (the SSE
half, ``GET /mcp``) is deliberately NOT implemented — this server never pushes, so offering a stream
it would never write to would only invite clients to hold connections open against the firm's app.

AUTHENTICATION IS THIS ROUTE'S OWN JOB. ``/mcp`` is listed in ``PUBLIC_EXACT`` so the staff session
middleware lets it through, exactly as the SharePoint webhook is: both authenticate a machine caller
by credential rather than by browser session. "Public" there means "no session cookie required", not
"unauthenticated" — every request here goes through ``app.mcp.auth.authenticate`` before any
dispatch, and a request with no valid token never reaches a tool.

CSRF does not apply and is not weakened by that listing: this endpoint honours no ambient credential
(no cookie, no session), so a browser cannot be made to call it with the user's authority.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.mcp import audit as mcp_audit
from app.mcp import auth as mcp_auth
from app.mcp import protocol
from app.mcp.errors import McpDenied, McpUnauthenticated, McpUnavailable

logger = logging.getLogger(__name__)
router = APIRouter()

MCP_PATH = "/mcp"
#: Bodies larger than this are refused unread. A JSON-RPC tool call is a few hundred bytes; anything
#: approaching a megabyte is a mistake or an attempt to exhaust memory before authentication.
MAX_BODY_BYTES = 256 * 1024


def _client_context(request: Request) -> dict:
    """Audit context for this request — request id, caller address, user agent."""
    return {
        "request_id": getattr(request.state, "request_id", None),
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _unauthorized(detail: str) -> JSONResponse:
    """A 401 that tells the client HOW to authenticate but never why it failed."""
    return JSONResponse({"error": "unauthorized", "detail": detail}, status_code=401,
                        headers={"WWW-Authenticate": "Bearer"})


@router.post(MCP_PATH)
async def mcp_endpoint(request: Request):
    """Handle one JSON-RPC request or batch."""
    context = _client_context(request)

    try:
        token = mcp_auth.authenticate(request.headers.get("authorization"))
    except McpUnavailable:
        # Switched off: behave as though the endpoint does not exist. A probe learns nothing about
        # whether this deployment has an MCP surface at all.
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    except McpUnauthenticated:
        mcp_audit.log_call(tool="<authenticate>", outcome="denied",
                           detail="No valid credential", **context)
        return _unauthorized("A valid MCP bearer token is required.")
    except McpDenied as exc:
        # Authenticated but not admitted — the actor is known, so the denial is attributable.
        mcp_audit.log_call(tool="<authenticate>", outcome="denied", detail=exc.message, **context)
        return JSONResponse({"error": "forbidden", "detail": exc.message}, status_code=403)

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        mcp_audit.log_call(tool="<request>", outcome="failure", detail="Request body too large",
                           actor_user_id=token.principal.user_id, token_id=token.token_id, **context)
        return JSONResponse({"error": "payload_too_large",
                             "detail": "Request body exceeds the permitted size."}, status_code=413)
    try:
        payload = json.loads(raw or b"")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": protocol.PARSE_ERROR, "message": "Invalid JSON"}}, status_code=400)

    response = protocol.handle_payload(payload, token, context=context)
    if response is None:
        # Every message was a notification; JSON-RPC forbids a body in reply.
        return Response(status_code=202)
    return JSONResponse(response)


@router.get(MCP_PATH)
async def mcp_stream_unsupported(request: Request):
    """MCP clients probe GET for an SSE stream. Answer honestly rather than hanging."""
    try:
        mcp_auth.authenticate(request.headers.get("authorization"))
    except McpUnavailable:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    except McpUnauthenticated:
        return _unauthorized("A valid MCP bearer token is required.")
    except McpDenied as exc:
        return JSONResponse({"error": "forbidden", "detail": exc.message}, status_code=403)
    return JSONResponse(
        {"error": "method_not_allowed",
         "detail": "This MCP server does not stream. POST JSON-RPC messages to /mcp."},
        status_code=405, headers={"Allow": "POST"})
