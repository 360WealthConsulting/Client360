"""Administrative bootstrap for an EXISTING Entra-authenticated user.

Reuses the existing users/roles/user_roles tables and the seeded ``administrator`` role — it creates
no parallel auth or role system, and never creates a duplicate user. It locates a user by email or
auth_subject (the OIDC subject), grants the administrator role idempotently, reports exactly what
changed, and writes an audit event. Privilege elevation requires explicit confirmation unless
``--noninteractive`` (a deployment flag) is supplied.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import engine, roles, user_roles, users
from app.security.audit import write_audit_event
from app.security.identity_utils import normalize_email


def find_user(*, email: str | None = None, subject: str | None = None):
    with engine.connect() as conn:
        if subject:
            row = conn.execute(select(users.c.id, users.c.email, users.c.display_name)
                               .where(users.c.auth_subject == subject)).mappings().first()
            if row:
                return dict(row)
        if email:
            row = conn.execute(select(users.c.id, users.c.email, users.c.display_name)
                               .where(users.c.normalized_email == normalize_email(email))).mappings().first()
            if row:
                return dict(row)
    return None


def _has_active_admin(conn, user_id, role_id) -> bool:
    return conn.scalar(
        select(user_roles.c.id).where(
            user_roles.c.user_id == user_id, user_roles.c.role_id == role_id,
            user_roles.c.inactive_date.is_(None))) is not None


def grant_administrator(*, email: str | None = None, subject: str | None = None,
                        actor_user_id: int | None = None) -> dict:
    """Grant the administrator role to the located existing user. Idempotent. Returns a change report."""
    user = find_user(email=email, subject=subject)
    if user is None:
        raise RuntimeError("No existing user matches that email/subject. The user must sign in via "
                           "Entra at least once (or use `python -m app.security.bootstrap` for the "
                           "very first user).")
    with engine.begin() as conn:
        role_id = conn.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        if role_id is None:
            raise RuntimeError("The 'administrator' role is not seeded in this database.")
        if _has_active_admin(conn, user["id"], role_id):
            return {"user_id": user["id"], "email": user["email"], "granted": False, "already": True}
        conn.execute(user_roles.insert().values(user_id=user["id"], role_id=role_id))
    write_audit_event(action="identity.grant_admin", entity_type="user", entity_id=user["id"],
                      actor_user_id=actor_user_id or user["id"], request_id="deploy-grant-admin",
                      metadata={"granted_role": "administrator", "via": "deploy-cli"})
    return {"user_id": user["id"], "email": user["email"], "granted": True, "already": False}


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Grant the administrator role to an existing user.")
    parser.add_argument("--email")
    parser.add_argument("--subject", help="the Entra/OIDC subject (auth_subject)")
    parser.add_argument("--yes", action="store_true", help="confirm the privilege elevation")
    parser.add_argument("--noninteractive", action="store_true",
                        help="deployment mode: proceed without an interactive prompt")
    args = parser.parse_args(argv)
    if not (args.email or args.subject):
        print("Provide --email or --subject.")
        return 2

    user = find_user(email=args.email, subject=args.subject)
    if user is None:
        print("No matching existing user found.")
        return 1
    print(f"About to grant the administrator role to user #{user['id']} <{user['email']}>.")
    if not (args.yes or args.noninteractive):
        answer = input("Type 'grant' to confirm privilege elevation: ").strip()
        if answer != "grant":
            print("Aborted.")
            return 1
    report = grant_administrator(email=args.email, subject=args.subject)
    if report["already"]:
        print(f"No change — user #{report['user_id']} already has the administrator role.")
    else:
        print(f"Granted administrator to user #{report['user_id']} <{report['email']}>.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
