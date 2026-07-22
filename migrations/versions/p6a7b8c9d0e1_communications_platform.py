"""Communications & Client Engagement platform (Phase D.18).

Communications is a new AUTHORITATIVE domain for communication metadata — conversations, threads,
messages, recipients, deliveries, attachment references, reusable templates, and an append-only
audit ledger. It **owns no business entities**: person/household/organization anchors are
references (``ON DELETE SET NULL``) and attachments reference the Document platform. It is never a
source of truth for business records; it coordinates outbound/inbound communication while
preserving ownership boundaries and reuses the EXISTING notification ledger / transactional outbox
/ Microsoft 365 transport (no proprietary transport is implemented here — delivery is metadata
only, mirroring the notification ledger's intent-only model).

Tables (8):
- ``communication_templates`` — reusable, deterministic message templates (welcome, annual review,
  tax organizer, missing documents, etc.).
- ``communication_conversations`` — the top-level Communication/Conversation container (subject,
  category, priority, status, primary channel, client anchor, tags, metadata).
- ``communication_threads`` — reply chains within a conversation.
- ``communication_messages`` — individual messages (channel, direction, sender, delivery status,
  read status, template link, notification-ledger link).
- ``communication_recipients`` — per-message recipients (type/ref/role + per-recipient delivery
  and read status).
- ``communication_deliveries`` — the delivery-lifecycle ledger (queued→…→expired), metadata only.
- ``communication_attachments`` — document/attachment references (never duplicates documents).
- ``communication_events`` — APPEND-ONLY audit ledger (trigger-blocked BEFORE UPDATE OR DELETE).

Seeds 5 ``communications.*`` capabilities. Additive and reversible. Single Alembic head
(down_revision ``o5f6a7b8c9d0`` — the D.17 head).
"""
import sqlalchemy as sa
from alembic import op

revision = "p6a7b8c9d0e1"
down_revision = "o5f6a7b8c9d0"
branch_labels = None
depends_on = None

_CHANNELS = ("email", "sms", "portal_message", "teams", "internal_notification", "phone_log",
             "letter", "secure_message")
_CATEGORIES = ("general", "review", "compliance", "tax", "insurance", "retirement",
               "document_request", "appointment", "campaign", "referral", "workflow",
               "onboarding", "servicing")
_PRIORITIES = ("low", "normal", "high", "urgent")
_DIRECTIONS = ("outbound", "inbound", "internal")
_CONV_STATUS = ("open", "closed", "archived")
_DELIVERY = ("queued", "scheduled", "sending", "sent", "delivered", "failed", "cancelled", "read",
             "expired")
_RECIPIENT_TYPES = ("person", "household", "organization", "user", "external")
_RECIPIENT_ROLES = ("to", "cc", "bcc")
_SENDER_TYPES = ("user", "system")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


# Deterministic starter templates (business-facing examples from the phase spec).
_TEMPLATE_SEED = (
    ("welcome", "Welcome", "onboarding", "email", "Welcome to 360 Wealth Consulting"),
    ("annual_review", "Annual Review Invitation", "review", "email", "Your annual review"),
    ("tax_organizer", "Tax Organizer", "tax", "email", "Your tax organizer is ready"),
    ("missing_documents", "Missing Documents", "document_request", "email", "Documents we still need"),
    ("insurance_review", "Insurance Review", "insurance", "email", "Time for an insurance review"),
    ("retirement_review", "Retirement Review", "retirement", "email", "Your retirement review"),
    ("compliance_notice", "Compliance Notice", "compliance", "secure_message", "Important compliance notice"),
    ("workflow_reminder", "Workflow Reminder", "workflow", "internal_notification", "Workflow reminder"),
    ("appointment_reminder", "Appointment Reminder", "appointment", "sms", "Appointment reminder"),
    ("campaign_followup", "Campaign Follow-up", "campaign", "email", "Following up"),
    ("referral_thank_you", "Referral Thank You", "referral", "email", "Thank you for your referral"),
    ("document_request", "Document Request", "document_request", "portal_message", "Document request"),
)

