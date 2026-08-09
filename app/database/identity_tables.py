from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

# --- Durable resolution / alias knowledge (folder_resolution_decisions) -------------------------------
# Positive, REUSABLE approved resolutions: they resolve a subject to a canonical outcome and may be reused
# by future ingestion as durable matching knowledge. Entity-linking decisions point at a canonical row;
# firm_material is an approved positive disposition that carries no canonical entity.
FRD_ENTITY_DECISIONS = (
    "link_person", "create_person",
    "link_household", "create_household",
    "link_business", "create_business",
)
FRD_FIRM_DECISION = "firm_material"
FRD_POSITIVE_DECISIONS = FRD_ENTITY_DECISIONS + (FRD_FIRM_DECISION,)
# Non-reusable dispositions: retained for audit history but NEVER positive matching knowledge.
FRD_NON_REUSABLE_DECISIONS = ("reject", "defer", "ambiguous")
FRD_DECISIONS = FRD_POSITIVE_DECISIONS + FRD_NON_REUSABLE_DECISIONS
# The canonical entity type each entity-linking decision must resolve to.
FRD_ENTITY_TYPES = ("person", "household", "relationship_entity")

# Structural fail-closed guard: the decision must agree with the resulting entity. Entity-linking
# decisions must carry the matching entity type + a non-null id; firm_material carries type 'firm' + null
# id; non-reusable dispositions carry neither. Enforced in the database, not only the service layer.
_FRD_DECISION_ENTITY_CHECK = (
    "(decision IN ('link_person', 'create_person') "
    "  AND resulting_entity_type = 'person' AND resulting_entity_id IS NOT NULL) "
    "OR (decision IN ('link_household', 'create_household') "
    "  AND resulting_entity_type = 'household' AND resulting_entity_id IS NOT NULL) "
    "OR (decision IN ('link_business', 'create_business') "
    "  AND resulting_entity_type = 'relationship_entity' AND resulting_entity_id IS NOT NULL) "
    "OR (decision = 'firm_material' "
    "  AND resulting_entity_type = 'firm' AND resulting_entity_id IS NULL) "
    "OR (decision IN ('reject', 'defer', 'ambiguous') "
    "  AND resulting_entity_type IS NULL AND resulting_entity_id IS NULL)"
)
_FRD_DECISION_LIST = ", ".join(f"'{d}'" for d in FRD_DECISIONS)

