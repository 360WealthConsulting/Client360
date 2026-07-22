"""Declared schema for the Phase D.18 Communications & Client Engagement platform.

Mirrors the live schema created by migration ``p6a7b8c9d0e1``. Communications is the authoritative
domain for **communication metadata** — conversations, threads, messages, recipients, deliveries,
attachment references, reusable templates, and an append-only audit ledger. It **owns no business
entities**: person/household/organization anchors are references (``ON DELETE SET NULL``) and
document attachments reference the Document platform. It never becomes a source of truth for
business records; it coordinates outbound/inbound communication while preserving ownership
boundaries and reuses the existing notification ledger / outbox / Microsoft 365 transport (no
proprietary transport is implemented here).

``communication_events`` is the append-only audit ledger (trigger-blocked BEFORE UPDATE OR DELETE,
created in the migration). Its ``conversation_id`` FK is RESTRICT (no cascade into an immutable
table) and ``message_id`` / ``actor_user_id`` are plain columns (no FK) so parent deletes never
attempt to mutate immutable rows.
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)

# Deterministic controlled vocabularies (metadata only; no proprietary transport).
COMMUNICATION_CHANNELS = ("email", "sms", "portal_message", "teams", "internal_notification",
                          "phone_log", "letter", "secure_message")
COMMUNICATION_CATEGORIES = ("general", "review", "compliance", "tax", "insurance", "retirement",
                            "document_request", "appointment", "campaign", "referral", "workflow",
                            "onboarding", "servicing")
COMMUNICATION_PRIORITIES = ("low", "normal", "high", "urgent")
COMMUNICATION_DIRECTIONS = ("outbound", "inbound", "internal")
CONVERSATION_STATUSES = ("open", "closed", "archived")
# Delivery lifecycle (metadata only — no mail server is implemented).
DELIVERY_STATUSES = ("queued", "scheduled", "sending", "sent", "delivered", "failed", "cancelled",
                     "read", "expired")
RECIPIENT_TYPES = ("person", "household", "organization", "user", "external")
RECIPIENT_ROLES = ("to", "cc", "bcc")
SENDER_TYPES = ("user", "system")

_CHANNELS_SQL = ",".join(f"'{c}'" for c in COMMUNICATION_CHANNELS)
_CATEGORIES_SQL = ",".join(f"'{c}'" for c in COMMUNICATION_CATEGORIES)
_PRIORITIES_SQL = ",".join(f"'{c}'" for c in COMMUNICATION_PRIORITIES)
_DIRECTIONS_SQL = ",".join(f"'{c}'" for c in COMMUNICATION_DIRECTIONS)
_CONV_STATUS_SQL = ",".join(f"'{c}'" for c in CONVERSATION_STATUSES)
_DELIVERY_STATUS_SQL = ",".join(f"'{c}'" for c in DELIVERY_STATUSES)
_RECIPIENT_TYPE_SQL = ",".join(f"'{c}'" for c in RECIPIENT_TYPES)
_RECIPIENT_ROLE_SQL = ",".join(f"'{c}'" for c in RECIPIENT_ROLES)
_SENDER_TYPE_SQL = ",".join(f"'{c}'" for c in SENDER_TYPES)


def define_communication_tables(metadata: MetaData):
    templates = Table(
        "communication_templates", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("category", Text, nullable=False, server_default="general"),
        Column("channel", Text, nullable=False, server_default="email"),
        Column("subject", Text),
        Column("body", Text, nullable=False),
        Column("description", Text),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("tags", JSON),
        Column("template_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(f"category IN ({_CATEGORIES_SQL})", name="ck_comm_template_category"),
        CheckConstraint(f"channel IN ({_CHANNELS_SQL})", name="ck_comm_template_channel"),
    )
    conversations = Table(
        "communication_conversations", metadata,
        Column("id", Integer, primary_key=True),
        Column("subject", Text, nullable=False),
        Column("category", Text, nullable=False, server_default="general"),
        Column("status", Text, nullable=False, server_default="open"),
        Column("priority", Text, nullable=False, server_default="normal"),
        Column("channel", Text, nullable=False, server_default="email"),
        # Client-record anchors (references only; never owned by Communications). The
        # organization anchor is the canonical relationship-entity id (the same id
        # ``organization_in_scope`` and ``organization_profiles.relationship_entity_id`` use).
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("organization_id", Integer,
               ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        Column("tags", JSON),
        Column("conversation_metadata", JSON),
        Column("last_message_at", DateTime(timezone=True)),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(f"status IN ({_CONV_STATUS_SQL})", name="ck_comm_conversation_status"),
        CheckConstraint(f"priority IN ({_PRIORITIES_SQL})", name="ck_comm_conversation_priority"),
        CheckConstraint(f"category IN ({_CATEGORIES_SQL})", name="ck_comm_conversation_category"),
        CheckConstraint(f"channel IN ({_CHANNELS_SQL})", name="ck_comm_conversation_channel"),
    )
    threads = Table(
        "communication_threads", metadata,
        Column("id", Integer, primary_key=True),
        Column("conversation_id", Integer,
               ForeignKey("communication_conversations.id", ondelete="CASCADE"), nullable=False),
        Column("subject", Text),
        Column("status", Text, nullable=False, server_default="open"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(f"status IN ({_CONV_STATUS_SQL})", name="ck_comm_thread_status"),
    )
    messages = Table(
        "communication_messages", metadata,
        Column("id", Integer, primary_key=True),
        Column("conversation_id", Integer,
               ForeignKey("communication_conversations.id", ondelete="CASCADE"), nullable=False),
        Column("thread_id", Integer,
               ForeignKey("communication_threads.id", ondelete="SET NULL")),
        Column("template_id", Integer,
               ForeignKey("communication_templates.id", ondelete="SET NULL")),
        Column("channel", Text, nullable=False, server_default="email"),
        Column("direction", Text, nullable=False, server_default="outbound"),
        Column("priority", Text, nullable=False, server_default="normal"),
        Column("category", Text, nullable=False, server_default="general"),
        Column("subject", Text),
        Column("body", Text),
        Column("sender_type", Text, nullable=False, server_default="user"),
        Column("sender_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("sender_ref", Text),
        Column("status", Text, nullable=False, server_default="queued"),
        # Link into the reused notification ledger (metadata only; no dispatch performed here).
        Column("notification_uid", Text),
        Column("scheduled_at", DateTime(timezone=True)),
        Column("sent_at", DateTime(timezone=True)),
        Column("delivered_at", DateTime(timezone=True)),
        Column("read_at", DateTime(timezone=True)),
        Column("tags", JSON),
        Column("message_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(f"channel IN ({_CHANNELS_SQL})", name="ck_comm_message_channel"),
        CheckConstraint(f"direction IN ({_DIRECTIONS_SQL})", name="ck_comm_message_direction"),
        CheckConstraint(f"priority IN ({_PRIORITIES_SQL})", name="ck_comm_message_priority"),
        CheckConstraint(f"category IN ({_CATEGORIES_SQL})", name="ck_comm_message_category"),
        CheckConstraint(f"sender_type IN ({_SENDER_TYPE_SQL})", name="ck_comm_message_sender_type"),
        CheckConstraint(f"status IN ({_DELIVERY_STATUS_SQL})", name="ck_comm_message_status"),
    )
    recipients = Table(
        "communication_recipients", metadata,
        Column("id", Integer, primary_key=True),
        Column("message_id", Integer,
               ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        Column("recipient_type", Text, nullable=False, server_default="person"),
        Column("recipient_ref", Text, nullable=False),
        Column("recipient_role", Text, nullable=False, server_default="to"),
        Column("display_name", Text),
        Column("delivery_status", Text, nullable=False, server_default="queued"),
        Column("delivered_at", DateTime(timezone=True)),
        Column("read_at", DateTime(timezone=True)),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(f"recipient_type IN ({_RECIPIENT_TYPE_SQL})", name="ck_comm_recipient_type"),
        CheckConstraint(f"recipient_role IN ({_RECIPIENT_ROLE_SQL})", name="ck_comm_recipient_role"),
        CheckConstraint(f"delivery_status IN ({_DELIVERY_STATUS_SQL})",
                        name="ck_comm_recipient_status"),
    )
    deliveries = Table(
        "communication_deliveries", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("message_id", Integer,
               ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        Column("recipient_id", Integer,
               ForeignKey("communication_recipients.id", ondelete="SET NULL")),
        Column("channel", Text, nullable=False),
        Column("provider", Text),
        Column("provider_ref", Text),
        Column("status", Text, nullable=False),
        Column("detail", Text),
        Column("delivery_metadata", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(f"status IN ({_DELIVERY_STATUS_SQL})", name="ck_comm_delivery_status"),
    )
    attachments = Table(
        "communication_attachments", metadata,
        Column("id", Integer, primary_key=True),
        Column("message_id", Integer,
               ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        Column("document_id", Integer, ForeignKey("documents.id", ondelete="SET NULL")),
        Column("attachment_ref", Text),
        Column("description", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("message_id", "document_id", name="uq_comm_attachment_document"),
    )
    # Append-only audit ledger (immutability enforced by a BEFORE UPDATE OR DELETE trigger in the
    # migration). conversation_id is RESTRICT (no cascade into an immutable table); message_id and
    # actor_user_id are plain columns (no FK) so a parent delete never attempts to mutate a row here.
    events = Table(
        "communication_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("conversation_id", Integer, ForeignKey("communication_conversations.id"),
               nullable=False),
        Column("message_id", Integer),
        Column("event_type", Text, nullable=False),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "communication_templates": templates,
        "communication_conversations": conversations,
        "communication_threads": threads,
        "communication_messages": messages,
        "communication_recipients": recipients,
        "communication_deliveries": deliveries,
        "communication_attachments": attachments,
        "communication_events": events,
    }
