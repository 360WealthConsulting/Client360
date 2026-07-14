from datetime import date, datetime, timezone
from sqlalchemy import select
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

def _role_capability_codes(connection, role_id):
    return set(connection.scalars(select(capabilities.c.code).select_from(
        role_capabilities.join(capabilities, capabilities.c.id == role_capabilities.c.capability_id)
    ).where(role_capabilities.c.role_id == role_id)))

def assign_role(user_id, role_id, effective_date=None, inactive_date=None, *, actor_capabilities):
    """Assign a role, enforcing that the acting principal already holds every
    capability the target role grants. This prevents a ``role.manage`` holder
    from self-escalating by assigning a more-powerful role (e.g. administrator)
    to themselves or others (H2)."""
    with engine.begin() as connection:
        role = connection.execute(select(roles).where(roles.c.id == role_id)).mappings().one_or_none()
        if role is None: raise ValueError("Role not found")
        beyond = sorted(_role_capability_codes(connection, role_id) - set(actor_capabilities))
        if beyond: raise PermissionError(f"Cannot assign a role granting capabilities you do not hold: {', '.join(beyond)}")
        return connection.execute(user_roles.insert().values(user_id=user_id, role_id=role_id, effective_date=effective_date or date.today(), inactive_date=inactive_date).returning(user_roles.c.id)).scalar_one()

def compose_role(role_id, capability_ids, *, actor_capabilities):
    """Recompose a role's capabilities, enforcing that the acting principal may
    only grant capabilities they themselves hold (ceiling check), and that the
    protected ``administrator`` system role cannot be recomposed at all (H2)."""
    with engine.begin() as connection:
        role = connection.execute(select(roles).where(roles.c.id == role_id)).mappings().one_or_none()
        if role is None: raise ValueError("Role not found")
        if role["code"] == "administrator": raise PermissionError("The administrator role cannot be recomposed")
        requested = set(connection.scalars(select(capabilities.c.code).where(capabilities.c.id.in_(capability_ids)))) if capability_ids else set()
        beyond = sorted(requested - set(actor_capabilities))
        if beyond: raise PermissionError(f"Cannot grant capabilities you do not hold: {', '.join(beyond)}")
        connection.execute(role_capabilities.delete().where(role_capabilities.c.role_id == role_id))
        if capability_ids: connection.execute(role_capabilities.insert(), [{"role_id": role_id, "capability_id": capability_id} for capability_id in sorted(set(capability_ids))])

def add_team_membership(user_id, team_id, membership_role="member", effective_date=None, inactive_date=None):
    with engine.begin() as connection:
        return connection.execute(team_memberships.insert().values(user_id=user_id, team_id=team_id, membership_role=membership_role, effective_date=effective_date or date.today(), inactive_date=inactive_date).returning(team_memberships.c.id)).scalar_one()

def assign_record(user_id, entity_type, entity_id, assignment_type, team_id=None, effective_date=None, inactive_date=None):
    if entity_type not in {"person", "household"}: raise ValueError("Assignments support person or household records")
    with engine.begin() as connection:
        return connection.execute(record_assignments.insert().values(user_id=user_id, team_id=team_id, entity_type=entity_type, entity_id=entity_id, assignment_type=assignment_type, effective_date=effective_date or date.today(), inactive_date=inactive_date).returning(record_assignments.c.id)).scalar_one()
