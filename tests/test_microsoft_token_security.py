"""Release 0.9.9 Phase 1 — Microsoft 365 token security tests.

Covers Fernet round-trip + fail-closed, the encrypted-cache silent-refresh
helper (MSAL mocked), read-only scopes, and sync-health recording.
"""
import uuid

import pytest
from sqlalchemy import select

from app.db import engine, microsoft_accounts


# --- WP1.1 token_crypto -----------------------------------------------------

def test_encrypt_decrypt_round_trip(monkeypatch):
    from app.security import token_crypto
    monkeypatch.setenv(token_crypto.KEY_ENV_VAR, token_crypto.generate_key())
    plaintext = '{"RefreshToken": {"secret": "abc123"}}'
    ciphertext = token_crypto.encrypt(plaintext)
    assert ciphertext != plaintext and plaintext not in ciphertext
    assert token_crypto.decrypt(ciphertext) == plaintext


def test_missing_key_fails_closed(monkeypatch):
    from app.security import token_crypto
    monkeypatch.delenv(token_crypto.KEY_ENV_VAR, raising=False)
    with pytest.raises(token_crypto.TokenKeyMissing):
        token_crypto.encrypt("x")
    with pytest.raises(token_crypto.TokenKeyMissing):
        token_crypto.decrypt("x")


# --- WP1.4 scope reduction --------------------------------------------------

def test_delegated_scopes_are_read_only():
    from app.routes.microsoft365_oauth import DELEGATED_SCOPES
    # No write / send scopes; offline_access is added by MSAL (reserved, cannot be listed).
    assert not any(s for s in DELEGATED_SCOPES if "Write" in s or "Send" in s)
    assert "offline_access" not in DELEGATED_SCOPES
    assert "Mail.Read" in DELEGATED_SCOPES and "User.Read" in DELEGATED_SCOPES


# --- WP1.3 refresh helper (MSAL mocked) -------------------------------------

class _FakeCache:
    def __init__(self, changed=False):
        self.has_state_changed = changed
        self._data = "{}"
    def deserialize(self, blob): self._data = blob
    def serialize(self): return self._data


class _FakeClient:
    def __init__(self, accounts, token):
        self._accounts = accounts
        self._token = token
    def get_accounts(self): return self._accounts
    def acquire_token_silent(self, scopes, account=None):
        return {"access_token": self._token} if self._token else None


def _account_row(token_cache_encrypted="cipher"):
    s = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        aid = c.execute(microsoft_accounts.insert().values(tenant_id="t", user_id=f"u{s}", email=f"m{s}@e.com",
            token_cache_encrypted=token_cache_encrypted).returning(microsoft_accounts.c.id)).scalar_one()
    with engine.connect() as c:
        return c.execute(select(microsoft_accounts).where(microsoft_accounts.c.id == aid)).mappings().one()


def test_helper_returns_refreshed_token(monkeypatch):
    import app.services.microsoft_identity as mi
    monkeypatch.setattr(mi, "decrypt", lambda b: "{}")
    monkeypatch.setattr(mi, "build_msal_client", lambda cache=None: _FakeClient(["acct"], "fresh-token"))
    monkeypatch.setattr(mi.msal, "SerializableTokenCache", lambda: _FakeCache(changed=False))
    account = _account_row()
    assert mi.get_microsoft_access_token(account) == "fresh-token"


def test_helper_repersists_cache_when_changed(monkeypatch):
    import app.services.microsoft_identity as mi
    persisted = {}
    monkeypatch.setattr(mi, "decrypt", lambda b: "{}")
    monkeypatch.setattr(mi, "build_msal_client", lambda cache=None: _FakeClient(["acct"], "fresh"))
    monkeypatch.setattr(mi.msal, "SerializableTokenCache", lambda: _FakeCache(changed=True))
    monkeypatch.setattr(mi, "persist_token_cache", lambda aid, cache: persisted.setdefault("id", aid))
    account = _account_row()
    mi.get_microsoft_access_token(account)
    assert persisted.get("id") == account["id"]


def test_helper_raises_reconnect_when_no_cache():
    import app.services.microsoft_identity as mi
    account = _account_row(token_cache_encrypted=None)
    with pytest.raises(RuntimeError):
        mi.get_microsoft_access_token(account)


def test_helper_raises_reconnect_on_silent_failure(monkeypatch):
    import app.services.microsoft_identity as mi
    monkeypatch.setattr(mi, "decrypt", lambda b: "{}")
    monkeypatch.setattr(mi, "build_msal_client", lambda cache=None: _FakeClient(["acct"], None))  # silent returns None
    monkeypatch.setattr(mi.msal, "SerializableTokenCache", lambda: _FakeCache())
    account = _account_row()
    with pytest.raises(RuntimeError):
        mi.get_microsoft_access_token(account)


# --- WP1.5/1.6 sync-health --------------------------------------------------

def test_record_sync_health_persists():
    import app.services.microsoft_identity as mi
    account = _account_row()
    mi.record_sync_health(account["id"], "ok")
    with engine.connect() as c:
        row = c.execute(select(microsoft_accounts.c.last_sync_status, microsoft_accounts.c.last_sync_at)
                        .where(microsoft_accounts.c.id == account["id"])).mappings().one()
    assert row["last_sync_status"] == "ok" and row["last_sync_at"] is not None


def test_status_page_shows_sync_health(monkeypatch):
    from app.routes.microsoft365 import microsoft365_status
    account = _account_row()
    import app.services.microsoft_identity as mi
    mi.record_sync_health(account["id"], "ok")
    html = microsoft365_status()
    html = html if isinstance(html, str) else html.body.decode()
    assert "Sync health" in html and "Last sync status" in html
