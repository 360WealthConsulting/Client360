"""Client360 <-> MCP (Model Context Protocol) read-only interface — Phase 1.

A THIN adapter that exposes six read-only tools over the EXISTING Client360 service and domain
layers. It owns no query logic of its own: client search goes through
``app.services.universal_search``, documents through ``app.services.document_platform.service``,
extracted text through ``app.services.document_ocr``. Every one of those already enforces record
scope from the caller's ``Principal``, so the MCP surface inherits the firm's permission boundaries
instead of re-deriving them.

What this package deliberately does NOT contain: SQL execution, filesystem access, shell access,
outbound URL fetching, a second search subsystem, and any write path whatsoever. See
``app.mcp.tools`` — the registry is read-only by construction and ``tests/test_mcp_no_mutation.py``
asserts it stays that way.
"""
