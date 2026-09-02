"""Runtime configuration for the MCP interface.

Every switch defaults to the SAFE value: the interface is off, bounded small, and short-lived. A
deployment that sets nothing exposes nothing — enabling MCP is an explicit act (see
docs/mcp/README.md).
"""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}

#: MCP protocol revision this server implements and negotiates.
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "client360"
SERVER_VERSION = "0.13.0"

#: Hard ceilings. A tool may never return more than these, whatever the caller asks for — an LLM
#: client that asks for 10000 rows gets DEFAULT_LIMIT-shaped pages, not the firm's whole book.
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
#: Extracted document text is truncated to this many characters, with the truncation declared in the
#: payload. Keeps one scanned 200-page return from flooding a model context.
MAX_TEXT_CHARS = 20000


def mcp_enabled() -> bool:
    """Master switch. Default OFF: the HTTP transport 404s until this is set."""
    return os.getenv("CLIENT360_MCP_ENABLED", "false").strip().lower() in _TRUE


def token_ttl_hours() -> int:
    """Lifetime of a newly issued MCP token. Default 12h — a working day, not a standing key."""
    try:
        return max(1, int(os.getenv("CLIENT360_MCP_TOKEN_TTL_HOURS", "12")))
    except ValueError:
        return 12


def clamp_limit(value, *, default: int = DEFAULT_LIMIT) -> int:
    """Coerce a caller-supplied limit into [1, MAX_LIMIT]. Junk falls back to ``default``."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_LIMIT, n))
