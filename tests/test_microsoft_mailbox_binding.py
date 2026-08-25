"""The Microsoft mailbox a caller acts on is bound to the AUTHENTICATED PRINCIPAL.

Before this, /microsoft365/mail and the mail sync job each resolved their mailbox with
``ORDER BY updated_at DESC LIMIT 1`` -- whichever account reconnected most recently. With two
connected mailboxes that showed a staff user a colleague's inbox, and it meant the sync job only
ever ingested one account while the rest went stale. These tests pin the corrected selection and,
deliberately, the ABSENCE of any fallback: no match lists nothing, never someone else's mail.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, insert, select

from app.db import engine, microsoft_accounts
from app.security.models import Principal
from app.services.microsoft_identity import (
    account_by_id,
    account_for_principal,
    connected_accounts,
)
from tests._portal_util import fake_request, render

_CAP = frozenset({"communication.read", "communications.view", "client.read"})
_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        with engine.begin() as c:
            c.execute(delete(microsoft_accounts)
                      .where(microsoft_accounts.c.tenant_id == tag))
    _TAGS.clear()


def _tag():
    t = "mbx-" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _accounts(tag):
    """Two connected mailboxes. B is deliberately the most recently updated row, which is exactly
    what the old ``ORDER BY updated_at DESC`` would have handed to everyone."""
    now = datetime.now(UTC)
    with engine.begin() as c:
        a_id = c.execute(insert(microsoft_accounts).values(
            tenant_id=tag, user_id=f"entra-a-{tag}", email=f"alice.{tag}@firm.test",
            display_name="Alice", access_token=f"token-a-{tag}",
            expires_at=now + timedelta(hours=1), updated_at=now - timedelta(hours=2),
        ).returning(microsoft_accounts.c.id)).scalar_one()
        b_id = c.execute(insert(microsoft_accounts).values(
            tenant_id=tag, user_id=f"entra-b-{tag}", email=f"bob.{tag}@firm.test",
            display_name="Bob", access_token=f"token-b-{tag}",
            expires_at=now + timedelta(hours=1), updated_at=now,
        ).returning(microsoft_accounts.c.id)).scalar_one()
    return {"a_id": a_id, "b_id": b_id,
            "a_email": f"alice.{tag}@firm.test", "b_email": f"bob.{tag}@firm.test"}


def _principal(email, caps=_CAP, uid=1):
    return Principal(uid, email, "Staff", frozenset(caps))


# --------------------------------------------------------------- the resolver
def test_resolver_returns_the_principals_own_account():
    tag = _tag(); f = _accounts(tag)
    assert account_for_principal(_principal(f["a_email"]))["id"] == f["a_id"]
    assert account_for_principal(_principal(f["b_email"]))["id"] == f["b_id"]


def test_resolver_email_match_is_case_insensitive():
    tag = _tag(); f = _accounts(tag)
    shouty = _principal(f["a_email"].upper())
    assert account_for_principal(shouty)["id"] == f["a_id"]


def test_resolver_treats_underscores_as_literals_not_wildcards():
    """The previous ILIKE match would have let 'a_c@' match a stored 'abc@' address."""
    tag = _tag(); _accounts(tag)
    with engine.begin() as c:
        c.execute(insert(microsoft_accounts).values(
            tenant_id=tag, user_id=f"entra-lit-{tag}", email=f"abc.{tag}@firm.test",
            access_token="t", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    assert account_for_principal(_principal(f"a_c.{tag}@firm.test")) is None


def test_resolver_fails_closed_with_no_match_and_no_fallback():
    tag = _tag(); _accounts(tag)
    assert account_for_principal(_principal(f"carol.{tag}@firm.test")) is None
    assert account_for_principal(_principal("")) is None
    assert account_for_principal(_principal(None)) is None


def test_connected_accounts_enumerates_deterministically():
    tag = _tag(); f = _accounts(tag)
    ids = [a["id"] for a in connected_accounts() if a["tenant_id"] == tag]
    assert ids == sorted(ids) == sorted([f["a_id"], f["b_id"]])


def test_account_by_id_returns_only_that_account():
    tag = _tag(); f = _accounts(tag)
    assert account_by_id(f["a_id"])["id"] == f["a_id"]
    assert account_by_id(-1) is None


# --------------------------------------------------------------- GET /microsoft365/mail
def _mail_page(monkeypatch, principal, *, subjects_by_token):
    """Render the route with Graph stubbed. The stub keys its payload by BEARER TOKEN, so the
    assertion is literally 'which mailbox's credential was used'."""
    import app.routes.microsoft365_mail as mod
    seen = {}

    class _Resp:
        status_code = 200
        ok = True

        def __init__(self, subjects):
            self._subjects = subjects

        def json(self):
            return {"value": [
                {"id": f"m-{s}", "subject": s, "from": {"emailAddress": {
                    "name": "Sender", "address": "sender@example.test"}},
                 "receivedDateTime": "2026-08-25T10:00:00Z", "bodyPreview": "",
                 "webLink": "https://outlook.test/x", "isRead": True,
                 "hasAttachments": False} for s in self._subjects]}

    def _get(url, headers=None, params=None, timeout=None):
        token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        seen["token"] = token
        return _Resp(subjects_by_token.get(token, []))

    monkeypatch.setattr(mod.requests, "get", _get)
    response = mod.microsoft365_mail(
        fake_request("/microsoft365/mail", state_principal=principal), principal=principal)
    return response, seen


