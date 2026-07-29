"""Client Portal MVP — Vault client-visibility + portal profile preferences.

The portal is already built (portal_accounts/sessions/notifications/threads/messages/
document_requests/auth_tokens). The one gap for the Client Portal MVP is exposing Vault
documents to clients: the Vault is staff-RBAC only with no client-exposure flag. This adds a
fail-closed ``client_visible`` flag (+ who, if a portal client, uploaded a pending doc) to
``vault_documents``, and two editable profile-preference columns to ``portal_accounts``.
No new document/message/request tables — those already exist and are reused.
"""
import sqlalchemy as sa
from alembic import op

revision = "pv02rtl0vlt1"
down_revision = "va01ult0mvp1"
branch_labels = None
depends_on = None


def upgrade():
    # Vault: client exposure is OFF by default (fail-closed); a client-uploaded doc records the
    # portal account that uploaded it and stays status='uploaded' until an employee approves it.
    op.add_column("vault_documents",
                  sa.Column("client_visible", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("vault_documents",
                  sa.Column("uploaded_by_portal_account_id", sa.Integer,
                            sa.ForeignKey("portal_accounts.id", ondelete="SET NULL")))
    op.create_index(op.f("ix_vault_documents_client_visible"), "vault_documents", ["client_visible"])

    # Portal profile preferences the client may edit (phone/email/address live on the person).
    op.add_column("portal_accounts", sa.Column("preferred_contact_method", sa.Text))
    op.add_column("portal_accounts",
                  sa.Column("communication_preferences", sa.JSON, nullable=False,
                            server_default=sa.text("'{}'::jsonb")))


def downgrade():
    op.drop_column("portal_accounts", "communication_preferences")
    op.drop_column("portal_accounts", "preferred_contact_method")
    op.drop_index(op.f("ix_vault_documents_client_visible"), table_name="vault_documents")
    op.drop_column("vault_documents", "uploaded_by_portal_account_id")
    op.drop_column("vault_documents", "client_visible")
