"""One-time, auditable initial administrator bootstrap.

Usage: python -m app.security.bootstrap --email admin@example.com --name "Firm Admin" --subject <oidc-subject>
"""
import argparse
from sqlalchemy import select
from app.db import engine, roles, user_roles, users
from app.security.audit import write_audit_event
from app.security.identity_utils import normalize_email

def bootstrap_administrator(email, name, subject):
    with engine.begin() as connection:
        if connection.scalar(select(users.c.id).limit(1)) is not None:
            raise RuntimeError("Bootstrap is allowed only before the first user exists")
        user_id = connection.execute(users.insert().values(email=email, normalized_email=normalize_email(email), display_name=name, auth_subject=subject, status="active").returning(users.c.id)).scalar_one()
        role_id = connection.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        connection.execute(user_roles.insert().values(user_id=user_id, role_id=role_id))
    write_audit_event(action="identity.bootstrap_admin", entity_type="user", entity_id=user_id, actor_user_id=user_id, request_id="bootstrap-cli")
    return user_id

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True); parser.add_argument("--name", required=True); parser.add_argument("--subject", required=True)
    args = parser.parse_args()
    print(bootstrap_administrator(args.email, args.name, args.subject))
