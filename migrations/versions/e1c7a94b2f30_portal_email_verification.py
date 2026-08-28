"""Email one-time-code authentication for the CLIENT portal.

The client portal authenticated clients through an external Microsoft identity provider. That is not
the intended client model: clients are not tenant users, and requiring them to hold a Microsoft
identity to read their own documents is the wrong bar. Client authentication becomes possession of
the mailbox the firm already invited: a short-lived one-time code is emailed to the address bound to
the portal account, and entering it proves possession.

Two additive changes, no rewrites:

``portal_email_verifications`` — one row per issued code.
  * ``code_hash`` is an HMAC of ``account_id:code`` keyed by the server secret, so the stored value is
    useless without the key AND cannot be replayed against a different account. It is deliberately
    NOT unique: a six-digit code collides across accounts by construction, and a UNIQUE index would
    both leak that fact and reject legitimate issues. Lookup is BY ACCOUNT, never by hash, so a code
    can only ever be tested against the account it was issued for.
  * ``sent_to_email`` records the address the code actually went to — the audit answer to "could the
    browser have redirected this code somewhere else?" (it cannot; the address is read from the
    account, never from the request).
  * ``attempts`` bounds brute force; ``consumed_at`` makes a code single-use; ``invalidated_at``
    is set when a newer code supersedes it, when the account is revoked, or when attempts run out.

``portal_accounts.auth_method`` — how the account authenticates, set at activation.
  ``auth_subject`` keeps its EXISTING meaning: the immutable subject of an external identity provider.
  An email-code account has no such subject, so rather than storing an email address in a column whose
  semantics are provider-specific, ``auth_subject`` stays NULL and ``auth_method`` records
  ``'email_code'``. Existing rows are backfilled to ``'microsoft'`` where a subject is already bound,
  which is exactly what they are.

Additive and reversible. Nothing existing is dropped or rewritten; the portal account, invitation,
grant and session lifecycle is untouched.
"""
import sqlalchemy as sa
from alembic import op

revision = "e1c7a94b2f30"
down_revision = "b5d82e04c917"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "portal_email_verifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portal_account_id", sa.Integer(),
                  sa.ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False),
        # 'activation' (first sign-in, consumes an invitation) or 'login' (repeat sign-in).
        sa.Column("purpose", sa.String(30), nullable=False),
        # HMAC(server_secret, "account_id:code") — never the code, never a bare digest of it.
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("sent_to_email", sa.String(320), nullable=False),
        sa.Column("portal_invitation_id", sa.Integer(),
                  sa.ForeignKey("portal_invitations.id", ondelete="CASCADE")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    # The hot path is "the newest live code for this account", and the resend limiter counts recent
    # rows for one account.
    op.create_index("ix_portal_email_verifications_account",
                    "portal_email_verifications", ["portal_account_id", "created_at"])

    op.add_column("portal_accounts", sa.Column("auth_method", sa.String(30)))
    # An account with a bound external subject IS a Microsoft account; anything else is unactivated
    # and will be stamped at activation.
    op.execute("UPDATE portal_accounts SET auth_method = 'microsoft' WHERE auth_subject IS NOT NULL")


def downgrade():
    op.drop_column("portal_accounts", "auth_method")
    op.drop_index("ix_portal_email_verifications_account",
                  table_name="portal_email_verifications")
    op.drop_table("portal_email_verifications")
