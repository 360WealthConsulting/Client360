"""Scheduling & Meeting Management platform (Phase D.19).

Scheduling is a new AUTHORITATIVE domain for scheduling metadata — meetings/appointments (the
Calendar Event), reusable meeting templates, bookable resources/rooms, attendees, resource
bookings, reminders, follow-ups, and an append-only audit ledger. It **owns no business entities**:
person/household/organization anchors and every cross-domain link (opportunity, annual review,
communications conversation, workflow instance, advisor-work item, document, Microsoft 365 event)
are references (``ON DELETE SET NULL``). It is never a source of truth for business records; it
coordinates the firm's calendar while reusing the EXISTING Microsoft 365 calendar sync, the
notification ledger, and Communications for transport (no calendar provider is implemented here —
availability and status are metadata only).

Tables (8):
- ``meeting_templates`` — reusable meeting templates (prospect, discovery, tax planning, annual
  review, insurance/retirement review, business owner planning, compliance review, client
  onboarding, internal) with default duration, location type, agenda, and preparation checklist.
- ``scheduling_resources`` — bookable rooms/equipment/virtual/staff resources.
- ``meetings`` — the core meeting/appointment/calendar-event (subject, type, category, status,
  organizer, client anchor, cross-domain references, time/location/virtual URL, recurrence
  metadata, agenda, prep checklist, outcome).
- ``meeting_attendees`` — per-meeting attendees (type/ref/role + response + check-in).
- ``meeting_resource_bookings`` — resource/room bookings for a meeting.
- ``meeting_reminders`` — reminder metadata (transport reuses the notification ledger).
- ``meeting_followups`` — follow-up items (authoritative task lives in Advisor Work when linked).
- ``scheduling_events`` — APPEND-ONLY audit ledger (trigger-blocked BEFORE UPDATE OR DELETE).

Seeds 5 ``scheduling.*`` capabilities and 9 starter meeting templates. Additive and reversible.
Single Alembic head (down_revision ``p6a7b8c9d0e1`` — the D.18 head).
"""
import sqlalchemy as sa
from alembic import op

revision = "q7b8c9d0e1f2"
down_revision = "p6a7b8c9d0e1"
branch_labels = None
depends_on = None

_MEETING_TYPES = ("prospect", "discovery", "tax_planning", "annual_review", "insurance_review",
                  "retirement_review", "business_owner_planning", "compliance_review",
                  "client_onboarding", "internal", "appointment", "general")
_CATEGORIES = ("client", "prospect", "internal", "compliance", "planning", "review", "onboarding",
               "general")
_STATUSES = ("draft", "scheduled", "confirmed", "checked_in", "completed", "cancelled", "no_show",
             "rescheduled")
_PRIORITIES = ("low", "normal", "high", "urgent")
_LOCATION_TYPES = ("in_person", "virtual", "phone", "hybrid")
_ATTENDEE_TYPES = ("person", "household", "organization", "user", "external")
_ATTENDEE_ROLES = ("organizer", "required", "optional", "resource")
_RESPONSE = ("needs_action", "accepted", "declined", "tentative")
_RESOURCE_TYPES = ("room", "equipment", "virtual", "staff")
_REMINDER = ("scheduled", "sent", "cancelled")
_FOLLOWUP = ("open", "done", "cancelled")
_BOOKING = ("booked", "released")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


# (code, name, meeting_type, category, duration, location_type)
_TEMPLATE_SEED = (
    ("prospect_meeting", "Prospect Meeting", "prospect", "prospect", 45, "virtual"),
    ("discovery", "Discovery Meeting", "discovery", "prospect", 60, "virtual"),
    ("tax_planning", "Tax Planning", "tax_planning", "planning", 60, "virtual"),
    ("annual_review", "Annual Review", "annual_review", "review", 90, "in_person"),
    ("insurance_review", "Insurance Review", "insurance_review", "review", 60, "virtual"),
    ("retirement_review", "Retirement Review", "retirement_review", "review", 60, "virtual"),
    ("business_owner_planning", "Business Owner Planning", "business_owner_planning", "planning",
     90, "in_person"),
    ("compliance_review", "Compliance Review", "compliance_review", "compliance", 45, "virtual"),
    ("client_onboarding", "Client Onboarding", "client_onboarding", "onboarding", 60, "in_person"),
    ("internal_meeting", "Internal Meeting", "internal", "internal", 30, "virtual"),
)