def test_user_a_sees_only_user_a_mailbox(monkeypatch):
    tag = _tag(); f = _accounts(tag)
    resp, seen = _mail_page(monkeypatch, _principal(f["a_email"]), subjects_by_token={
        f"token-a-{tag}": ["ALICE-ONLY-MESSAGE"], f"token-b-{tag}": ["BOB-ONLY-MESSAGE"]})
    html = render(resp)
    assert seen["token"] == f"token-a-{tag}"
    assert "ALICE-ONLY-MESSAGE" in html
    assert "BOB-ONLY-MESSAGE" not in html


def test_user_b_sees_only_user_b_mailbox(monkeypatch):
    tag = _tag(); f = _accounts(tag)
    resp, seen = _mail_page(monkeypatch, _principal(f["b_email"], uid=2), subjects_by_token={
        f"token-a-{tag}": ["ALICE-ONLY-MESSAGE"], f"token-b-{tag}": ["BOB-ONLY-MESSAGE"]})
    html = render(resp)
    assert seen["token"] == f"token-b-{tag}"
    assert "BOB-ONLY-MESSAGE" in html
    assert "ALICE-ONLY-MESSAGE" not in html


def test_user_a_does_not_get_user_b_mail_because_b_row_is_newer(monkeypatch):
    """The exact regression: B's row has the later updated_at, which the old query preferred."""
    tag = _tag(); f = _accounts(tag)
    with engine.connect() as c:
        newest = c.execute(select(microsoft_accounts.c.id)
                           .where(microsoft_accounts.c.tenant_id == tag)
                           .order_by(microsoft_accounts.c.updated_at.desc())
                           .limit(1)).scalar_one()
    assert newest == f["b_id"]                       # the old selection really would pick B
    _, seen = _mail_page(monkeypatch, _principal(f["a_email"]), subjects_by_token={
        f"token-a-{tag}": ["ALICE-ONLY-MESSAGE"], f"token-b-{tag}": ["BOB-ONLY-MESSAGE"]})
    assert seen["token"] == f"token-a-{tag}"


def test_route_uses_the_selected_users_own_token(monkeypatch):
    tag = _tag(); f = _accounts(tag)
    _, seen = _mail_page(monkeypatch, _principal(f["b_email"]), subjects_by_token={})
    assert seen["token"] == f"token-b-{tag}"
    assert seen["token"] != f"token-a-{tag}"


def test_no_connected_account_fails_closed_without_calling_graph(monkeypatch):
    import app.routes.microsoft365_mail as mod
    tag = _tag(); _accounts(tag)

    def _must_not_call(*a, **k):
        raise AssertionError("Graph must not be called without a bound account")

    monkeypatch.setattr(mod.requests, "get", _must_not_call)
    resp = mod.microsoft365_mail(
        fake_request("/microsoft365/mail"), principal=_principal(f"nobody.{tag}@firm.test"))
    assert resp.status_code == 303
    assert resp.headers["location"] == "/microsoft365/connect"


def test_route_still_requires_communication_read():
    from app.routes.microsoft365_mail import microsoft365_mail
    from app.security.dependencies import CAPABILITY_DEP_ATTR
    from app.security.middleware import RULES
    dep = next(d.dependency for d in microsoft365_mail.__defaults__
               if getattr(d, "dependency", None) is not None)
    assert getattr(dep, CAPABILITY_DEP_ATTR) == ("communication.read",)
    rule = next(code for pattern, code in RULES if pattern.search("/microsoft365/mail"))
    assert rule == "communication.read"


