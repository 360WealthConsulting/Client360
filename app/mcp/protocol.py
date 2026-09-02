"""MCP protocol handling — JSON-RPC 2.0 over any transport.

Implements the subset of MCP that a read-only tool server needs: ``initialize``,
``notifications/initialized``, ``ping``, ``tools/list`` and ``tools/call``. Transport-agnostic by
design: :func:`handle_message` takes a decoded message and an authenticated
:class:`~app.mcp.tokens.McpToken` and returns a response dict (or None for a notification), so the
HTTP route and the stdio runner share one implementation and cannot drift in what they enforce.

No MCP SDK dependency. The wire format here is small, stable and fully specified, and the alternative
was adding a package to a firm's production dependency tree for a few dozen lines of dispatch.

TOOL ERRORS ARE RESULTS, NOT TRANSPORT FAILURES. A denied or malformed ``tools/call`` comes back as a
successful JSON-RPC result carrying ``isError: true``, per the MCP specification — that is how the
model SEES the refusal and can explain it, rather than the client swallowing a protocol error. The
denial is audited either way.
"""
from __future__ import annotations

import json
import logging

from app.mcp import audit as mcp_audit
from app.mcp import auth as mcp_auth
from app.mcp.config import PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION
from app.mcp.errors import McpError, McpInvalidInput
from app.mcp.tools import TOOLS, tool_definitions

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600
PARSE_ERROR = -32700


def _result(request_id, payload: dict) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": payload}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": {"code": code,
                                                                    "message": message}}


def _tool_content(payload: dict, *, is_error: bool = False) -> dict:
    """An MCP tool result. Structured data goes out as JSON text plus ``structuredContent``.

    Both, deliberately: ``structuredContent`` is what a modern client parses, and the text block is
    what older clients and human transcript readers see. They are the same object.
    """
    body = {"content": [{"type": "text", "text": json.dumps(payload, default=str, indent=2)}]}
    if is_error:
        body["isError"] = True
    else:
        body["structuredContent"] = payload
    return body


def server_info() -> dict:
    """The ``initialize`` result: what this server is and what it offers.

    Only ``tools`` is advertised. No resources, no prompts, and crucially no ``sampling`` — this
    server never asks the client's model to generate anything on its behalf.
    """
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": (
            "Read-only access to Client360 clients and documents. All results are limited to the "
            "records the authenticated staff member is permitted to see. This server cannot create, "
            "modify or delete anything."),
    }


def call_tool(token, name: str, arguments: dict, *, context: dict | None = None) -> dict:
    """Authorize, run and audit one tool call. Returns the MCP tool-result body.

    Every exit path is audited exactly once, with the outcome and — for a denial — the fixed reason.
    Arguments are never audited: they carry the caller's free text, which can contain anything they
    pasted into the assistant.
    """
    context = context or {}
    spec = TOOLS.get(name)
    audit_kwargs = {
        "actor_user_id": token.principal.user_id,
        "token_id": token.token_id,
        "request_id": context.get("request_id"),
        "ip_address": context.get("ip_address"),
        "user_agent": context.get("user_agent"),
    }
    if spec is None:
        mcp_audit.log_call(tool=name, outcome="denied", detail="Unknown tool", **audit_kwargs)
        return _tool_content({"error": "unknown_tool",
                              "message": f"No such tool: {name}"}, is_error=True)

    if not isinstance(arguments, dict):
        mcp_audit.log_call(tool=name, outcome="failure", detail="Arguments must be an object",
                           **audit_kwargs)
        return _tool_content({"error": "invalid_arguments",
                              "message": "arguments must be an object"}, is_error=True)

    try:
        mcp_auth.authorize(token, scope=spec["scope"])
        payload = spec["handler"](token, arguments)
    except McpError as exc:
        outcome = "failure" if isinstance(exc, McpInvalidInput) else "denied"
        mcp_audit.log_call(tool=name, outcome=outcome, detail=exc.message,
                           target_type=_target_type(arguments), target_id=_target_id(arguments),
                           **audit_kwargs)
        return _tool_content({"error": type(exc).__name__, "message": exc.message}, is_error=True)
    except Exception:  # noqa: BLE001 — never leak an internal error's text to the model
        logger.exception("MCP tool %s failed", name)
        mcp_audit.log_call(tool=name, outcome="failure", detail="Internal error", **audit_kwargs)
        return _tool_content({"error": "internal_error",
                              "message": "The request could not be completed."}, is_error=True)

    mcp_audit.log_call(tool=name, outcome="success",
                       target_type=_target_type(arguments), target_id=_target_id(arguments),
                       result_count=_result_count(payload), **audit_kwargs)
    return _tool_content(payload)


def _target_type(arguments: dict) -> str | None:
    """Which kind of record a call was aimed at — for the audit trail, not for logic."""
    if "document_id" in arguments:
        return "document"
    if "entity_type" in arguments and isinstance(arguments.get("entity_type"), str):
        return arguments["entity_type"]
    if "person_id" in arguments:
        return "person"
    if "household_id" in arguments:
        return "household"
    return None


def _target_id(arguments: dict):
    for key in ("document_id", "entity_id", "person_id", "household_id"):
        if arguments.get(key) is not None:
            return arguments[key]
    return None


def _result_count(payload):
    if isinstance(payload, dict) and isinstance(payload.get("count"), int):
        return payload["count"]
    return None


def handle_message(message, token, *, context: dict | None = None) -> dict | None:
    """Dispatch one decoded JSON-RPC message. Returns the response, or None for a notification."""
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "Request must be a JSON object")

    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, INVALID_REQUEST, "params must be an object")
    is_notification = "id" not in message

    # Notifications carry no id and MUST NOT be answered — checked BEFORE any method dispatch, so
    # the rule holds for every method rather than for the ones that happen to fall through.
    # "initialized" is the client telling us the handshake is done; anything else unrecognised is
    # ignored the same way rather than erroring, so a chattier client cannot break the session.
    if is_notification:
        return None

    if method == "initialize":
        return _result(request_id, server_info())

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": tool_definitions()})

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _error(request_id, INVALID_REQUEST, "tools/call requires a tool name")
        return _result(request_id, call_tool(token, name, params.get("arguments") or {},
                                             context=context))

    return _error(request_id, METHOD_NOT_FOUND, f"Method not supported: {method}")


def handle_payload(payload, token, *, context: dict | None = None):
    """Dispatch a decoded request body: one message, or a JSON-RPC batch.

    Returns the response object, a list of responses, or None when every message in the batch was a
    notification (the transport then answers with an empty 202-style response).
    """
    if isinstance(payload, list):
        if not payload:
            return _error(None, INVALID_REQUEST, "Batch must not be empty")
        responses = [r for r in (handle_message(m, token, context=context) for m in payload)
                     if r is not None]
        return responses or None
    return handle_message(payload, token, context=context)
