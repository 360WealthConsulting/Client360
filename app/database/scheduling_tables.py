"""Declared schema for the Phase D.19 Scheduling & Meeting Management platform.

Mirrors the live schema created by migration ``q7b8c9d0e1f2``. Scheduling is the authoritative
domain for **scheduling metadata only** — meetings/appointments (the Calendar Event), reusable
meeting templates, bookable resources/rooms, attendees, resource bookings, reminders, follow-ups,
and an append-only audit ledger. It **owns no business entities**: person/household/organization
anchors and every cross-domain link (opportunity, annual review, communications conversation,
workflow instance, advisor-work item, document, Microsoft 365 event) are references
(``ON DELETE SET NULL``). It never becomes a source of truth for business records; it coordinates
the firm's calendar while reusing the existing Microsoft 365 calendar sync, notification ledger,
and Communications for transport (no calendar provider is implemented here).

``scheduling_events`` is the append-only audit ledger (trigger-blocked BEFORE UPDATE OR DELETE,
created in the migration). Its ``meeting_id`` FK is RESTRICT (no cascade into an immutable table);
``actor_user_id`` is a plain column (no FK) so a parent delete never mutates an immutable row.
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

# Deterministic controlled vocabularies (metadata only; no calendar provider).
MEETING_TYPES = ("prospect", "discovery", "tax_planning", "annual_review", "insurance_review",
                 "retirement_review", "business_owner_planning", "compliance_review",
                 "client_onboarding", "internal", "appointment", "general")
MEETING_CATEGORIES = ("client", "prospect", "internal", "compliance", "planning", "review",
                      "onboarding", "general")
MEETING_STATUSES = ("draft", "scheduled", "confirmed", "checked_in", "completed", "cancelled",
                    "no_show", "rescheduled")
MEETING_PRIORITIES = ("low", "normal", "high", "urgent")
LOCATION_TYPES = ("in_person", "virtual", "phone", "hybrid")
ATTENDEE_TYPES = ("person", "household", "organization", "user", "external")
ATTENDEE_ROLES = ("organizer", "required", "optional", "resource")
RESPONSE_STATUSES = ("needs_action", "accepted", "declined", "tentative")
RESOURCE_TYPES = ("room", "equipment", "virtual", "staff")
REMINDER_STATUSES = ("scheduled", "sent", "cancelled")
FOLLOWUP_STATUSES = ("open", "done", "cancelled")
BOOKING_STATUSES = ("booked", "released")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_scheduling_tables(metadata: MetaData):
    templates = Table(
        "meeting_templates", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("meeting_type", Text, nullable=False, server_default="general"),
        Column("category", Text, nullable=False, server_default="general"),
        Column("default_duration_minutes", Integer, nullable=False, server_default="60"),
        Column("default_location_type", Text, nullable=False, server_default="virtual"),
        Column("agenda", JSON),                 # list of agenda items (deterministic)
        Column("preparation_checklist", JSON),  # list of prep items
        Column("description", Text),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("tags", JSON),
        Column("template_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("meeting_type", MEETING_TYPES), name="ck_meeting_template_type"),
        CheckConstraint(_in("category", MEETING_CATEGORIES), name="ck_meeting_template_category"),
        CheckConstraint(_in("default_location_type", LOCATION_TYPES),
                        name="ck_meeting_template_location"),
    )
    resources = Table(
        "scheduling_resources", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("resource_type", Text, nullable=False, server_default="room"),
        Column("capacity", Integer),
        Column("location", Text),
        Column("description", Text),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("resource_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("resource_type", RESOURCE_TYPES), name="ck_scheduling_resource_type"),
    )
    meetings = Table(
        "meetings", metadata,
        Column("id", Integer, primary_key=True),
        Column("subject", Text, nullable=False),
        Column("meeting_type", Text, nullable=False, server_default="general"),
        Column("category", Text, nullable=False, server_default="general"),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("priority", Text, nullable=False, server_default="normal"),
        Column("organizer_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        # Client-record anchors (references only; never owned by Scheduling). The organization
        # anchor is the canonical relationship-entity id (matching organization_in_scope).
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("organization_id", Integer,
               ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        Column("template_id", Integer, ForeignKey("meeting_templates.id", ondelete="SET NULL")),
        # Cross-domain references (Scheduling references business domains; owns none of them).
        Column("opportunity_id", Integer, ForeignKey("opportunities.id", ondelete="SET NULL")),
        Column("annual_review_session_id", Integer,
               ForeignKey("annual_review_sessions.id", ondelete="SET NULL")),
        Column("conversation_id", Integer,
               ForeignKey("communication_conversations.id", ondelete="SET NULL")),
        Column("workflow_instance_id", Integer,
               ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        Column("agenda_document_id", Integer, ForeignKey("documents.id", ondelete="SET NULL")),
        # Microsoft 365 calendar/Teams reference (reuse, not duplication — no provider here).
        Column("microsoft_event_id", Text),
        # Scheduling metadata.
        Column("starts_at", DateTime(timezone=True)),
        Column("ends_at", DateTime(timezone=True)),
        Column("timezone", Text, nullable=False, server_default="America/Chicago"),
        Column("all_day", Boolean, nullable=False, server_default="false"),
        Column("location", Text),
        Column("location_type", Text, nullable=False, server_default="virtual"),
        Column("virtual_url", Text),
        Column("recurrence", JSON),             # recurrence metadata only (no expansion engine)
        Column("agenda", JSON),
        Column("preparation_checklist", JSON),
        Column("outcome", Text),                # meeting outcome summary
        Column("outcome_notes", Text),
        Column("outcome_recorded_at", DateTime(timezone=True)),
        Column("tags", JSON),
        Column("meeting_metadata", JSON),
        Column("last_status_at", DateTime(timezone=True)),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("meeting_type", MEETING_TYPES), name="ck_meeting_type"),
        CheckConstraint(_in("category", MEETING_CATEGORIES), name="ck_meeting_category"),
        CheckConstraint(_in("status", MEETING_STATUSES), name="ck_meeting_status"),
        CheckConstraint(_in("priority", MEETING_PRIORITIES), name="ck_meeting_priority"),
        CheckConstraint(_in("location_type", LOCATION_TYPES), name="ck_meeting_location_type"),
    )
    attendees = Table(
        "meeting_attendees", metadata,
        Column("id", Integer, primary_key=True),
        Column("meeting_id", Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        Column("attendee_type", Text, nullable=False, server_default="person"),
        Column("attendee_ref", Text, nullable=False),
        Column("attendee_role", Text, nullable=False, server_default="required"),
        Column("display_name", Text),
        Column("response_status", Text, nullable=False, server_default="needs_action"),
        Column("checked_in_at", DateTime(timezone=True)),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("attendee_type", ATTENDEE_TYPES), name="ck_meeting_attendee_type"),
        CheckConstraint(_in("attendee_role", ATTENDEE_ROLES), name="ck_meeting_attendee_role"),
        CheckConstraint(_in("response_status", RESPONSE_STATUSES),
                        name="ck_meeting_attendee_response"),
    )
    bookings = Table(
        "meeting_resource_bookings", metadata,
        Column("id", Integer, primary_key=True),
        Column("meeting_id", Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        Column("resource_id", Integer,
               ForeignKey("scheduling_resources.id", ondelete="CASCADE"), nullable=False),
        Column("starts_at", DateTime(timezone=True)),
        Column("ends_at", DateTime(timezone=True)),
        Column("status", Text, nullable=False, server_default="booked"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", BOOKING_STATUSES), name="ck_meeting_booking_status"),
        UniqueConstraint("meeting_id", "resource_id", name="uq_meeting_resource_booking"),
    )
    reminders = Table(
        "meeting_reminders", metadata,
        Column("id", Integer, primary_key=True),
        Column("meeting_id", Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        Column("remind_at", DateTime(timezone=True)),
        Column("minutes_before", Integer),
        Column("channel", Text, nullable=False, server_default="internal_notification"),
        Column("status", Text, nullable=False, server_default="scheduled"),
        Column("notification_uid", Text),       # link into the reused notification ledger
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", REMINDER_STATUSES), name="ck_meeting_reminder_status"),
    )
    followups = Table(
        "meeting_followups", metadata,
        Column("id", Integer, primary_key=True),
        Column("meeting_id", Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        Column("description", Text, nullable=False),
        Column("due_date", DateTime(timezone=True)),
        Column("status", Text, nullable=False, server_default="open"),
        Column("assigned_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        # The authoritative task lives in Advisor Work when linked (reference, not ownership).
        Column("advisor_work_item_id", Integer,
               ForeignKey("advisor_work_items.id", ondelete="SET NULL")),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", FOLLOWUP_STATUSES), name="ck_meeting_followup_status"),
    )
    # Append-only audit ledger (immutability enforced by a BEFORE UPDATE OR DELETE trigger in the
    # migration). meeting_id is RESTRICT (no cascade into an immutable table); actor_user_id is a
    # plain column (no FK) so a parent delete never attempts to mutate a row here.
    events = Table(
        "scheduling_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("meeting_id", Integer, ForeignKey("meetings.id"), nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "meeting_templates": templates,
        "scheduling_resources": resources,
        "meetings": meetings,
        "meeting_attendees": attendees,
        "meeting_resource_bookings": bookings,
        "meeting_reminders": reminders,
        "meeting_followups": followups,
        "scheduling_events": events,
    }
