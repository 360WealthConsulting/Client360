import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db import capabilities, engine, role_capabilities, roles, user_roles, user_sessions, users
from app.integrations.identity.base import IdentityClaims
from app.security.models import Principal
from app.security.policy import capability_codes_query
from app.security.identity_utils import normalize_email

def _hash(value): return hashlib.sha256(value.encode()).hexdigest()

def authenticate_claims(claims: IdentityClaims, require_mfa=True):
    if require_mfa and not claims.mfa_authenticated: return None
    with engine.begin() as connection:
        user = connection.execute(select(users).where(users.c.auth_subject == claims.subject)).mappings().first()
        if not user:
            user = connection.execute(select(users).where(users.c.normalized_email == normalize_email(claims.email))).mappings().first()
            if user and not user["auth_subject"]:
                connection.execute(users.update().where(users.c.id == user["id"]).values(auth_subject=claims.subject))
        if not user or user["status"] != "active": return None
        connection.execute(users.update().where(users.c.id == user["id"]).values(last_login_at=datetime.now(timezone.utc), mfa_enabled=claims.mfa_authenticated))
        return user["id"]

def create_session(user_id, hours=8):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=hours)
    with engine.begin() as connection:
        connection.execute(user_sessions.insert().values(user_id=user_id, session_hash=_hash(token), expires_at=expires))
    return token

def revoke_session(token):
    with engine.begin() as connection:
        connection.execute(user_sessions.update().where(user_sessions.c.session_hash == _hash(token), user_sessions.c.revoked_at.is_(None)).values(revoked_at=datetime.now(timezone.utc)))

def resolve_principal(token):
    if not token: return None
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        row = connection.execute(select(user_sessions.c.id, users.c.id.label("user_id"), users.c.email, users.c.display_name).join(users, users.c.id == user_sessions.c.user_id).where(user_sessions.c.session_hash == _hash(token), user_sessions.c.revoked_at.is_(None), user_sessions.c.expires_at > now, users.c.status == "active")).mappings().first()
        if not row: return None
        codes = frozenset(connection.scalars(capability_codes_query(row["user_id"], users=users, user_roles=user_roles, roles=roles, role_capabilities=role_capabilities, capabilities=capabilities)))
        connection.execute(user_sessions.update().where(user_sessions.c.id == row["id"]).values(last_seen_at=now))
        return Principal(row["user_id"], row["email"], row["display_name"], codes)
