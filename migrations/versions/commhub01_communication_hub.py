"""Communication Hub — relationship-owned conversation metadata on the portal messaging foundation.

Additive columns only; no data is moved and no existing column changes type. Extends the existing portal
secure-messaging tables into a unified client communication system:

  * portal_threads         — topic, organization context, assignment (existing users/teams — no parallel
                             directory), resolved state, and RELATIONSHIP-level read/activity markers
                             (thread-level, not one row per page view).
  * portal_messages        — ``channel`` (portal today; email/sms later) so future channels appear as
                             messages without rewriting the thread model.
  * portal_document_requests — ``thread_id`` so a conversation can be linked to a document request.

``organization_id`` is the relationship-entity id (same convention as portal_access_grants /
communication_conversations — no physical ``organizations`` table), so it carries no FK. Reversible.
"""
import sqlalchemy as sa
from alembic import op

revision = "commhub01"
down_revision = "caf01"
branch_labels = None
depends_on = None

_THREAD_COLUMNS = (
    ("topic", sa.Column("topic", sa.String(40))),
    ("organization_id", sa.Column("organization_id", sa.Integer())),   # relationship entity id (no FK)
    ("assigned_user_id", sa.Column("assigned_user_id", sa.Integer(),
                                   sa.ForeignKey("users.id", ondelete="SET NULL"))),
    ("assigned_team_id", sa.Column("assigned_team_id", sa.Integer(),
                                   sa.ForeignKey("teams.id", ondelete="SET NULL"))),
    ("resolved_at", sa.Column("resolved_at", sa.DateTime(timezone=True))),
    ("resolved_by_user_id", sa.Column("resolved_by_user_id", sa.Integer(),
                                      sa.ForeignKey("users.id", ondelete="SET NULL"))),
    ("last_client_message_at", sa.Column("last_client_message_at", sa.DateTime(timezone=True))),
    ("last_staff_message_at", sa.Column("last_staff_message_at", sa.DateTime(timezone=True))),
    ("staff_last_read_at", sa.Column("staff_last_read_at", sa.DateTime(timezone=True))),
    ("client_last_read_at", sa.Column("client_last_read_at", sa.DateTime(timezone=True))),
)


def upgrade():
    for _name, col in _THREAD_COLUMNS:
        op.add_column("portal_threads", col)
    op.add_column("portal_messages",
                  sa.Column("channel", sa.String(20), nullable=False, server_default="portal"))
    op.add_column("portal_document_requests",
                  sa.Column("thread_id", sa.Integer(),
                            sa.ForeignKey("portal_threads.id", ondelete="SET NULL")))


def downgrade():
    op.drop_column("portal_document_requests", "thread_id")
    op.drop_column("portal_messages", "channel")
    for name, _col in reversed(_THREAD_COLUMNS):
        op.drop_column("portal_threads", name)
