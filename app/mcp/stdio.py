"""Local stdio transport — ``python -m app.mcp.stdio``.

The transport MCP desktop clients (and the MCP Inspector) speak natively: newline-delimited JSON-RPC
on stdin/stdout. Intended for LOCAL testing against a development database. Production access goes
over HTTP through the Secure MCP Tunnel (docs/mcp/README.md), because that path can be revoked,
rate-limited and monitored at the network edge; a stdio process cannot.

The credential comes from ``CLIENT360_MCP_TOKEN`` and is resolved ONCE at startup, so a token
revoked mid-session still ends the session — the process holds an McpToken, but every tool call
re-reads the owner's capabilities from the database (see ``tokens.resolve``), and the operator
restarts the client to pick up a revocation. Nothing is read from argv: a token in a command line is
a token in the process list.

STDOUT IS THE PROTOCOL CHANNEL. Every diagnostic goes to stderr; a stray print to stdout would
corrupt the JSON-RPC stream and the client would drop the connection.
"""
from __future__ import annotations

import json
import os
import sys


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        return _fail("usage: python -m app.mcp.stdio   (credential comes from CLIENT360_MCP_TOKEN)")

    from app.mcp import auth as mcp_auth
    from app.mcp import config as mcp_config
    from app.mcp import protocol
    from app.mcp.errors import McpError

    if not mcp_config.mcp_enabled():
        return _fail("MCP is disabled. Set CLIENT360_MCP_ENABLED=true to run the server.")

    raw_token = os.getenv("CLIENT360_MCP_TOKEN", "").strip()
    if not raw_token:
        return _fail("CLIENT360_MCP_TOKEN is not set. Issue one with scripts/mcp_token.py.")

    try:
        token = mcp_auth.authenticate(f"Bearer {raw_token}")
    except McpError as exc:
        return _fail(f"MCP authentication failed: {exc.message}")

    print(f"client360 MCP server ready for {token.principal.email} "
          f"(scopes: {', '.join(token.scopes) or 'none'})", file=sys.stderr)

    context = {"request_id": None, "ip_address": "stdio", "user_agent": "stdio"}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write({"jsonrpc": "2.0", "id": None,
                    "error": {"code": protocol.PARSE_ERROR, "message": "Invalid JSON"}})
            continue
        response = protocol.handle_payload(message, token, context=context)
        if response is not None:
            _write(response)
    return 0


def _write(payload) -> None:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
