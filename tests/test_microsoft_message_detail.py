"""READ-ONLY message-detail preview: mailbox isolation, matching, attachments, and no writes.

Every test drives the real route with Graph stubbed. The stub keys its responses by BEARER TOKEN, so
"which mailbox did this read" is asserted on the credential actually presented -- a route that
picked the wrong account would fail here rather than quietly render someone else's mail.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import engine, microsoft_accounts, people
from app.routes.microsoft365_mail import (
    ATTACHMENT_SELECT,
    MESSAGE_SELECT,
    microsoft365_mail_detail,
)
from app.security.models import Principal
from tests._portal_util import fake_request, render

_CAP = frozenset({"communication.read", "communications.view", "client.read"})
_TAGS: list[str] = []

FWD_BODY = """<html><body>
<p>Michael - see below, new enquiry. Can you take it?</p><p>Lauren</p>
<div><hr><b>From:</b> Jane Prospect &lt;jane.{tag}@example.com&gt;<br>
<b>Sent:</b> Monday, August 24, 2026 9:14 AM<br>
<b>To:</b> Lauren Ross &lt;lauren.{tag}@firm.test&gt;<br>
<b>Subject:</b> Tax liability on a property sale<br></div>
<p>Best,<br>Jane Prospect<br>(415) 555-0134</p></body></html>"""


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        with engine.begin() as c:
            c.execute(delete(microsoft_accounts).where(microsoft_accounts.c.tenant_id == tag))
            c.execute(delete(people).where(people.c.last_name.like(f"%{tag}%")))
    _TAGS.clear()


def _tag():
    t = "msg" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _mailboxes(tag):
    now = datetime.now(UTC)
    with engine.begin() as c:
        for who, delta in (("a", timedelta(hours=2)), ("b", timedelta(0))):
            c.execute(insert(microsoft_accounts).values(
                tenant_id=tag, user_id=f"entra-{who}-{tag}", email=f"{who}.{tag}@firm.test",
                access_token=f"token-{who}-{tag}", expires_at=now + timedelta(hours=1),
                updated_at=now - delta))
    return {"a_email": f"a.{tag}@firm.test", "b_email": f"b.{tag}@firm.test"}


def _principal(email, caps=_CAP, uid=1):
    return Principal(uid, email, "Staff", frozenset(caps))


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload or {}

    def json(self):
        return self._payload


def _message(tag, *, body=None, attachments_flag=False, subject="FW: Tax liability"):
    return {
        "id": f"AAMk{tag}", "subject": subject,
        "from": {"emailAddress": {"name": "Lauren Ross", "address": f"lauren.{tag}@firm.test"}},
        "sender": {"emailAddress": {"name": "Lauren Ross", "address": f"lauren.{tag}@firm.test"}},
        "toRecipients": [{"emailAddress": {"name": "Michael", "address": f"a.{tag}@firm.test"}}],
        "ccRecipients": [], "receivedDateTime": "2026-08-25T13:00:00Z",
        "body": {"contentType": "html", "content": body if body is not None
                 else FWD_BODY.format(tag=tag)},
        "bodyPreview": "Michael - see below", "hasAttachments": attachments_flag,
        "conversationId": f"conv-{tag}", "internetMessageId": f"<imid-{tag}@example.com>",
        "webLink": "https://outlook.test/msg",
    }


def _stub(monkeypatch, *, by_token, attachments_by_token=None):
    """Graph stub. Records every call so tests can assert URLs, params and tokens."""
    import app.routes.microsoft365_mail as mod
    calls = []

    def _get(url, headers=None, params=None, timeout=None):
        token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        calls.append({"url": url, "params": params or {}, "token": token})
        if url.endswith("/attachments"):
            payload = (attachments_by_token or {}).get(token)
            return _Resp(200, payload or {"value": []})
        entry = by_token.get(token)
        if entry is None:
            return _Resp(404)
        return _Resp(200, entry)

    monkeypatch.setattr(mod.requests, "get", _get)
    return calls


def _open(principal, message_id, *, tag="x"):
    return microsoft365_mail_detail(
        fake_request(f"/microsoft365/mail/{message_id}", state_principal=principal),
        message_id=message_id, principal=principal)


# ------------------------------------------------------------------ mailbox isolation
def test_user_a_reads_with_user_a_token(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    calls = _stub(monkeypatch, by_token={f"token-a-{tag}": _message(tag)})
    html = render(_open(_principal(f["a_email"]), f"AAMk{tag}"))
    assert calls[0]["token"] == f"token-a-{tag}"
    assert "Tax liability" in html


def test_user_b_reads_with_user_b_token(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    calls = _stub(monkeypatch, by_token={f"token-b-{tag}": _message(tag)})
    _open(_principal(f["b_email"], uid=2), f"AAMk{tag}")
    assert calls[0]["token"] == f"token-b-{tag}"


def test_a_cannot_fetch_a_message_that_only_exists_under_bs_token(monkeypatch):
    """I. The message id is real, but it lives in B's mailbox. /me is resolved by the token, so A
    gets the ordinary non-disclosing not-found -- never B's message."""
    tag = _tag(); f = _mailboxes(tag)
    calls = _stub(monkeypatch, by_token={f"token-b-{tag}": _message(tag)})
    resp = _open(_principal(f["a_email"]), f"AAMk{tag}")
    assert calls[0]["token"] == f"token-a-{tag}"
    assert resp.status_code == 404
    body = render(resp)
    assert "Tax liability" not in body
    assert f"lauren.{tag}" not in body