_CAPS = (
    ("communications.view", "View conversations, messages, and communication history.", False,
     ("administrator", "advisor", "operations", "compliance")),
    ("communications.send", "Compose and send communications (record delivery intent).", False,
     ("administrator", "advisor", "operations")),
    ("communications.manage_templates", "Manage reusable communication templates.", False,
     ("administrator", "operations")),
    ("communications.audit", "View communication audit history.", True,
     ("administrator", "compliance")),
    ("communications.admin", "Administer the communications platform.", True, ("administrator",)),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "communication_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("channel", sa.Text, nullable=False, server_default="email"),
        sa.Column("subject", sa.Text),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("tags", sa.JSON),
        sa.Column("template_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_comm_template_category"),
        sa.CheckConstraint(_in("channel", _CHANNELS), name="ck_comm_template_channel"),
    )

    op.create_table(
        "communication_conversations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("priority", sa.Text, nullable=False, server_default="normal"),
        sa.Column("channel", sa.Text, nullable=False, server_default="email"),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        sa.Column("tags", sa.JSON),
        sa.Column("conversation_metadata", sa.JSON),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _CONV_STATUS), name="ck_comm_conversation_status"),
        sa.CheckConstraint(_in("priority", _PRIORITIES), name="ck_comm_conversation_priority"),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_comm_conversation_category"),
        sa.CheckConstraint(_in("channel", _CHANNELS), name="ck_comm_conversation_channel"),
    )
    op.create_index("ix_comm_conversation_person", "communication_conversations", ["person_id"])
    op.create_index("ix_comm_conversation_household", "communication_conversations", ["household_id"])
    op.create_index("ix_comm_conversation_status", "communication_conversations", ["status"])

    op.create_table(
        "communication_threads",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer,
                  sa.ForeignKey("communication_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _CONV_STATUS), name="ck_comm_thread_status"),
    )
    op.create_index("ix_comm_thread_conversation", "communication_threads", ["conversation_id"])

    op.create_table(
        "communication_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer,
                  sa.ForeignKey("communication_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.Integer,
                  sa.ForeignKey("communication_threads.id", ondelete="SET NULL")),
        sa.Column("template_id", sa.Integer,
                  sa.ForeignKey("communication_templates.id", ondelete="SET NULL")),
        sa.Column("channel", sa.Text, nullable=False, server_default="email"),
        sa.Column("direction", sa.Text, nullable=False, server_default="outbound"),
        sa.Column("priority", sa.Text, nullable=False, server_default="normal"),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("subject", sa.Text),
        sa.Column("body", sa.Text),
        sa.Column("sender_type", sa.Text, nullable=False, server_default="user"),
        sa.Column("sender_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("sender_ref", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("notification_uid", sa.Text),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("tags", sa.JSON),
        sa.Column("message_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("channel", _CHANNELS), name="ck_comm_message_channel"),
        sa.CheckConstraint(_in("direction", _DIRECTIONS), name="ck_comm_message_direction"),
        sa.CheckConstraint(_in("priority", _PRIORITIES), name="ck_comm_message_priority"),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_comm_message_category"),
        sa.CheckConstraint(_in("sender_type", _SENDER_TYPES), name="ck_comm_message_sender_type"),
        sa.CheckConstraint(_in("status", _DELIVERY), name="ck_comm_message_status"),
    )
    op.create_index("ix_comm_message_conversation", "communication_messages", ["conversation_id"])
    op.create_index("ix_comm_message_status", "communication_messages", ["status"])

    op.create_table(
        "communication_recipients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("message_id", sa.Integer,
                  sa.ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_type", sa.Text, nullable=False, server_default="person"),
        sa.Column("recipient_ref", sa.Text, nullable=False),
        sa.Column("recipient_role", sa.Text, nullable=False, server_default="to"),
        sa.Column("display_name", sa.Text),
        sa.Column("delivery_status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("recipient_type", _RECIPIENT_TYPES), name="ck_comm_recipient_type"),
        sa.CheckConstraint(_in("recipient_role", _RECIPIENT_ROLES), name="ck_comm_recipient_role"),
        sa.CheckConstraint(_in("delivery_status", _DELIVERY), name="ck_comm_recipient_status"),
    )
    op.create_index("ix_comm_recipient_message", "communication_recipients", ["message_id"])

    op.create_table(
        "communication_deliveries",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("message_id", sa.Integer,
                  sa.ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_id", sa.Integer,
                  sa.ForeignKey("communication_recipients.id", ondelete="SET NULL")),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("provider", sa.Text),
        sa.Column("provider_ref", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("delivery_metadata", sa.JSON),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _DELIVERY), name="ck_comm_delivery_status"),
    )
    op.create_index("ix_comm_delivery_message", "communication_deliveries", ["message_id"])

    op.create_table(
        "communication_attachments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("message_id", sa.Integer,
                  sa.ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("attachment_ref", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", "document_id", name="uq_comm_attachment_document"),
    )
    op.create_index("ix_comm_attachment_message", "communication_attachments", ["message_id"])

    # Append-only audit ledger. conversation_id is RESTRICT (no cascade into an immutable table);
    # message_id / actor_user_id are plain columns (no FK) so parent deletes never mutate a row here.
    op.create_table(
        "communication_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("conversation_id", sa.Integer,
                  sa.ForeignKey("communication_conversations.id"), nullable=False),
        sa.Column("message_id", sa.Integer),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("payload", sa.JSON),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_comm_event_conversation", "communication_events", ["conversation_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_communication_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'communication_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER communication_events_immutable BEFORE UPDATE OR DELETE ON "
        "communication_events FOR EACH ROW EXECUTE FUNCTION prevent_communication_event_mutation()"
    )

    # Seed reusable starter templates (idempotent by code).
    for code, name, category, channel, subject in _TEMPLATE_SEED:
        exists = bind.execute(sa.text("SELECT id FROM communication_templates WHERE code = :c"),
                              {"c": code}).scalar()
        if exists is None:
            bind.execute(sa.text(
                "INSERT INTO communication_templates (code, name, category, channel, subject, body, "
                "active) VALUES (:c, :n, :cat, :ch, :s, :b, true)"),
                {"c": code, "n": name, "cat": category, "ch": channel, "s": subject,
                 "b": f"{{{{greeting}}}}\n\n{subject}.\n\n{{{{signature}}}}"})

    # Seed capabilities (idempotent).
    for code, description, sensitive, roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, :s) RETURNING id"),
                {"c": code, "d": description, "s": sensitive}).scalar()
        for role_code in roles:
            role_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
            if role_id is None:
                continue
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid}).scalar()
            if not exists:
                bind.execute(sa.text("INSERT INTO role_capabilities (role_id, capability_id) "
                                     "VALUES (:r, :c)"), {"r": role_id, "c": cid})


def downgrade():
    bind = op.get_bind()
    for code, _d, _s, _roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})

    op.execute("DROP TRIGGER IF EXISTS communication_events_immutable ON communication_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_communication_event_mutation()")
    op.drop_table("communication_events")
    op.drop_table("communication_attachments")
    op.drop_table("communication_deliveries")
    op.drop_table("communication_recipients")
    op.drop_table("communication_messages")
    op.drop_table("communication_threads")
    op.drop_table("communication_conversations")
    op.drop_table("communication_templates")
