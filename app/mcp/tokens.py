"""MCP access tokens — issue, resolve, revoke.

Same credential SHAPE as the existing browser sessions (``app/security/service.py``): a random
URL-safe secret, stored only as its SHA-256, with ``expires_at`` / ``revoked_at`` / ``last_used_at``.
The difference is the store: an MCP token lives in ``mcp_access_tokens`` and is accepted ONLY by the
MCP surface, so it cannot be replayed against the web UI, and revoking it does not disturb the
owner's browser session.

Fails CLOSED in every direction: if migration mcp01 has not been applied, ``resolve`` returns None
(every request denied) rather than raising at import.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import (
    capabilities,
    engine,
    mcp_access_tokens,
    role_capabilities,
    roles,
    user_roles,
    users,
)
from app.mcp import config as mcp_config
from app.mcp.scopes import normalize_scopes
from app.security.models import Principal
from app.security.policy import capability_codes_query

#: Bearer tokens carry this prefix so a leaked string is identifiable in a log or a paste, and so an
#: operator can tell an MCP token from a session cookie at a glance.
TOKEN_PREFIX = "c360mcp_"


@dataclass(frozen=True)
class McpToken:
    """A resolved, currently-valid MCP credential and the principal behind it."""

    token_id: int
    principal: Principal
    scopes: tuple[str, ...]
    label: str

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _available() -> bool:
    """False when migration mcp01 has not been applied — the caller must then fail closed."""
    return mcp_access_tokens is not None


def issue_token(*, user_id: int, scopes, label: str = "", ttl_hours: int | None = None,
                created_by_user_id: int | None = None) -> str:
    """Mint a token for ``user_id`` and return it ONCE, in clear. Only the hash is persisted.

    Scopes are normalised on the way in, so an unrecognised scope is refused at issue time rather
    than silently stored and ignored later.
    """
    if not _available():
        raise RuntimeError("mcp_access_tokens is missing — apply migration mcp01 first")
    granted = normalize_scopes(scopes)
    if not granted:
        raise ValueError("at least one recognised MCP scope is required")
    secret = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    hours = ttl_hours if ttl_hours is not None else mcp_config.token_ttl_hours()
    expires = datetime.now(UTC) + timedelta(hours=max(1, int(hours)))
    with engine.begin() as conn:
        conn.execute(mcp_access_tokens.insert().values(
            user_id=user_id, token_hash=_hash(secret), label=(label or "").strip(),
            scopes=list(granted), expires_at=expires,
            created_by_user_id=created_by_user_id or user_id))
    return secret


def resolve(token: str | None) -> McpToken | None:
    """The valid token behind ``token``, or None.

    None covers every failure indistinguishably — absent, malformed, unknown, expired, revoked,
    owner deactivated, table missing. The caller turns that into one generic 401; the MCP surface
    never reports WHICH of those it was.
    """
    if not token or not _available():
        return None
    now = datetime.now(UTC)
    with engine.begin() as conn:
        row = conn.execute(
            select(mcp_access_tokens.c.id, mcp_access_tokens.c.scopes,
                   mcp_access_tokens.c.label, users.c.id.label("user_id"),
                   users.c.email, users.c.display_name)
            .join(users, users.c.id == mcp_access_tokens.c.user_id)
            .where(mcp_access_tokens.c.token_hash == _hash(token),
                   mcp_access_tokens.c.revoked_at.is_(None),
                   mcp_access_tokens.c.expires_at > now,
                   users.c.status == "active")).mappings().first()
        if not row:
            return None
        # Capabilities are read LIVE on every call, never cached on the token: a role revoked in the
        # UI takes effect on the assistant's very next request, not when the token expires.
        codes = frozenset(conn.scalars(capability_codes_query(
            row["user_id"], users=users, user_roles=user_roles, roles=roles,
            role_capabilities=role_capabilities, capabilities=capabilities)))
        conn.execute(mcp_access_tokens.update()
                     .where(mcp_access_tokens.c.id == row["id"])
                     .values(last_used_at=now))
    return McpToken(
        token_id=row["id"],
        principal=Principal(row["user_id"], row["email"], row["display_name"], codes),
        scopes=normalize_scopes(row["scopes"]),
        label=row["label"] or "")


def revoke_token(token_id: int) -> bool:
    """Revoke by id. Idempotent — revoking an already-revoked token reports False, not an error."""
    if not _available():
        return False
    with engine.begin() as conn:
        result = conn.execute(mcp_access_tokens.update().where(
            mcp_access_tokens.c.id == token_id,
            mcp_access_tokens.c.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
    return bool(result.rowcount)


def revoke_all_for_user(user_id: int) -> int:
    """Revoke every live token for one user. The per-user kill switch (offboarding, suspected leak)."""
    if not _available():
        return 0
    with engine.begin() as conn:
        result = conn.execute(mcp_access_tokens.update().where(
            mcp_access_tokens.c.user_id == user_id,
            mcp_access_tokens.c.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
    return int(result.rowcount or 0)


def list_tokens(*, user_id: int | None = None) -> list[dict]:
    """Token metadata for operators. Never returns a hash or a secret — only who/what/when."""
    if not _available():
        return []
    stmt = select(mcp_access_tokens.c.id, mcp_access_tokens.c.user_id, mcp_access_tokens.c.label,
                  mcp_access_tokens.c.scopes, mcp_access_tokens.c.expires_at,
                  mcp_access_tokens.c.revoked_at, mcp_access_tokens.c.last_used_at,
                  mcp_access_tokens.c.created_at).order_by(mcp_access_tokens.c.id.desc())
    if user_id is not None:
        stmt = stmt.where(mcp_access_tokens.c.user_id == user_id)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]
