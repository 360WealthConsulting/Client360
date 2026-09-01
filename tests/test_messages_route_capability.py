"""Secure client MESSAGES is gated by client.read, not by identity.manage.

`/admin/client-portal/threads` is staff client service that happens to live under `/admin`. The generic
`^/admin` -> `identity.manage` middleware rule used to swallow it, so only the Administrator role could
open a surface whose own six handlers ask for `client.read` (GET) and `client.write` (POST). A Client
Service or Advisor employee - the people whose job IS answering clients - got a 403 on a link the
sidebar advertised to them.

A narrow carve-out on the `/threads` subtree fixes that. These tests pin all three halves of the
contract, because each could regress on its own:

  1. authorized staff (client.read, no identity.manage) REACH Messages;
  2. staff without client.read are DENIED;
  3. every other `/admin/*` route - including the rest of `/admin/client-portal` - still demands
     identity.manage, so the carve-out never became a general `/admin` exemption.

Runtime behaviour is driven through the real ``AuthenticationMiddleware.dispatch`` with a mocked
``call_next``, the same harness ``test_portal_authz_fail_closed`` uses: reaching call_next (200) means
the middleware AUTHORIZED the request through to the route's own ``require_capability`` check.

Record scope is a SEPARATE layer and is not weakened here - ``communication_hub.thread_in_staff_scope``
runs per row and still decides which threads a given employee can see. ``test_communication_hub`` and
``test_portal_message_isolation`` cover that; the last test here pins that the two layers are distinct.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.security.middleware import RULES, AuthenticationMiddleware

_APP = FastAPI()


def _principal(*capabilities):
    caps = set(capabilities)
    return SimpleNamespace(user_id=42, email="staff@example-demo.example",
                           capabilities=sorted(caps), can=lambda code: code in caps)


def _status(monkeypatch, method, path, principal):
    """Status from the REAL middleware. 200 == authorized through to the route."""
    monkeypatch.setattr("app.security.middleware.resolve_principal", lambda token: principal)
    monkeypatch.setattr("app.security.middleware.write_audit_event", lambda **kw: None)

    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": [(b"accept", b"application/json")],
             "session": {"session_token": "t"}, "app": _APP, "client": ("127.0.0.1", 1234),
             "server": ("testserver", 80), "scheme": "http"}

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _call_next(request):
        return JSONResponse({"ok": True})

    mw = AuthenticationMiddleware(_APP)
    return asyncio.run(mw.dispatch(Request(scope, _receive), _call_next)).status_code


def _required(path, method="GET"):
    """The capability the RULES map resolves for a path, including the .read->.write inference."""
    cap = next((code for pattern, code in RULES if pattern.search(path)), None)
    if method not in {"GET", "HEAD", "OPTIONS"} and cap:
        cap = cap.replace(".read", ".write")
    return cap


# --- 1. authorized staff can enter Messages ----------------------------------

THREAD_READS = ("/admin/client-portal/threads", "/admin/client-portal/threads/7")
THREAD_WRITES = ("/admin/client-portal/threads/new", "/admin/client-portal/threads/7/reply",
                 "/admin/client-portal/threads/7/assign", "/admin/client-portal/threads/7/resolve",
                 "/admin/client-portal/threads/7/link-request",
                 "/admin/client-portal/threads/7/create-request")


@pytest.mark.parametrize("path", THREAD_READS)
def test_client_read_staff_can_open_messages_without_identity_manage(monkeypatch, path):
    """The regression this whole patch exists for: a Client Service employee reaches Messages."""
    assert _status(monkeypatch, "GET", path, _principal("client.read")) == 200


@pytest.mark.parametrize("path", THREAD_WRITES)
def test_client_write_staff_can_act_on_a_thread_without_identity_manage(monkeypatch, path):
    assert _status(monkeypatch, "POST", path, _principal("client.read", "client.write")) == 200


def test_the_administrator_can_still_open_messages(monkeypatch):
    """Broadening access must not cost the role that already had it."""
    admin = _principal("client.read", "client.write", "identity.manage", "record.read_all")
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads", admin) == 200


# --- 2. staff without client.read are denied ---------------------------------

@pytest.mark.parametrize("path", THREAD_READS)
def test_staff_without_client_read_cannot_open_messages(monkeypatch, path):
    assert _status(monkeypatch, "GET", path, _principal("work.read", "task.read")) == 403


def test_identity_manage_alone_does_not_open_messages(monkeypatch):
    """The gate is genuinely client.read now - not "client.read OR the old admin capability"."""
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads",
                   _principal("identity.manage")) == 403


def test_read_only_staff_cannot_reply(monkeypatch):
    """The .read->.write inference still separates viewing a conversation from writing into it."""
    reader = _principal("client.read")
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads/7", reader) == 200
    assert _status(monkeypatch, "POST", "/admin/client-portal/threads/7/reply", reader) == 403


def test_an_unauthenticated_request_never_reaches_messages(monkeypatch):
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads", None) != 200


# --- 3. the carve-out did NOT become a general /admin exemption --------------

# Every one of these must still resolve to identity.manage. The first four are the REST of
# /admin/client-portal, which is where an over-broad carve-out would leak first.
STILL_ADMIN_ONLY = (
    "/admin/client-portal",
    "/admin/client-portal/accounts",
    "/admin/client-portal/invite",
    "/admin/client-portal/create-client",
    "/admin/client-portal/diagnostics",
    "/admin/review",
    "/admin/documents/unassigned",
    "/admin/employees",
    "/admin/invitations",
    "/admin/access-profiles",
)


@pytest.mark.parametrize("path", STILL_ADMIN_ONLY)
def test_other_admin_routes_still_require_identity_manage(monkeypatch, path):
    assert _required(path) == "identity.manage", path
    assert _status(monkeypatch, "GET", path, _principal("client.read", "client.write")) == 403
    assert _status(monkeypatch, "GET", path, _principal("identity.manage")) == 200


def test_admin_review_was_deliberately_left_alone(monkeypatch):
    """Scoped out of this patch on purpose: /admin/review keeps the generic rule until it gets
    its own change. Pinned so nobody "tidies" it in by accident."""
    assert _required("/admin/review") == "identity.manage"


@pytest.mark.parametrize("path,cap", [
    ("/admin/audit", "audit.read"),
    ("/admin/rule-catalog", "audit.read"),
    ("/admin/roles", "role.manage"),
    ("/admin/team-memberships", "team.manage"),
    ("/admin/assignments", "assignment.manage"),
])
def test_the_pre_existing_admin_carve_outs_are_undisturbed(path, cap):
    """The new rule was inserted among these; ordering decides all of them."""
    assert _required(path) == cap


def test_the_carve_out_is_scoped_to_the_threads_subtree():
    """A prefix one segment shorter would hand the whole client-portal admin surface to client.read."""
    assert _required("/admin/client-portal/threads") == "client.read"
    assert _required("/admin/client-portal/threads", "POST") == "client.write"
    assert _required("/admin/client-portal") == "identity.manage"
    assert _required("/admin") == "identity.manage"


def test_capability_and_record_scope_are_separate_layers():
    """Passing the capability gate is not access to a thread: staff_inbox filters every row through
    record scope, so the carve-out cannot widen WHICH conversations anyone sees."""
    import inspect

    from app.portal import communication_hub as hub
    assert "thread_in_staff_scope" in inspect.getsource(hub.staff_inbox)


# --- navigation visibility ---------------------------------------------------

def test_the_sidebar_already_offers_messages_on_client_read():
    """A nav item gated more tightly than its route hides the surface from the very staff this
    patch enables; gated more loosely it is shown-then-403. base.html has always gated Messages
    on client.read - which this carve-out makes correct for the first time, so the sidebar needs
    no change and must not drift to something narrower."""
    import pathlib
    src = pathlib.Path("app/templates/base.html").read_text(encoding="utf-8")
    assert "{% set can_messages = 'client.read' in caps %}" in src
    messages_item = next(line for line in src.splitlines()
                         if '"href": "/admin/client-portal/threads"' in line)
    assert '"show": can_messages' in messages_item
