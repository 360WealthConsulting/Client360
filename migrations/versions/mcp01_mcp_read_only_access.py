"""MCP read-only access: dedicated capabilities + a dedicated access-token store.

Phase 1 of the Client360 <-> ChatGPT MCP integration. The MCP surface is a THIN, READ-ONLY adapter
over the existing service layers; this migration adds only what that surface cannot borrow.

WHY A SEPARATE TOKEN TABLE. MCP callers are long-lived machine clients, not browsers. Reusing
``user_sessions`` would have meant one credential that opens both the web UI and the MCP surface, so a
leaked MCP token would grant full interactive access and revoking MCP access would sign the human out.
``mcp_access_tokens`` keeps the two credential classes disjoint: an MCP token can ONLY reach MCP tools,
carries its own scope grant, and is revoked independently. It reuses the established session-token
shape exactly (SHA-256 hash at rest, ``expires_at`` / ``revoked_at`` / ``last_used_at``) so there is no
second credential *scheme* to reason about, only a second credential *store*.

WHY FOUR CAPABILITIES. Reading a client record, reading a document's metadata, and reading a
document's extracted CONTENT are three different authorities, and reaching any of them over MCP is a
fourth. ``mcp.access`` gates the door; the three ``mcp.*.read`` capabilities gate the rooms. This lets
a firm grant an assistant document metadata without ever exposing OCR text.

DEFAULT DENY IS LITERAL: this migration grants these capabilities to NO role, not even administrator.
A fresh upgrade leaves the MCP surface reachable by nobody until an administrator explicitly grants
them. Combined with the ``CLIENT360_MCP_ENABLED`` runtime flag (default off) and per-token scopes, an
accidental deploy exposes nothing. See docs/mcp/README.md for the enable procedure.

The capabilities are marked ``sensitive`` so they surface in privileged-capability review.

Additive, data-preserving, fully reversible. Single Alembic head preserved.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "mcp01"
down_revision = "docnorm01"
branch_labels = None
depends_on = None

_CAPABILITIES = (
    ("mcp.access", "Reach Client360 through the read-only MCP interface (door capability)"),
    ("mcp.client.read", "Read client/household summaries over MCP"),
    ("mcp.document.read", "Read document metadata over MCP"),
    ("mcp.document.content.read", "Read already-extracted document text (OCR) over MCP"),
)


def upgrade():
    op.create_table(
        "mcp_access_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # SHA-256 of the bearer token. The token itself is shown once, at issue, and never stored.
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.Text, nullable=False, server_default=""),
        # Granted MCP scopes, e.g. ["client:read", "document:read"]. Empty list => the token can
        # authenticate but reach no tool, which is the safe default for a mis-issued token.
        sa.Column("scopes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mcp_access_tokens_user_id", "mcp_access_tokens", ["user_id"])

    bind = op.get_bind()
    for code, description in _CAPABILITIES:
        bind.execute(sa.text(
            "INSERT INTO capabilities (code, description, sensitive) "
            "VALUES (:code, :description, true) "
            "ON CONFLICT (code) DO NOTHING"), {"code": code, "description": description})
    # Deliberately no role grants. See the module docstring.


def downgrade():
    bind = op.get_bind()
    codes = [code for code, _ in _CAPABILITIES]
    bind.execute(sa.text(
        "DELETE FROM role_capabilities WHERE capability_id IN "
        "(SELECT id FROM capabilities WHERE code IN :codes)").bindparams(
            sa.bindparam("codes", expanding=True)), {"codes": codes})
    bind.execute(sa.text(
        "DELETE FROM capabilities WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)), {"codes": codes})
    op.drop_index("ix_mcp_access_tokens_user_id", table_name="mcp_access_tokens")
    op.drop_table("mcp_access_tokens")
