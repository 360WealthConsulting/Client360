"""The MCP authorization gate — default deny, five layers deep.

Every tool call passes through :func:`authorize`, and each layer can only ever REMOVE access:

  1. The interface must be switched on            (CLIENT360_MCP_ENABLED)
  2. The bearer token must resolve                (live, unexpired, unrevoked, owner active)
  3. The owner must hold ``mcp.access``           (the assistant-channel door capability)
  4. The token must carry the tool's scope        (explicit per-token grant)
  5. The owner must hold the tool's capabilities  (the same RBAC the web UI enforces)

Record scope is the sixth layer and is NOT enforced here: it belongs to the service layer each tool
delegates to (``accessible_person_ids`` / ``record_in_scope`` / the document platform's scope
clause), which already applies it per row. Re-implementing it here would create a second, drifting
copy of the firm's permission boundary.
"""
from __future__ import annotations

from app.mcp import config as mcp_config
from app.mcp import tokens as mcp_tokens
from app.mcp.errors import McpDenied, McpUnauthenticated, McpUnavailable
from app.mcp.scopes import CAPABILITY_MCP_ACCESS, SCOPE_CAPABILITIES


def bearer_token(header_value: str | None) -> str | None:
    """The credential out of an ``Authorization: Bearer <token>`` header, or None.

    Case-insensitive on the scheme, as RFC 6750 requires. Anything that is not a well-formed Bearer
    header yields None, which the caller turns into a 401 — never a partial credential.
    """
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def authenticate(header_value: str | None) -> mcp_tokens.McpToken:
    """Layers 1-3: interface enabled, token valid, owner holds ``mcp.access``.

    Raises McpUnavailable / McpUnauthenticated / McpDenied. Never returns a partial identity.
    """
    if not mcp_config.mcp_enabled():
        raise McpUnavailable("MCP interface is disabled")
    token = mcp_tokens.resolve(bearer_token(header_value))
    if token is None:
        raise McpUnauthenticated("Authentication required")
    if not token.principal.can(CAPABILITY_MCP_ACCESS):
        raise McpDenied(f"Capability required: {CAPABILITY_MCP_ACCESS}")
    return token


def authorize(token: mcp_tokens.McpToken, *, scope: str) -> None:
    """Layers 4-5 for one tool's declared scope. Raises McpDenied; returns None on success."""
    if not token.has_scope(scope):
        raise McpDenied(f"Scope required: {scope}")
    required = SCOPE_CAPABILITIES.get(scope)
    if not required:
        # An unmapped scope is a programming error, and the safe reading of "I don't know what this
        # permits" is "it permits nothing".
        raise McpDenied(f"Scope not recognised: {scope}")
    missing = [code for code in required if not token.principal.can(code)]
    if missing:
        raise McpDenied(f"Capability required: {', '.join(missing)}")
