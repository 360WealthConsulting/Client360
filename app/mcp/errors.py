"""MCP error taxonomy.

Mapped onto JSON-RPC error codes by ``app.mcp.protocol``. Messages are deliberately coarse: a caller
learns THAT it was denied, never whether the entity it named exists. ``McpDenied`` is therefore used
for both "you may not" and "there is no such record in your scope" — distinguishing them would make
the MCP surface an existence oracle over the firm's client list.
"""
from __future__ import annotations


class McpError(Exception):
    """Base class. ``code`` is the JSON-RPC error code used on the wire."""

    code = -32000

    def __init__(self, message: str = "MCP error"):
        super().__init__(message)
        self.message = message


class McpUnauthenticated(McpError):
    """No usable credential was presented."""

    code = -32001

    def __init__(self, message: str = "Authentication required"):
        super().__init__(message)


class McpDenied(McpError):
    """Authenticated, but not permitted — or the target is outside the caller's record scope."""

    code = -32002

    def __init__(self, message: str = "Not permitted"):
        super().__init__(message)


class McpInvalidInput(McpError):
    """The arguments did not satisfy the tool's contract (bad id, unknown enum, missing field)."""

    code = -32602

    def __init__(self, message: str = "Invalid arguments"):
        super().__init__(message)


class McpUnavailable(McpError):
    """The interface is switched off, or a dependency it needs is not installed."""

    code = -32003

    def __init__(self, message: str = "MCP interface unavailable"):
        super().__init__(message)