_CAPS = (
    ("scheduling.view", "View meetings, appointments, templates, and availability.", False,
     ("administrator", "advisor", "operations", "compliance")),
    ("scheduling.manage", "Create, update, and transition meetings and appointments.", False,
     ("administrator", "advisor", "operations")),
    ("scheduling.templates", "Manage meeting templates and scheduling resources.", False,
     ("administrator", "operations")),
    ("scheduling.audit", "View meeting/scheduling audit history.", True,
     ("administrator", "compliance")),
    ("scheduling.admin", "Administer the scheduling platform.", True, ("administrator",)),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "meeting_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("meeting_type", sa.Text, nullable=False, server_default="general"),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("default_duration_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("default_location_type", sa.Text, nullable=False, server_default="virtual"),
        sa.Column("agenda", sa.JSON),
        sa.Column("preparation_checklist", sa.JSON),
        sa.Column("description", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("tags", sa.JSON),
        sa.Column("template_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("meeting_type", _MEETING_TYPES), name="ck_meeting_template_type"),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_meeting_template_category"),
        sa.CheckConstraint(_in("default_location_type", _LOCATION_TYPES),
                           name="ck_meeting_template_location"),
    )

    op.create_table(
        "scheduling_resources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("resource_type", sa.Text, nullable=False, server_default="room"),
        sa.Column("capacity", sa.Integer),
        sa.Column("location", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("resource_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("resource_type", _RESOURCE_TYPES), name="ck_scheduling_resource_type"),
    )

    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("meeting_type", sa.Text, nullable=False, server_default="general"),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("priority", sa.Text, nullable=False, server_default="normal"),
        sa.Column("organizer_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        sa.Column("template_id", sa.Integer, sa.ForeignKey("meeting_templates.id", ondelete="SET NULL")),
        sa.Column("opportunity_id", sa.Integer, sa.ForeignKey("opportunities.id", ondelete="SET NULL")),
        sa.Column("annual_review_session_id", sa.Integer,
                  sa.ForeignKey("annual_review_sessions.id", ondelete="SET NULL")),
        sa.Column("conversation_id", sa.Integer,
                  sa.ForeignKey("communication_conversations.id", ondelete="SET NULL")),
        sa.Column("workflow_instance_id", sa.Integer,
                  sa.ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        sa.Column("agenda_document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("microsoft_event_id", sa.Text),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("timezone", sa.Text, nullable=False, server_default="America/Chicago"),
        sa.Column("all_day", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("location", sa.Text),
        sa.Column("location_type", sa.Text, nullable=False, server_default="virtual"),
        sa.Column("virtual_url", sa.Text),
        sa.Column("recurrence", sa.JSON),
        sa.Column("agenda", sa.JSON),
        sa.Column("preparation_checklist", sa.JSON),
        sa.Column("outcome", sa.Text),
        sa.Column("outcome_notes", sa.Text),
        sa.Column("outcome_recorded_at", sa.DateTime(timezone=True)),
        sa.Column("tags", sa.JSON),
        sa.Column("meeting_metadata", sa.JSON),
        sa.Column("last_status_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("meeting_type", _MEETING_TYPES), name="ck_meeting_type"),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_meeting_category"),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_meeting_status"),
        sa.CheckConstraint(_in("priority", _PRIORITIES), name="ck_meeting_priority"),
        sa.CheckConstraint(_in("location_type", _LOCATION_TYPES), name="ck_meeting_location_type"),
    )
    op.create_index("ix_meetings_person", "meetings", ["person_id"])
    op.create_index("ix_meetings_household", "meetings", ["household_id"])
    op.create_index("ix_meetings_organizer", "meetings", ["organizer_user_id"])
    op.create_index("ix_meetings_status", "meetings", ["status"])
    op.create_index("ix_meetings_starts_at", "meetings", ["starts_at"])

    op.create_table(
        "meeting_attendees",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("meeting_id", sa.Integer, sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attendee_type", sa.Text, nullable=False, server_default="person"),
        sa.Column("attendee_ref", sa.Text, nullable=False),
        sa.Column("attendee_role", sa.Text, nullable=False, server_default="required"),
        sa.Column("display_name", sa.Text),
        sa.Column("response_status", sa.Text, nullable=False, server_default="needs_action"),
        sa.Column("checked_in_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("attendee_type", _ATTENDEE_TYPES), name="ck_meeting_attendee_type"),
        sa.CheckConstraint(_in("attendee_role", _ATTENDEE_ROLES), name="ck_meeting_attendee_role"),
        sa.CheckConstraint(_in("response_status", _RESPONSE), name="ck_meeting_attendee_response"),
    )
    op.create_index("ix_meeting_attendees_meeting", "meeting_attendees", ["meeting_id"])

    op.create_table(
        "meeting_resource_bookings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("meeting_id", sa.Integer, sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource_id", sa.Integer,
                  sa.ForeignKey("scheduling_resources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="booked"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _BOOKING), name="ck_meeting_booking_status"),
        sa.UniqueConstraint("meeting_id", "resource_id", name="uq_meeting_resource_booking"),
    )
    op.create_index("ix_meeting_bookings_resource", "meeting_resource_bookings", ["resource_id"])

    op.create_table(
        "meeting_reminders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("meeting_id", sa.Integer, sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True)),
        sa.Column("minutes_before", sa.Integer),
        sa.Column("channel", sa.Text, nullable=False, server_default="internal_notification"),
        sa.Column("status", sa.Text, nullable=False, server_default="scheduled"),
        sa.Column("notification_uid", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _REMINDER), name="ck_meeting_reminder_status"),
    )
    op.create_index("ix_meeting_reminders_meeting", "meeting_reminders", ["meeting_id"])

    op.create_table(
        "meeting_followups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("meeting_id", sa.Integer, sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("due_date", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("assigned_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("advisor_work_item_id", sa.Integer,
                  sa.ForeignKey("advisor_work_items.id", ondelete="SET NULL")),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _FOLLOWUP), name="ck_meeting_followup_status"),
    )
    op.create_index("ix_meeting_followups_meeting", "meeting_followups", ["meeting_id"])

    # Append-only audit ledger. meeting_id is RESTRICT (no cascade into an immutable table);
    # actor_user_id is a plain column (no FK) so a parent delete never mutates a row here.
    op.create_table(
        "scheduling_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("meeting_id", sa.Integer, sa.ForeignKey("meetings.id"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status", sa.Text),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("payload", sa.JSON),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_scheduling_events_meeting", "scheduling_events", ["meeting_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_scheduling_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'scheduling_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER scheduling_events_immutable BEFORE UPDATE OR DELETE ON scheduling_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_scheduling_event_mutation()"
    )

    # Seed reusable starter meeting templates (idempotent by code).
    for code, name, mtype, category, duration, loc in _TEMPLATE_SEED:
        exists = bind.execute(sa.text("SELECT id FROM meeting_templates WHERE code = :c"),
                              {"c": code}).scalar()
        if exists is None:
            bind.execute(sa.text(
                "INSERT INTO meeting_templates (code, name, meeting_type, category, "
                "default_duration_minutes, default_location_type, active) "
                "VALUES (:c, :n, :t, :cat, :d, :loc, true)"),
                {"c": code, "n": name, "t": mtype, "cat": category, "d": duration, "loc": loc})

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

    op.execute("DROP TRIGGER IF EXISTS scheduling_events_immutable ON scheduling_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_scheduling_event_mutation()")
    op.drop_table("scheduling_events")
    op.drop_table("meeting_followups")
    op.drop_table("meeting_reminders")
    op.drop_table("meeting_resource_bookings")
    op.drop_table("meeting_attendees")
    op.drop_table("meetings")
    op.drop_table("scheduling_resources")
    op.drop_table("meeting_templates")
