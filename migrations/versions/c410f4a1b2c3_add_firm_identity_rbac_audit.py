"""add firm identity, capability authorization, teams, and audit

Revision ID: c410f4a1b2c3
Revises: 753c04edab33
"""
from alembic import op
import sqlalchemy as sa
from app.database.schema import metadata

revision = "c410f4a1b2c3"
down_revision = "753c04edab33"
branch_labels = None
depends_on = None

TABLES = ["users", "teams", "team_memberships", "capabilities", "roles", "role_capabilities", "user_roles", "record_assignments", "user_sessions", "audit_events"]
ATTRIBUTED = ("households", "people", "tasks", "activities", "documents")
CAPABILITIES = {
    "client.read": "View assigned clients and households.", "client.write": "Modify assigned clients and households.",
    "task.read": "View assigned and team tasks.", "task.write": "Create and update tasks.",
    "document.read": "View authorized documents.", "document.write": "Manage authorized documents.",
    "communication.read": "View authorized communications.", "communication.write": "Create communications.",
    "team.manage": "Manage teams and memberships.", "identity.manage": "Manage user access and sessions.",
    "role.manage": "Manage role capability composition.", "assignment.manage": "Manage record assignments.", "audit.read": "Read audit events.",
    "record.read_all": "Read records outside explicit assignments.", "record.write_all": "Modify records outside explicit assignments.",
}
ROLE_CAPABILITIES = {
    "administrator": tuple(CAPABILITIES),
    "advisor": ("client.read", "client.write", "task.read", "task.write", "document.read", "document.write", "communication.read", "communication.write"),
    "operations": ("client.read", "client.write", "task.read", "task.write", "document.read", "document.write", "communication.read", "communication.write", "assignment.manage"),
    "compliance": ("client.read", "task.read", "document.read", "communication.read", "audit.read", "record.read_all"),
}

def upgrade():
    bind = op.get_bind()
    for name in TABLES: metadata.tables[name].create(bind, checkfirst=True)
    for table in ATTRIBUTED:
        op.add_column(table, sa.Column("created_by_user_id", sa.Integer()))
        op.add_column(table, sa.Column("updated_by_user_id", sa.Integer()))
        op.create_foreign_key(f"fk_{table}_created_by_user", table, "users", ["created_by_user_id"], ["id"], ondelete="SET NULL")
        op.create_foreign_key(f"fk_{table}_updated_by_user", table, "users", ["updated_by_user_id"], ["id"], ondelete="SET NULL")
    caps, roles, links = (metadata.tables[n] for n in ("capabilities", "roles", "role_capabilities"))
    cap_ids = {code: index for index, code in enumerate(CAPABILITIES, 1)}
    for code, description in CAPABILITIES.items():
        op.bulk_insert(caps, [{"id": cap_ids[code], "code": code, "description": description, "sensitive": code in {"identity.manage", "role.manage", "audit.read"}}])
    for role_id, (code, codes) in enumerate(ROLE_CAPABILITIES.items(), 1):
        op.bulk_insert(roles, [{"id": role_id, "code": code, "name": code.title(), "system_role": True, "active": True}])
        bind.execute(links.insert(), [{"role_id": role_id, "capability_id": cap_ids[item]} for item in codes])
    bind.execute(metadata.tables["teams"].insert(), [{"code": code, "name": name} for code, name in (("wealth", "Wealth"), ("tax", "Tax"), ("insurance", "Insurance"), ("operations", "Operations"), ("compliance", "Compliance"))])
    op.execute("CREATE FUNCTION prevent_audit_event_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'audit_events are append-only'; END; $$ LANGUAGE plpgsql")
    op.execute("CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation()")

def downgrade():
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_event_mutation()")
    for table in reversed(ATTRIBUTED):
        op.drop_constraint(f"fk_{table}_updated_by_user", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_created_by_user", table, type_="foreignkey")
        op.drop_column(table, "updated_by_user_id")
        op.drop_column(table, "created_by_user_id")
    bind = op.get_bind()
    for name in reversed(TABLES): metadata.tables[name].drop(bind, checkfirst=True)
