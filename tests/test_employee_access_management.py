"""Employee & Access Management — admin-only UI over the EXISTING identity/RBAC/audit model.

No schema, no parallel identity system. Tests build a real admin actor + Principal and call the admin
route functions directly (established pattern), asserting authorization, the benefits-only Angel
profile, the last-administrator guard, audit trails, dup/identity matching, and nav gating.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select
from starlette.requests import Request

from app.db import (
    audit_events,
    capabilities,
    engine,
    role_capabilities,
    roles,
    user_roles,
    users,
)
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.security.rbac import resolve_capabilities
from app.services import employee_admin as ea
from app.services.identity import assign_role, invite_user

BENEFITS_ADVISOR_CAPS = frozenset({
    "benefits.enroll", "benefits.read", "benefits.write", "exception.read", "exception.write",
    "organization.read", "vault.category.benefits", "vault.category.general", "vault.download",
    "vault.upload", "vault.view"})
# An admin actor must hold every cap it grants (ceiling); superset of benefits_advisor + admin caps.
ADMIN_CAPS = BENEFITS_ADVISOR_CAPS | frozenset({"identity.manage", "role.manage", "record.read_all"})
NON_ADMIN = Principal(0, "staff@e.test", "Staff", frozenset({"client.read", "work.read"}))


@pytest.fixture
def env():
    tag = uuid.uuid4().hex[:8]
    created = {"users": []}
    with engine.begin() as c:
        admin_uid = c.execute(insert(users).values(
            email=f"admin{tag}@e.test", normalized_email=f"admin{tag}@e.test", display_name="Michael",
            auth_subject=f"admin-{tag}", status="active").returning(users.c.id)).scalar_one()
        admin_role = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        c.execute(insert(user_roles).values(user_id=admin_uid, role_id=admin_role))
    created["admin_uid"] = admin_uid
    created["admin"] = Principal(admin_uid, "michael@e.test", "Michael", ADMIN_CAPS)
    created["benefits_role_id"] = _role_id("benefits_advisor")
    created["admin_role_id"] = admin_role
    created["tag"] = tag
    yield created
    # Leave user rows referenced by immutable audit; clear role assignments we created.
    with engine.begin() as c:
        for uid in [*created["users"], admin_uid]:
            c.execute(delete(user_roles).where(user_roles.c.user_id == uid))


def _role_id(code):
    with engine.connect() as c:
        return c.scalar(select(roles.c.id).where(roles.c.code == code))


def _req(env):
    scope = {"type": "http", "method": "POST", "path": "/admin/employees", "headers": [],
             "query_string": b"", "client": ("127.0.0.1", 0), "state": {}}
    r = Request(scope)
    r.state.request_id = f"test-{uuid.uuid4()}"
    return r


def _invite(env, name):
    email = f"{name.lower()}-{env['tag']}@firm.test"
    uid = invite_user(email, name)
    env["users"].append(uid)
    return uid, email


def _audit_count(action, user_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            (audit_events.c.action == action) & (audit_events.c.entity_id == str(user_id))))


# --- authorization -----------------------------------------------------------

def test_admin_can_list_employees(env):
    from app.routes.admin import employees_list
    html = employees_list(_req(env), principal=env["admin"]).body.decode()
    assert "Employees" in html and "Michael" in html


def test_non_admin_cannot_list_or_inspect_employees():
    gate = require_capability("identity.manage")
    with pytest.raises(HTTPException) as exc:
        gate(principal=NON_ADMIN)
    assert exc.value.status_code == 403


def test_admin_routes_are_capability_gated_in_middleware():
    from app.security.middleware import RULES
    def cap(path):
        return next((code for pat, code in RULES if pat.search(path)), None)
    assert cap("/admin/employees") == "identity.manage"
    assert cap("/admin/employees/5") == "identity.manage"
    assert cap("/admin/access-profiles") == "identity.manage"


# --- invite / activate -------------------------------------------------------

def test_admin_can_invite_and_activate_employee(env):
    from app.routes.admin import employee_status
    uid, email = _invite(env, "Newhire")
    with engine.connect() as c:
        assert c.scalar(select(users.c.status).where(users.c.id == uid)) == "invited"
    employee_status(uid, _req(env), status="active", principal=env["admin"])
    with engine.connect() as c:
        assert c.scalar(select(users.c.status).where(users.c.id == uid)) == "active"


# --- Angel: benefits-only profile -------------------------------------------

def test_admin_can_assign_benefits_profile_to_angel(env):
    from app.routes.admin import employee_assign_role
    uid, _ = _invite(env, "Angel")
    employee_assign_role(uid, _req(env), role_id=env["benefits_role_id"], principal=env["admin"])
    with engine.connect() as c:
        assigned = c.scalar(select(func.count()).select_from(user_roles).where(
            user_roles.c.user_id == uid, user_roles.c.role_id == env["benefits_role_id"]))
    assert assigned == 1


def test_angel_effective_capabilities_are_benefits_and_benefits_vault(env):
    uid, _ = _invite(env, "Angel")
    assign_role(uid, env["benefits_role_id"], actor_capabilities=env["admin"].capabilities)
    caps = resolve_capabilities(uid)
    assert {"benefits.read", "benefits.write", "vault.category.benefits", "vault.view",
            "vault.upload", "vault.download", "organization.read"} <= caps


def test_angel_lacks_tax_wealth_admin_and_unrestricted_scope(env):
    uid, _ = _invite(env, "Angel")
    assign_role(uid, env["benefits_role_id"], actor_capabilities=env["admin"].capabilities)
    caps = resolve_capabilities(uid)
    for forbidden in ("tax.read", "vault.category.tax", "vault.category.wealth", "wealth.read",
                      "identity.manage", "role.manage", "record.read_all", "audit.read",
                      "compliance.supervise", "benefits.sensitive.read"):
        assert forbidden not in caps, forbidden


def test_benefits_profile_carries_no_tax_wealth_or_admin(env):
    # Existing role restrictions are authoritative (regression for Sarah/Jessica/Lauren-style roles):
    # the benefits profile grants nothing outside its domain.
    profiles = {p["code"]: p for p in ea.access_profiles()}
    ba_areas = profiles["benefits_advisor"]["capabilities"]
    assert "Tax" not in ba_areas and "Wealth" not in ba_areas and "Administration" not in ba_areas


def test_ceiling_prevents_privilege_broadening(env):
    # A role.manage holder cannot assign a role granting caps they lack (protects all staff).
    limited = frozenset({"role.manage"})
    with pytest.raises(PermissionError):
        assign_role(env["users"][0] if env["users"] else _invite(env, "X")[0],
                    env["admin_role_id"], actor_capabilities=limited)


# --- audit -------------------------------------------------------------------

def test_role_assignment_is_audited(env):
    from app.routes.admin import employee_assign_role
    uid, _ = _invite(env, "Angel")
    employee_assign_role(uid, _req(env), role_id=env["benefits_role_id"], principal=env["admin"])
    assert _audit_count("authorization.role_assigned", uid) >= 1


def test_activation_is_audited(env):
    from app.routes.admin import employee_status
    uid, _ = _invite(env, "Newhire")
    employee_status(uid, _req(env), status="active", principal=env["admin"])
    assert _audit_count("identity.status_changed", uid) >= 1


def test_invitation_is_audited(env):
    from app.routes.admin import employee_invite
    email = f"invitee-{env['tag']}@firm.test"
    employee_invite(_req(env), email=email, display_name="Invitee", role_id=None,
                           principal=env["admin"])
    with engine.connect() as c:
        uid = c.scalar(select(users.c.id).where(users.c.email == email))
    env["users"].append(uid)
    assert _audit_count("identity.user_invited", uid) >= 1


# --- identity matching / dup guard ------------------------------------------

def test_exact_entra_identity_mapping_is_enforced(env):
    a, _ = _invite(env, "Aaa")
    b, _ = _invite(env, "Bbb")
    subject = f"shared-subj-{env['tag']}"
    assert ea.set_auth_subject(a, subject) is True
    with pytest.raises(ValueError):                 # same subject cannot map to a second employee
        ea.set_auth_subject(b, subject)


def test_no_duplicate_user_for_existing_identity(env):
    email = f"dup-{env['tag']}@firm.test"
    first = invite_user(email, "Dup One")
    env["users"].append(first)
    second = invite_user(email, "Dup One Again")    # same normalized email → upsert, same id
    assert second == first


# --- final administrator guard ----------------------------------------------

def test_final_administrator_cannot_be_deactivated(env, monkeypatch):
    from app.routes.admin import employee_status
    monkeypatch.setattr(ea, "is_last_active_administrator", lambda uid: True)
    resp = employee_status(env["admin_uid"], _req(env), status="disabled", principal=env["admin"])
    assert resp.status_code == 303 and "err=" in resp.headers["location"]
    with engine.connect() as c:
        assert c.scalar(select(users.c.status).where(users.c.id == env["admin_uid"])) == "active"


def test_final_administrator_role_cannot_be_removed(env, monkeypatch):
    from app.routes.admin import employee_remove_role
    monkeypatch.setattr(ea, "is_last_active_administrator", lambda uid: True)
    resp = employee_remove_role(env["admin_uid"], _req(env), role_id=env["admin_role_id"],
                                principal=env["admin"])
    assert resp.status_code == 303 and "err=" in resp.headers["location"]
    with engine.connect() as c:
        still = c.scalar(select(func.count()).select_from(user_roles).where(
            user_roles.c.user_id == env["admin_uid"], user_roles.c.role_id == env["admin_role_id"],
            user_roles.c.inactive_date.is_(None)))
    assert still == 1


# --- nav gating + fail-closed ------------------------------------------------

def test_navigation_appears_only_for_administrators(env):
    from app.routes.home import staff_home
    def _get():
        s = {"type": "http", "method": "GET", "path": "/home", "headers": [], "query_string": b"",
             "state": {}}
        return Request(s)
    admin_html = staff_home(_get(), principal=env["admin"]).body.decode()
    staff_html = staff_home(_get(), principal=NON_ADMIN).body.decode()
    assert "/admin/employees" in admin_html and "Administration" in admin_html
    assert "/admin/employees" not in staff_html


def test_existing_403_remains_fail_closed():
    import pathlib
    src = pathlib.Path("app/routes/auth.py").read_text()
    assert "Account is inactive, uninvited, or missing required MFA" in src


# --- multi-select access-profile editor (multiple profiles, union) -----------

def _role_caps(role_id):
    with engine.connect() as c:
        return set(c.scalars(
            select(capabilities.c.code).select_from(
                role_capabilities.join(capabilities, capabilities.c.id == role_capabilities.c.capability_id))
            .where(role_capabilities.c.role_id == role_id)))


def _two_non_admin_roles():
    """Two distinct active non-administrator roles with capabilities, for union tests."""
    with engine.connect() as c:
        rows = c.execute(
            select(roles.c.id).where(roles.c.active.is_(True), roles.c.code != "administrator")
            .order_by(roles.c.id)).scalars().all()
    picked = [rid for rid in rows if _role_caps(rid)][:2]
    return picked[0], picked[1]


def _actor_over(env, role_ids):
    """An actor Principal that holds every capability the given roles grant (satisfies the ceiling)."""
    caps = {"role.manage"}
    for rid in role_ids:
        caps |= _role_caps(rid)
    return Principal(env["admin_uid"], "actor@e.test", "Actor", frozenset(caps))


def test_editor_assigns_multiple_profiles_with_union_of_capabilities(env):
    from app.routes.admin import employee_set_roles
    uid, _ = _invite(env, "Multi")
    r1, r2 = _two_non_admin_roles()
    actor = _actor_over(env, [r1, r2])
    employee_set_roles(uid, _req(env), role_ids=[r1, r2], principal=actor)
    with engine.connect() as c:
        active = set(c.scalars(select(user_roles.c.role_id).where(
            user_roles.c.user_id == uid, user_roles.c.inactive_date.is_(None))))
    assert {r1, r2} <= active
    # Effective permissions are the union of both profiles — the RBAC engine is unchanged.
    assert resolve_capabilities(uid) >= (_role_caps(r1) | _role_caps(r2))


def test_editor_reconciles_by_removing_unchecked_profiles(env):
    from app.routes.admin import employee_set_roles
    uid, _ = _invite(env, "Reconcile")
    r1, r2 = _two_non_admin_roles()
    employee_set_roles(uid, _req(env), role_ids=[r1, r2], principal=_actor_over(env, [r1, r2]))
    # Re-submit with only r1 checked → r2 is ended, r1 remains.
    employee_set_roles(uid, _req(env), role_ids=[r1], principal=_actor_over(env, [r1, r2]))
    assert ea.active_role_ids(uid) == {r1}


def test_editor_still_enforces_capability_ceiling(env):
    from app.routes.admin import employee_set_roles
    uid, _ = _invite(env, "Ceiling")
    r1, _r2 = _two_non_admin_roles()
    limited = Principal(env["admin_uid"], "lim@e.test", "Lim", frozenset({"role.manage"}))
    resp = employee_set_roles(uid, _req(env), role_ids=[r1], principal=limited)
    assert resp.status_code == 303 and "err=" in resp.headers["location"]
    assert r1 not in ea.active_role_ids(uid)   # broadening beyond the actor's caps is refused


def test_editor_honours_final_administrator_guard(env, monkeypatch):
    from app.routes.admin import employee_set_roles
    monkeypatch.setattr(ea, "is_last_active_administrator", lambda uid: True)
    # Try to drop every profile from the sole administrator → administrator role is retained.
    resp = employee_set_roles(env["admin_uid"], _req(env), role_ids=[], principal=env["admin"])
    assert resp.status_code == 303 and "err=" in resp.headers["location"]
    assert env["admin_role_id"] in ea.active_role_ids(env["admin_uid"])


def test_single_role_via_editor_is_backward_compatible(env):
    from app.routes.admin import employee_set_roles
    uid, _ = _invite(env, "Solo")
    employee_set_roles(uid, _req(env), role_ids=[env["benefits_role_id"]], principal=env["admin"])
    assert ea.active_role_ids(uid) == {env["benefits_role_id"]}


def test_invite_can_assign_multiple_profiles(env):
    from app.routes.admin import employee_invite
    email = f"multi-{env['tag']}@firm.test"
    r1, r2 = _two_non_admin_roles()
    employee_invite(_req(env), email=email, display_name="Multi Invite", role_ids=[r1, r2],
                    principal=_actor_over(env, [r1, r2]))
    with engine.connect() as c:
        uid = c.scalar(select(users.c.id).where(users.c.email == email))
    env["users"].append(uid)
    assert {r1, r2} <= ea.active_role_ids(uid)
