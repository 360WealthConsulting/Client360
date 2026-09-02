"""MCP scopes — the explicit, MCP-specific grant layer.

A scope is NOT a substitute for the firm's RBAC; it is a narrowing of it. Reaching a tool requires
BOTH the scope on the presented token AND the underlying Client360 capabilities the same action
needs in the web UI. A token can therefore only ever expose a subset of what its owner could already
see by signing in, and an administrator can issue a document-metadata-only token to an assistant
without also handing over OCR text.

Mapping, deliberately narrow (three scopes, not one per tool):

  client:read            search_clients, get_client
  document:read          list_client_documents, get_document, search_documents
  document:content:read  get_document_text
"""
from __future__ import annotations

CLIENT_READ = "client:read"
DOCUMENT_READ = "document:read"
DOCUMENT_CONTENT_READ = "document:content:read"

#: Every scope this build understands. An unknown scope on a token is ignored, never honoured.
ALL_SCOPES: tuple[str, ...] = (CLIENT_READ, DOCUMENT_READ, DOCUMENT_CONTENT_READ)

#: The door capability. Every MCP request requires it, whatever the tool.
CAPABILITY_MCP_ACCESS = "mcp.access"

#: Capabilities each scope additionally requires of the token owner's Principal. Both the MCP-specific
#: capability AND the ordinary app capability must be held — the MCP one says "this person may use the
#: assistant channel", the app one says "this person may read this kind of thing at all".
SCOPE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    CLIENT_READ: ("mcp.client.read", "client.read"),
    DOCUMENT_READ: ("mcp.document.read", "documents.view"),
    DOCUMENT_CONTENT_READ: ("mcp.document.content.read", "documents.view"),
}


def normalize_scopes(raw) -> tuple[str, ...]:
    """The recognised scopes in ``raw``, de-duplicated and ordered.

    Anything unrecognised is DROPPED rather than carried: a typo'd or future scope on a stored token
    must never widen access on an older build that does not know what it means.
    """
    if not raw:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    try:
        items = {str(s).strip() for s in raw}
    except TypeError:
        return ()
    return tuple(s for s in ALL_SCOPES if s in items)
