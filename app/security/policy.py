from datetime import date
from sqlalchemy import or_, select

def capability_codes_query(user_id, *, users, user_roles, roles, role_capabilities, capabilities):
    today = date.today()
    return select(capabilities.c.code).select_from(user_roles.join(roles, roles.c.id == user_roles.c.role_id).join(role_capabilities, role_capabilities.c.role_id == roles.c.id).join(capabilities, capabilities.c.id == role_capabilities.c.capability_id)).where(user_roles.c.user_id == user_id, roles.c.active.is_(True), user_roles.c.effective_date <= today, or_(user_roles.c.inactive_date.is_(None), user_roles.c.inactive_date >= today))

def has_record_scope(connection, principal, entity_type, entity_id, *, record_assignments, write=False):
    bypass = "record.write_all" if write else "record.read_all"
    if principal.can(bypass): return True
    today = date.today()
    return connection.scalar(select(record_assignments.c.id).where(record_assignments.c.user_id == principal.user_id, record_assignments.c.entity_type == entity_type, record_assignments.c.entity_id == entity_id, record_assignments.c.effective_date <= today, or_(record_assignments.c.inactive_date.is_(None), record_assignments.c.inactive_date >= today)).limit(1)) is not None
