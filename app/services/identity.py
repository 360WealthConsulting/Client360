from datetime import date, datetime, timezone
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db import capabilities, engine, record_assignments, role_capabilities, roles, team_memberships, teams, user_roles, user_sessions, users
from app.security.identity_utils import normalize_email

def list_identity_data():
    with engine.connect() as connection:
        return {"users": connection.execute(select(users).order_by(users.c.display_name)).mappings().all(), "teams": connection.execute(select(teams).order_by(teams.c.name)).mappings().all(), "roles": connection.execute(select(roles).order_by(roles.c.name)).mappings().all(), "capabilities": connection.execute(select(capabilities).order_by(capabilities.c.code)).mappings().all()}

def invite_user(email, display_name, auth_subject=None):
    values = {"email": email.strip(), "normalized_email": normalize_email(email), "display_name": display_name.strip(), "auth_subject": auth_subject, "status": "invited"}
    updates = {"email": values["email"], "display_name": values["display_name"]}
    if auth_subject:
        updates["auth_subject"] = auth_subject
    with engine.begin() as connection:
        return connection.execute(pg_insert(users).values(**values).on_conflict_do_update(index_elements=[users.c.normalized_email], set_=updates).returning(users.c.id)).scalar_one()

def set_user_status(user_id, status):
    if status not in {"invited", "active", "disabled"}: raise ValueError("Invalid user status")
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        changed = connection.execute(users.update().where(users.c.id == user_id).values(status=status)).rowcount
        if status == "disabled": connection.execute(user_sessions.update().where(user_sessions.c.user_id == user_id, user_sessions.c.revoked_at.is_(None)).values(revoked_at=now))
    return bool(changed)

def assign_role(user_id, role_id, effective_date=None, inactive_date=None):
    with engine.begin() as connection:
        return connection.execute(user_roles.insert().values(user_id=user_id, role_id=role_id, effective_date=effective_date or date.today(), inactive_date=inactive_date).returning(user_roles.c.id)).scalar_one()

def compose_role(role_id, capability_ids):
    with engine.begin() as connection:
        connection.execute(role_capabilities.delete().where(role_capabilities.c.role_id == role_id))
        if capability_ids: connection.execute(role_capabilities.insert(), [{"role_id": role_id, "capability_id": capability_id} for capability_id in sorted(set(capability_ids))])

def add_team_membership(user_id, team_id, membership_role="member", effective_date=None, inactive_date=None):
    with engine.begin() as connection:
        return connection.execute(team_memberships.insert().values(user_id=user_id, team_id=team_id, membership_role=membership_role, effective_date=effective_date or date.today(), inactive_date=inactive_date).returning(team_memberships.c.id)).scalar_one()

def assign_record(user_id, entity_type, entity_id, assignment_type, team_id=None, effective_date=None, inactive_date=None):
    if entity_type not in {"person", "household"}: raise ValueError("Assignments support person or household records")
    with engine.begin() as connection:
        return connection.execute(record_assignments.insert().values(user_id=user_id, team_id=team_id, entity_type=entity_type, entity_id=entity_id, assignment_type=assignment_type, effective_date=effective_date or date.today(), inactive_date=inactive_date).returning(record_assignments.c.id)).scalar_one()