def test_unknown_message_and_out_of_mailbox_message_are_indistinguishable(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    _stub(monkeypatch, by_token={f"token-b-{tag}": _message(tag)})
    a = _open(_principal(f["a_email"]), f"AAMk{tag}")            # exists, but in B's mailbox
    b = _open(_principal(f["a_email"]), "totally-made-up-id")     # does not exist at all
    assert a.status_code == b.status_code == 404
    # Same user-visible wording either way (the pages differ only by their per-request id), so the
    # response cannot be used to probe whether a message exists in someone else's mailbox.
    assert "Message not found." in render(a) and "Message not found." in render(b)
    assert "Tax liability" not in render(a) and "Tax liability" not in render(b)


def test_no_connected_account_fails_closed_without_calling_graph(monkeypatch):
    tag = _tag(); _mailboxes(tag)
    calls = _stub(monkeypatch, by_token={})
    resp = _open(_principal(f"nobody.{tag}@firm.test"), f"AAMk{tag}")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/microsoft365/connect"
    assert calls == []


def test_route_requires_communication_read():
    from app.security.dependencies import CAPABILITY_DEP_ATTR
    from app.security.middleware import RULES
    dep = next(d.dependency for d in microsoft365_mail_detail.__defaults__
               if getattr(d, "dependency", None) is not None)
    assert getattr(dep, CAPABILITY_DEP_ATTR) == ("communication.read",)
    assert next(code for pattern, code in RULES
                if pattern.search("/microsoft365/mail/AAMk123")) == "communication.read"


# ------------------------------------------------------------------ graph usage
def test_only_read_endpoints_and_no_content_bytes(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    calls = _stub(monkeypatch, by_token={f"token-a-{tag}": _message(tag, attachments_flag=True)},
                  attachments_by_token={f"token-a-{tag}": {"value": [
                      {"@odata.type": "#microsoft.graph.fileAttachment", "id": "att1",
                       "name": "2025 tax return.pdf", "contentType": "application/pdf",
                       "size": 12345, "isInline": False}]}})
    _open(_principal(f["a_email"]), f"AAMk{tag}")
    assert [c["url"] for c in calls] == [
        f"https://graph.microsoft.com/v1.0/me/messages/AAMk{tag}",
        f"https://graph.microsoft.com/v1.0/me/messages/AAMk{tag}/attachments",
    ]
    joined = " ".join(str(c["params"]) for c in calls)
    assert "contentBytes" not in joined
    assert "contentBytes" not in MESSAGE_SELECT and "contentBytes" not in ATTACHMENT_SELECT
    for field in ("conversationId", "internetMessageId", "body", "toRecipients"):
        assert field in MESSAGE_SELECT


def test_graph_is_never_called_with_a_write_method(monkeypatch):
    """The module must expose no post/patch/delete usage at all."""
    import inspect

    import app.routes.microsoft365_mail as mod
    src = inspect.getsource(mod)
    for verb in ("requests.post", "requests.patch", "requests.delete", "requests.put",
                 "sendMail"):
        assert verb not in src
    # contentBytes appears only in the comment explaining its absence, never in a $select.
    assert "contentBytes" not in MESSAGE_SELECT + ATTACHMENT_SELECT


def test_message_id_is_percent_encoded_into_one_segment(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    weird = "AA/Mk+id=="
    calls = _stub(monkeypatch, by_token={})
    _open(_principal(f["a_email"]), weird)
    assert calls[0]["url"].endswith("/me/messages/AA%2FMk%2Bid%3D%3D")


def test_attachments_are_not_fetched_when_the_message_has_none(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    calls = _stub(monkeypatch, by_token={f"token-a-{tag}": _message(tag, attachments_flag=False)})
    _open(_principal(f["a_email"]), f"AAMk{tag}")
    assert not any(c["url"].endswith("/attachments") for c in calls)


# ------------------------------------------------------------------ preview content
def test_forwarder_is_shown_as_forwarder_and_prospect_comes_from_the_body(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    _stub(monkeypatch, by_token={f"token-a-{tag}": _message(tag)})
    html = render(_open(_principal(f["a_email"]), f"AAMk{tag}"))
    assert "Lauren Ross" in html
    assert f"jane.{tag}@example.com" in html
    assert "Jane Prospect" in html
    assert "Staff confirmation required" in html


def test_non_forwarded_message_makes_no_prospect_attribution(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    _stub(monkeypatch, by_token={f"token-a-{tag}": _message(
        tag, body="<p>Are we on for Thursday?</p>", subject="Thursday")})
    html = render(_open(_principal(f["a_email"]), f"AAMk{tag}"))
    assert "does not look forwarded" in html
    assert f"lauren.{tag}@firm.test" not in html.split("Detected prospect candidate")[1]


def test_g_file_attachment_metadata_is_listed(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    _stub(monkeypatch, by_token={f"token-a-{tag}": _message(tag, attachments_flag=True)},
          attachments_by_token={f"token-a-{tag}": {"value": [
              {"@odata.type": "#microsoft.graph.fileAttachment", "id": "att1",
               "name": "2025 tax return.pdf", "contentType": "application/pdf",
               "size": 204800, "isInline": False}]}})
    html = render(_open(_principal(f["a_email"]), f"AAMk{tag}"))
    assert "2025 tax return.pdf" in html and "application/pdf" in html and "204800" in html


def test_h_item_attachment_is_flagged_but_not_parsed(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    _stub(monkeypatch, by_token={f"token-a-{tag}": _message(tag, attachments_flag=True)},
          attachments_by_token={f"token-a-{tag}": {"value": [
              {"@odata.type": "#microsoft.graph.itemAttachment", "id": "item1",
               "name": "Original message.eml", "contentType": "message/rfc822",
               "size": 8000, "isInline": False}]}})
    html = render(_open(_principal(f["a_email"]), f"AAMk{tag}"))
    assert "attached email" in html
    assert "Original message.eml" in html


def test_the_page_offers_no_mutation(monkeypatch):
    tag = _tag(); f = _mailboxes(tag)
    _stub(monkeypatch, by_token={f"token-a-{tag}": _message(tag)})
    html = render(_open(_principal(f["a_email"]), f"AAMk{tag}"))
    # The only <form> on the page is the site-wide GET search in base.html; the preview itself
    # posts nothing anywhere.
    assert 'method="post"' not in html.lower()
    assert "coming next" in html
    assert "disabled" in html


# ------------------------------------------------------------------ read-only guarantee
def test_previewing_writes_nothing_to_the_database(monkeypatch):
    from app.db import audit_events, timeline_events
    tag = _tag(); f = _mailboxes(tag)
    _stub(monkeypatch, by_token={f"token-a-{tag}": _message(tag, attachments_flag=True)},
          attachments_by_token={f"token-a-{tag}": {"value": [
              {"@odata.type": "#microsoft.graph.fileAttachment", "id": "a", "name": "x.pdf",
               "contentType": "application/pdf", "size": 1, "isInline": False}]}})

    def _counts():
        with engine.connect() as c:
            return {t.name: c.scalar(select(func.count()).select_from(t))
                    for t in (people, timeline_events, audit_events, microsoft_accounts)}

    before = _counts()
    _open(_principal(f["a_email"]), f"AAMk{tag}")
    _open(_principal(f["a_email"]), f"AAMk{tag}")
    assert _counts() == before
