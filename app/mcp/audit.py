"""Audit logging for the MCP surface.

Every call is recorded on the EXISTING tamper-evident audit chain (``app.security.audit``), so MCP
activity sits in the same ledger, and under the same hash chain, as everything else staff do. The
required fields — timestamp, authenticated actor, tool, target entity/document, outcome — are the
chain's own columns plus a small metadata object.

What is recorded is REFERENCES ONLY: ids, a tool name, counts, an outcome. Never document text,
never a client's name, never the query string a user typed (it can carry an SSN a caller pasted in),
never a token or any part of one. ``redact_metadata`` in the audit writer is a second net under
that, not the first one.

Auditing never breaks a call: a failure to write the event is swallowed and the tool result stands.
The alternative — turning a successful read into a 500 because the ledger hiccuped — is worse, and
the denial path in particular must not be able to fail open into an exception.
"""
from __future__ import annotations

import logging
import uuid

from app.security.audit import write_audit_event

logger = logging.getLogger(__name__)

ACTION = "mcp.tool.call"
ENTITY_TYPE = "mcp_tool"


def log_call(*, tool: str, outcome: str, actor_user_id: int | None = None,
             target_type: str | None = None, target_id=None, request_id: str | None = None,
             ip_address: str | None = None, user_agent: str | None = None,
             detail: str | None = None, result_count: int | None = None,
             token_id: int | None = None) -> None:
    """Append one MCP call to the audit chain. Best-effort; never raises."""
    metadata = {"tool": tool}
    if target_type:
        metadata["target_type"] = target_type
    if target_id is not None:
        metadata["target_id"] = str(target_id)
    if result_count is not None:
        metadata["result_count"] = int(result_count)
    if token_id is not None:
        metadata["mcp_token_id"] = int(token_id)
    if detail:
        # A short, fixed reason ("Scope required: document:read") — never caller-supplied content.
        metadata["detail"] = detail[:200]
    try:
        write_audit_event(
            action=ACTION, entity_type=ENTITY_TYPE, entity_id=tool,
            request_id=request_id or f"mcp-{uuid.uuid4().hex[:12]}",
            actor_user_id=actor_user_id, outcome=outcome,
            ip_address=ip_address, user_agent=user_agent, metadata=metadata)
    except Exception:  # noqa: BLE001 — auditing must never turn a read into an error
        logger.warning("MCP audit write failed for tool=%s outcome=%s", tool, outcome,
                       exc_info=True)