def define_identity_tables(metadata: MetaData):
    users = Table("users", metadata,
        Column("id", Integer, primary_key=True), Column("email", String(320), nullable=False), Column("normalized_email", String(320), nullable=False, unique=True),
        Column("display_name", String(255), nullable=False), Column("auth_subject", String(500), unique=True), Column("status", String(50), nullable=False, server_default="invited"),
        Column("mfa_enabled", Boolean, nullable=False, server_default="false"), Column("last_login_at", DateTime(timezone=True)),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()), Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()))
    teams = Table("teams", metadata,
        Column("id", Integer, primary_key=True), Column("code", String(100), nullable=False, unique=True), Column("name", String(255), nullable=False),
        Column("active", Boolean, nullable=False, server_default="true"), Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()))
    team_memberships = Table("team_memberships", metadata,
        Column("id", Integer, primary_key=True), Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("team_id", Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False), Column("membership_role", String(100), nullable=False, server_default="member"),
        Column("effective_date", Date, nullable=False, server_default=func.current_date()), Column("inactive_date", Date),
        UniqueConstraint("user_id", "team_id", "effective_date", name="uq_team_membership_period"))
    capabilities = Table("capabilities", metadata,
        Column("id", Integer, primary_key=True), Column("code", String(150), nullable=False, unique=True), Column("description", Text, nullable=False),
        Column("sensitive", Boolean, nullable=False, server_default="false"))
    roles = Table("roles", metadata,
        Column("id", Integer, primary_key=True), Column("code", String(100), nullable=False, unique=True), Column("name", String(255), nullable=False),
        Column("description", Text), Column("system_role", Boolean, nullable=False, server_default="false"), Column("active", Boolean, nullable=False, server_default="true"))
    role_capabilities = Table("role_capabilities", metadata,
        Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True), Column("capability_id", Integer, ForeignKey("capabilities.id", ondelete="CASCADE"), primary_key=True))
    user_roles = Table("user_roles", metadata,
        Column("id", Integer, primary_key=True), Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False), Column("effective_date", Date, nullable=False, server_default=func.current_date()),
        Column("inactive_date", Date), UniqueConstraint("user_id", "role_id", "effective_date", name="uq_user_role_period"))
    assignments = Table("record_assignments", metadata,
        Column("id", Integer, primary_key=True), Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        Column("team_id", Integer, ForeignKey("teams.id", ondelete="SET NULL")), Column("entity_type", String(50), nullable=False), Column("entity_id", Integer, nullable=False),
        Column("assignment_type", String(100), nullable=False), Column("effective_date", Date, nullable=False, server_default=func.current_date()), Column("inactive_date", Date),
        UniqueConstraint("user_id", "entity_type", "entity_id", "assignment_type", "effective_date", name="uq_record_assignment_period"))
    sessions = Table("user_sessions", metadata,
        Column("id", Integer, primary_key=True), Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("session_hash", String(64), nullable=False, unique=True), Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("expires_at", DateTime(timezone=True), nullable=False), Column("revoked_at", DateTime(timezone=True)), Column("last_seen_at", DateTime(timezone=True)))
    audit_events = Table("audit_events", metadata,
        Column("id", Integer, primary_key=True), Column("actor_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")), Column("action", String(150), nullable=False),
        Column("entity_type", String(100), nullable=False), Column("entity_id", String(255)), Column("outcome", String(50), nullable=False, server_default="success"),
        Column("request_id", String(100), nullable=False), Column("ip_address", String(100)), Column("user_agent", String(1000)), Column("metadata", JSON, nullable=False, server_default="{}"),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()))
    # NOTE: the F3.2 hash-chain columns (prev_hash, entry_hash, hash_version, chain_id)
    # are added by migration f2h3c4a5i6n7 via ALTER TABLE and are NOT declared here.
    # This table is created by migration c410f4a1b2c3 from this declared metadata
    # (`metadata.tables["audit_events"].create(...)`), so declaring the columns here
    # would make that migration pre-create them and the F3.2 ADD COLUMN would fail.
    # app.db reflects the live schema, so runtime code sees the columns. (See docs/DATABASE.md.)
    # Durable resolution / alias knowledge ledger. Subject-GENERIC (subject_system/type/key) so the same
    # ledger serves TaxDome folders now and acquired advisor books, acquired firms, scanned-paper batches,
    # CRM records, and other ingestion sources later. A REUSE layer over the canonical provenance tables
    # (person_source_links / households / relationship_entities) — not a replacement. History is retained:
    # a correction supersedes the prior row (active=false, superseded_at, superseded_by -> new row) rather
    # than overwriting/deleting it, and a PARTIAL UNIQUE index enforces exactly one active row per
    # (subject_system, subject_type, subject_key). resulting_entity_id / exception_id are soft polymorphic
    # references (like record_assignments): the target may be a person/household/relationship_entity, and
    # the Exception Engine table is migration-managed and outside this declared metadata.
    folder_resolution_decisions = Table("folder_resolution_decisions", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("subject_system", String(100), nullable=False),
        Column("subject_type", String(50), nullable=False, server_default="folder"),
        Column("subject_key", String(500), nullable=False),
        Column("display_name", String(500), nullable=False),
        Column("decision", String(50), nullable=False),
        Column("resulting_entity_type", String(50)),
        Column("resulting_entity_id", BigInteger),
        Column("evidence_snapshot", JSONB, nullable=False, server_default="{}"),
        Column("match_reason", Text),
        Column("confidence", Numeric(5, 2)),
        Column("evidence_metadata", JSONB, nullable=False, server_default="{}"),
        Column("reviewed_by", String(255)),
        Column("reviewed_at", DateTime(timezone=True)),
        Column("exception_id", Integer),      # soft ref to exceptions.id (migration-managed table)
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("superseded_at", DateTime(timezone=True)),
        Column("superseded_by", BigInteger, ForeignKey("folder_resolution_decisions.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
        CheckConstraint(f"decision IN ({_FRD_DECISION_LIST})", name="ck_frd_decision"),
        CheckConstraint(_FRD_DECISION_ENTITY_CHECK, name="ck_frd_decision_entity"),
        Index("uq_frd_active", "subject_system", "subject_type", "subject_key", unique=True, postgresql_where=text("active")),
        Index("ix_frd_subject", "subject_system", "subject_type", "subject_key"),
        Index("ix_frd_exception", "exception_id"))
    return {t.name: t for t in (users, teams, team_memberships, capabilities, roles, role_capabilities, user_roles, assignments, sessions, audit_events, folder_resolution_decisions)}
