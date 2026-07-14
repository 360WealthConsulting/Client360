"""Microsoft 365 identity, token, and sync-health helpers.

Release 0.9.9 (Platform Consolidation), Phase 1. Single provider path for
obtaining a valid Microsoft Graph access token via an encrypted, persisted MSAL
token cache with silent refresh (RC8/RC9 H10; PRODUCTION_ARCHITECTURE §8/§9).

Note on ``offline_access``: MSAL rejects it as an explicit scope (it is reserved)
and includes it automatically in the auth-code flow, so a refresh token is issued
into the token cache. The fix is therefore to **persist the (encrypted) MSAL
token cache** — which holds the refresh token — and to obtain access tokens via
``acquire_token_silent`` (which refreshes transparently), rather than storing a
bare, non-refreshable access token.
"""
from datetime import datetime, timezone

import msal
from sqlalchemy import select

from app.connectors.microsoft365.config import get_microsoft365_config
from app.db import engine, microsoft_accounts
from app.security.token_crypto import decrypt, encrypt

# Least-privilege, read-only Graph resource scopes (H10 scope reduction). MSAL
# adds the reserved offline_access/openid/profile scopes automatically.
GRAPH_READ_SCOPES = [
    "User.Read",
    "Mail.Read",
    "Calendars.Read",
    "Files.Read.All",
    "Sites.Read.All",
]

RECONNECT_MESSAGE = "Microsoft 365 must be reconnected before syncing."


def build_msal_client(token_cache=None) -> msal.ConfidentialClientApplication:
    config = get_microsoft365_config()
    return msal.ConfidentialClientApplication(
        client_id=config.client_id,
        authority=config.authority,
        client_credential=config.client_secret,
        token_cache=token_cache,
    )


def serialize_cache(token_cache) -> str:
    """Encrypt a serialized MSAL token cache for storage."""
    return encrypt(token_cache.serialize())


def persist_token_cache(account_id, token_cache):
    """Persist the (possibly refreshed) encrypted cache back to the account row."""
    with engine.begin() as connection:
        connection.execute(
            microsoft_accounts.update()
            .where(microsoft_accounts.c.id == account_id)
            .values(token_cache_encrypted=serialize_cache(token_cache),
                    updated_at=datetime.now(timezone.utc))
        )


def get_microsoft_access_token(account) -> str:
    """Return a valid Graph access token for a connected account.

    Loads the encrypted MSAL token cache, silently refreshes the access token
    (using the cached refresh token) when stale, re-persists the cache if it
    changed, and returns the bearer token. Raises ``RuntimeError`` with a
    reconnect message on any failure, preserving the existing graceful-degradation
    behavior the scheduler already handles.
    """
    blob = account.get("token_cache_encrypted") if hasattr(account, "get") else account["token_cache_encrypted"]
    if not blob:
        raise RuntimeError(RECONNECT_MESSAGE)
    cache = msal.SerializableTokenCache()
    cache.deserialize(decrypt(blob))
    client = build_msal_client(cache)
    accounts = client.get_accounts()
    if not accounts:
        raise RuntimeError(RECONNECT_MESSAGE)
    result = client.acquire_token_silent(GRAPH_READ_SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError(RECONNECT_MESSAGE)
    if cache.has_state_changed:
        persist_token_cache(account["id"], cache)
    return result["access_token"]


def record_sync_health(account_id, status, error=None):
    """Record the outcome of a sync job on the account row (monitoring; §13)."""
    if account_id is None:
        return
    with engine.begin() as connection:
        connection.execute(
            microsoft_accounts.update()
            .where(microsoft_accounts.c.id == account_id)
            .values(last_sync_at=datetime.now(timezone.utc), last_sync_status=status,
                    last_sync_error=(str(error)[:1000] if error else None))
        )
