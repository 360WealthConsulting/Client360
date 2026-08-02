"""Employee & Access Management — read models + thin setters over the EXISTING identity/RBAC tables.

This adds NO parallel identity store and NO schema. It reuses ``users``/``roles``/``user_roles``/
``record_assignments``/``audit_events`` and the authoritative resolvers (``rbac.resolve_capabilities``/
``resolve_roles``, ``authorization.accessible_person_ids``) to present an administrator view, plus the
few thin writes the existing service layer lacked (bind an Entra subject, correct an email, end a role
assignment) — each done through the same tables + audit path, never a bypass. The capability-ceiling
and administrator-role protections in ``app.services.identity`` remain the authoritative guards for
assignment; this module adds the "final active administrator" guard for deactivation/role removal.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.db import (
    audit_events,
    capabilities,
    engine,
    role_capabilities,
    roles,
    user_roles,
    users,
)
from app.security.authorization import accessible_person_ids
from app.security.identity_utils import normalize_email
from app.security.models import Principal
from app.security.rbac import resolve_capabilities

ADMIN_ROLE_CODE = "administrator"

# Capability → business area (first matching rule wins). Vault category caps route to their domain.
_AREA_RULES = [
    ("Documents/Vault", lambda c: c in {"vault.view", "vault.upload", "vault.download", "vault.manage",
                                        "vault.access.all"} or c.startswith("documents.")),
    ("Tax", lambda c: c.startswith("tax.") or c == "vault.category.tax"),
    ("Wealth", lambda c: c.startswith(("wealth", "portfolio")) or c == "vault.category.wealth"),
    ("Accounting/Payroll", lambda c: c.startswith(("accounting", "payroll"))
        or c in {"vault.category.accounting", "vault.category.payroll"}),
    ("Benefits", lambda c: c.startswith("benefits.") or c == "vault.category.benefits"
        or c == "organization.read"),
    ("Insurance", lambda c: c.startswith("insurance.") or c == "vault.category.insurance"),
    ("Workflows/Work Queue", lambda c: c.startswith(("work.", "workflow", "advisor_work", "capacity.",
                                                     "task.", "scheduling.", "opportunity."))),
    ("Compliance/Oversight", lambda c: c.startswith(("compliance.", "audit.", "exception.",
                                                     "observability.", "governance."))
        or c == "vault.category.compliance"),
    ("Administration", lambda c: c in {"identity.manage", "role.manage", "team.manage",
                                       "assignment.manage", "record.read_all", "record.write_all"}
        or c.startswith("security.")),
    ("Core/Client Records", lambda c: c.startswith(("client.", "relationship")) or c == "record.read"),
]


def _area(code: str) -> str:
    for name, rule in _AREA_RULES:
        if rule(code):
            return name
    return "Other"


def group_capabilities(codes) -> dict:
    """Group capability codes by business area (display-only)."""
    grouped: dict[str, list[str]] = {}
    for code in sorted(codes):
        grouped.setdefault(_area(code), []).append(code)
    return grouped


# --- reads -------------------------------------------------------------------

def _active_role_codes(conn, user_id) -> list[str]:
    today = date.today()
    return list(conn.scalars(
        select(roles.c.code).select_from(user_roles.join(roles, roles.c.id == user_roles.c.role_id))
        .where(user_roles.c.user_id == user_id, roles.c.active.is_(True),
               user_roles.c.effective_date <= today,
               (user_roles.c.inactive_date.is_(None)) | (user_roles.c.inactive_date >= today))))


def roster() -> list[dict]:
    """Every employee with status/identity/MFA/roles for the Employees list."""
    with engine.connect() as conn:
        rows = conn.execute(select(users).order_by(users.c.display_name)).mappings().all()
        out = []
        for u in rows:
            codes = _active_role_codes(conn, u["id"])
            out.append({
                "id": u["id"], "display_name": u["display_name"], "email": u["email"],
                "status": u["status"], "mfa_enabled": u["mfa_enabled"],
                "entra_linked": bool(u["auth_subject"]), "last_login_at": u["last_login_at"],
                "roles": codes, "invited": u["status"] == "invited",
                "access_status": _access_status(u["status"], codes, bool(u["auth_subject"])),
            })
    return out


def _access_status(status, role_codes, entra_linked) -> str:
    if status == "disabled":
        return "inactive"
    if status == "invited":
        return "awaiting_activation"
    if not role_codes:
        return "active_no_role"
    if not entra_linked:
        return "active_identity_unmapped"
    return "active"


def blocked_reason(user_id) -> str | None:
    """Why an authenticated Entra user would still be blocked by Client360 (admin-only diagnostic).
    Returns None when the account should pass login. Never surfaced to the blocked employee."""
    with engine.connect() as conn:
        u = conn.execute(select(users).where(users.c.id == user_id)).mappings().first()
        if u is None:
            return "uninvited"
        if u["status"] == "disabled":
            return "inactive"
        if u["status"] == "invited":
            return "awaiting_activation"
        if not _active_role_codes(conn, user_id):
            return "no_role_assigned"
    return None


def employee_detail(user_id) -> dict | None:
    with engine.connect() as conn:
        u = conn.execute(select(users).where(users.c.id == user_id)).mappings().first()
        if u is None:
            return None
        today = date.today()
        active_roles = conn.execute(
            select(user_roles.c.id, roles.c.id.label("role_id"), roles.c.code, roles.c.name,
                   user_roles.c.effective_date)
            .select_from(user_roles.join(roles, roles.c.id == user_roles.c.role_id))
            .where(user_roles.c.user_id == user_id, roles.c.active.is_(True),
                   user_roles.c.effective_date <= today,
                   (user_roles.c.inactive_date.is_(None)) | (user_roles.c.inactive_date >= today))
            .order_by(roles.c.name)).mappings().all()
        all_roles = conn.execute(
            select(roles.c.id, roles.c.code, roles.c.name, roles.c.description)
            .where(roles.c.active.is_(True)).order_by(roles.c.name)).mappings().all()
        recent_audit = conn.execute(
            select(audit_events.c.action, audit_events.c.actor_user_id, audit_events.c.occurred_at,
                   audit_events.c.outcome, audit_events.c.metadata)
            .where(audit_events.c.entity_type == "user", audit_events.c.entity_id == str(user_id))
            .order_by(audit_events.c.occurred_at.desc()).limit(20)).mappings().all()
    caps = resolve_capabilities(user_id)
    scope = accessible_person_ids_summary(user_id, caps)
    return {
        "user": dict(u), "entra_linked": bool(u["auth_subject"]),
        "active_roles": [dict(r) for r in active_roles],
        "assignable_roles": [dict(r) for r in all_roles],
        "effective_capabilities": group_capabilities(caps),
        "capability_count": len(caps),
        "record_scope": scope,
        "recent_audit": [dict(a) for a in recent_audit],
        "blocked_reason": blocked_reason(user_id),
    }


def accessible_person_ids_summary(user_id, caps) -> dict:
    """Read-only record-scope summary: firm-wide, or a bounded count of reachable persons."""
    principal = Principal(user_id, "", "", frozenset(caps))
    with engine.connect() as conn:
        ids = accessible_person_ids(conn, principal)
    if ids is None:
        return {"firm_wide": True, "person_count": None}
    return {"firm_wide": False, "person_count": len(ids)}


def access_profiles() -> list[dict]:
    """Existing roles as administrative access profiles: caps grouped by business area + headcount."""
    today = date.today()
    with engine.connect() as conn:
        role_rows = conn.execute(select(roles).where(roles.c.active.is_(True))
                                 .order_by(roles.c.name)).mappings().all()
        out = []
        for r in role_rows:
            cap_codes = list(conn.scalars(
                select(capabilities.c.code).select_from(
                    role_capabilities.join(capabilities, capabilities.c.id == role_capabilities.c.capability_id))
                .where(role_capabilities.c.role_id == r["id"])))
            headcount = conn.scalar(
                select(func.count(func.distinct(user_roles.c.user_id)))
                .where(user_roles.c.role_id == r["id"],
                       user_roles.c.effective_date <= today,
                       (user_roles.c.inactive_date.is_(None)) | (user_roles.c.inactive_date >= today)))
            out.append({"id": r["id"], "code": r["code"], "name": r["name"],
                        "description": r["description"], "system_role": r["system_role"],
                        "capabilities": group_capabilities(cap_codes),
                        "capability_count": len(cap_codes), "assigned_employees": headcount or 0})
    return out


# --- administrator-safety guards --------------------------------------------

def active_administrator_ids() -> set[int]:
    today = date.today()
    with engine.connect() as conn:
        return set(conn.scalars(
            select(users.c.id).select_from(
                users.join(user_roles, user_roles.c.user_id == users.c.id)
                .join(roles, roles.c.id == user_roles.c.role_id))
            .where(users.c.status == "active", roles.c.code == ADMIN_ROLE_CODE,
                   user_roles.c.effective_date <= today,
                   (user_roles.c.inactive_date.is_(None)) | (user_roles.c.inactive_date >= today))))


def is_last_active_administrator(user_id) -> bool:
    admins = active_administrator_ids()
    return admins == {user_id}


# --- thin setters (existing tables + audited at the route) -------------------

def set_auth_subject(user_id, subject) -> bool:
    """Bind/rebind an employee's Entra subject. Raises ValueError if the subject is already bound to a
    different user (the DB unique constraint) or the user is missing."""
    subject = (subject or "").strip() or None
    with engine.begin() as conn:
        if conn.scalar(select(users.c.id).where(users.c.id == user_id)) is None:
            raise ValueError("User not found")
        if subject is not None:
            other = conn.scalar(select(users.c.id).where(users.c.auth_subject == subject,
                                                         users.c.id != user_id))
            if other is not None:
                raise ValueError("That Microsoft identity is already mapped to another employee")
        return bool(conn.execute(users.update().where(users.c.id == user_id)
                                 .values(auth_subject=subject)).rowcount)


def update_email(user_id, email) -> bool:
    """Correct an employee's work email (+ normalized). Raises ValueError on a duplicate email."""
    norm = normalize_email(email)
    with engine.begin() as conn:
        if conn.scalar(select(users.c.id).where(users.c.id == user_id)) is None:
            raise ValueError("User not found")
        clash = conn.scalar(select(users.c.id).where(users.c.normalized_email == norm,
                                                     users.c.id != user_id))
        if clash is not None:
            raise ValueError("That email is already used by another employee")
        return bool(conn.execute(users.update().where(users.c.id == user_id)
                                 .values(email=email.strip(), normalized_email=norm)).rowcount)


def end_role(user_id, role_id) -> bool:
    """End (deactivate) a role assignment by setting inactive_date=today on active rows. Temporal —
    never a hard delete, so history is preserved."""
    today = date.today()
    with engine.begin() as conn:
        return bool(conn.execute(
            user_roles.update().where(
                user_roles.c.user_id == user_id, user_roles.c.role_id == role_id,
                (user_roles.c.inactive_date.is_(None)) | (user_roles.c.inactive_date >= today))
            .values(inactive_date=today)).rowcount)


def role_code(role_id) -> str | None:
    with engine.connect() as conn:
        return conn.scalar(select(roles.c.code).where(roles.c.id == role_id))
