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
from sqlalchemy import func, select

from app.connectors.microsoft365.config import get_microsoft365_config
from app.db import engine, microsoft_accounts
from app.security.token_crypto import decrypt, encrypt

# Least-privilege delegated Graph resource scopes (H10 scope reduction). MSAL adds the reserved
# offline_access/openid/profile scopes automatically.
#
# Every scope here is read-only EXCEPT Mail.Send, which lets a signed-in staff user email one existing
# Client360 document from their OWN mailbox (app.services.communications.mail_send). Mail.Send grants
# send-as-self only: it cannot read, modify or delete mail, and it is not Mail.ReadWrite,
# Mail.Send.Shared, or an application permission. Adding it changes the consented scope set, so every
# connected account must reconnect once before sending works.
GRAPH_DELEGATED_SCOPES = [
    "User.Read",
    "Mail.Read",
    "Mail.Send",
    "Calendars.Read",
    "Files.Read.All",
    "Sites.Read.All",
]

#: Back-compat alias — the set is no longer read-only, but the old name is still imported elsewhere.
GRAPH_READ_SCOPES = GRAPH_DELEGATED_SCOPES

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


# --- account resolution -------------------------------------------------------------------------
#
# ONE place decides which connected mailbox a caller acts on. Before this, three surfaces each ran
# their own ``ORDER BY updated_at DESC LIMIT 1``, which returns whichever account connected most
# recently -- so with two connected mailboxes a staff user could be shown a colleague's inbox. A
# mailbox read must be bound to the authenticated principal, and a background job must enumerate
# the accounts it intends to sync rather than silently picking one.
#
# ``microsoft_accounts`` carries no foreign key to ``users`` (``person_id`` points at ``people`` and
# ``user_id`` is the Microsoft/Entra object id, not a Client360 user id), so the account email is the
# only binding available. That is the precedent ``communications.mail_send`` already set.


def account_for_principal(principal, *, conn=None):
    """The connected Microsoft account owned by ``principal``, or ``None``.

    Case-insensitive on the account email, matching the existing send path. It compares with
    ``lower(email) = lower(principal.email)`` rather than ``ILIKE``: an address is a literal here,
    and ``ILIKE`` would treat ``_`` and ``%`` in a local part as wildcards -- ``a_b@x.com`` could
    match ``axb@x.com``. Never falls back to another account: no match returns ``None`` and the
    caller applies its own not-connected behaviour.
    """
    email = (getattr(principal, "email", "") or "").strip().lower()
    if not email:
        return None
    stmt = select(microsoft_accounts).where(func.lower(microsoft_accounts.c.email) == email)

    def _do(c):
        row = c.execute(stmt).mappings().first()
        return dict(row) if row else None

    if conn is not None:
        return _do(conn)
    with engine.connect() as c:
        return _do(c)


def connected_accounts(*, conn=None):
    """EVERY connected Microsoft account, oldest id first.

    For background multi-account synchronisation, which has no authenticated principal. The order is
    deterministic and the enumeration explicit -- a job processes the accounts it was given, it never
    picks one by recency.
    """
    stmt = select(microsoft_accounts).order_by(microsoft_accounts.c.id)

    def _do(c):
        return [dict(r) for r in c.execute(stmt).mappings()]

    if conn is not None:
        return _do(conn)
    with engine.connect() as c:
        return _do(c)


def account_by_id(account_id, *, conn=None):
    """One connected account by its primary key, or ``None``. Used when a caller names the mailbox."""
    stmt = select(microsoft_accounts).where(microsoft_accounts.c.id == account_id)

    def _do(c):
        row = c.execute(stmt).mappings().first()
        return dict(row) if row else None

    if conn is not None:
        return _do(conn)
    with engine.connect() as c:
        return _do(c)


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
    result = client.acquire_token_silent(GRAPH_DELEGATED_SCOPES, account=accounts[0])
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
