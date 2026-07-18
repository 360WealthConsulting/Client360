from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, JSON, MetaData, String, Table, Text, UniqueConstraint, func

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
    return {t.name: t for t in (users, teams, team_memberships, capabilities, roles, role_capabilities, user_roles, assignments, sessions, audit_events)}