# --------------------------------------------------------------- sync job
def _stub_sync(monkeypatch, *, per_token_messages=None, failing_account_ids=()):
    import app.jobs.microsoft_mail_sync as mod
    calls = {"tokens": [], "health": []}

    def _token(account):
        if account["id"] in failing_account_ids:
            raise RuntimeError("token refresh failed")
        return f"token-{account['id']}"

    class _Resp:
        status_code = 200

        def __init__(self, msgs):
            self._msgs = msgs

        def raise_for_status(self):
            return None

        def json(self):
            return {"value": self._msgs}

    def _get(url, headers=None, params=None, timeout=None):
        token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        calls["tokens"].append(token)
        return _Resp((per_token_messages or {}).get(token, []))

    monkeypatch.setattr(mod, "get_microsoft_access_token", _token)
    monkeypatch.setattr(mod, "record_sync_health",
                        lambda aid, status, error=None: calls["health"].append((aid, status)))
    monkeypatch.setattr(mod.requests, "get", _get)
    return mod, calls


def test_sync_enumerates_every_connected_account(monkeypatch):
    tag = _tag(); f = _accounts(tag)
    mod, calls = _stub_sync(monkeypatch)
    result = mod.sync_recent_mail(top=5)
    for aid in (f["a_id"], f["b_id"]):
        assert f"token-{aid}" in calls["tokens"]
    assert result["accounts_synced"] >= 2


def test_sync_never_silently_picks_the_newest_account(monkeypatch):
    """Old behaviour touched exactly one mailbox -- the newest. It must now touch both."""
    tag = _tag(); f = _accounts(tag)
    mod, calls = _stub_sync(monkeypatch)
    mod.sync_recent_mail(top=5)
    touched = {t for t in calls["tokens"] if t in (f"token-{f['a_id']}", f"token-{f['b_id']}")}
    assert touched == {f"token-{f['a_id']}", f"token-{f['b_id']}"}


def test_sync_accepts_an_explicit_account(monkeypatch):
    tag = _tag(); f = _accounts(tag)
    mod, calls = _stub_sync(monkeypatch)
    mod.sync_recent_mail(top=5, account_id=f["a_id"])
    assert calls["tokens"] == [f"token-{f['a_id']}"]


def test_sync_named_account_that_does_not_exist_fails_closed(monkeypatch):
    tag = _tag(); _accounts(tag)
    mod, calls = _stub_sync(monkeypatch)
    with pytest.raises(RuntimeError, match="No Microsoft 365 account is connected"):
        mod.sync_recent_mail(top=5, account_id=-1)
    assert calls["tokens"] == []                      # never degraded to some other mailbox


def test_sync_one_bad_account_does_not_starve_the_others(monkeypatch):
    tag = _tag(); f = _accounts(tag)
    mod, calls = _stub_sync(monkeypatch, failing_account_ids={f["a_id"]})
    result = mod.sync_recent_mail(top=5)
    assert f"token-{f['b_id']}" in calls["tokens"]
    assert (f["a_id"], "error") in calls["health"]
    assert result["accounts_synced"] >= 1


def test_sync_uses_each_accounts_own_token(monkeypatch):
    tag = _tag(); f = _accounts(tag)
    mod, calls = _stub_sync(monkeypatch, per_token_messages={})
    mod.sync_recent_mail(top=5, account_id=f["b_id"])
    assert calls["tokens"] == [f"token-{f['b_id']}"]
    assert calls["health"] == [(f["b_id"], "ok")]


# --------------------------------------------------------------- send path shares the resolver
def test_mail_send_resolves_through_the_same_helper():
    tag = _tag(); f = _accounts(tag)
    from app.services.communications.mail_send import _staff_microsoft_account
    assert _staff_microsoft_account(_principal(f["a_email"]))["id"] == f["a_id"]
    assert _staff_microsoft_account(_principal(f["a_email"].upper()))["id"] == f["a_id"]


def test_mail_send_refuses_when_the_principal_has_no_account():
    tag = _tag(); _accounts(tag)
    from app.services.communications.mail_send import (
        MailSendError,
        _staff_microsoft_account,
    )
    with pytest.raises(MailSendError):
        _staff_microsoft_account(_principal(f"nobody.{tag}@firm.test"))
