"""Secure client MESSAGES is gated by DEDICATED capabilities — not identity.manage, not client.read.

`/admin/client-portal/threads` is staff client SERVICE that happens to live under `/admin`. The generic
`^/admin` -> `identity.manage` middleware rule swallowed it, so only the Administrator could open a
surface the sidebar advertised to Client Service and Advisor employees — the people whose job IS
answering clients.

The fix is a narrow carve-out on the `/threads` subtree gated on `communications.message.read`
(msgcap01), plus route dependencies that declare the SAME capabilities. The obvious alternative —
`client.read` — would also have opened Messages, but eleven roles hold it, including Accounting,
Payroll, Reviewer and Read Only. Reading a client's correspondence is a narrower authority than
reading their record.

Two layers, deliberately in agreement rather than stacked as an accidental double-gate:

  * MIDDLEWARE resolves the URL prefix to `communications.message.read`, and the `.read`->`.write`
    inference turns every mutation under it into `communications.message.write`.
  * ROUTE DEPENDENCIES declare the same pair explicitly, so the declared gate IS the real gate.

Messages authority is self-contained: a principal holding ONLY the two message capabilities can use
Messages, and a principal holding client.read/client.write cannot. That is the whole point — Messages
capability grants authority over the Messages FEATURE and nothing else, while record scope
(`communication_hub.thread_in_staff_scope`, a separate layer) still decides WHICH threads that
authorized person may see.

Runtime behaviour is driven through the real ``AuthenticationMiddleware.dispatch`` with a mocked
``call_next``, the same harness ``test_portal_authz_fail_closed`` uses: reaching call_next (200) means
the middleware AUTHORIZED the request through to the route's own ``require_capability`` check.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.security.dependencies import CAPABILITY_DEP_ATTR
from app.security.middleware import RULES, AuthenticationMiddleware

_APP = FastAPI()

READ_CAP = "communications.message.read"
WRITE_CAP = "communications.message.write"


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


THREAD_READS = ("/admin/client-portal/threads", "/admin/client-portal/threads/7")
THREAD_WRITES = ("/admin/client-portal/threads/new", "/admin/client-portal/threads/7/reply",
                 "/admin/client-portal/threads/7/assign", "/admin/client-portal/threads/7/resolve",
                 "/admin/client-portal/threads/7/link-request",
                 "/admin/client-portal/threads/7/create-request")

# A viewer/replier holds ONLY the Messages capabilities. No client.read, no client.write, no
# identity.manage — proving Messages authority stands on its own.
VIEWER = (READ_CAP,)
REPLIER = (READ_CAP, WRITE_CAP)


# --- 1. holders of the message capability reach Messages ---------------------

@pytest.mark.parametrize("path", THREAD_READS)
def test_message_reader_can_open_messages_without_identity_manage(monkeypatch, path):
    """The regression this whole repair exists for: a Client Service employee reaches Messages."""
    assert _status(monkeypatch, "GET", path, _principal(*VIEWER)) == 200


@pytest.mark.parametrize("path", THREAD_WRITES)
def test_message_writer_can_act_on_a_thread_without_identity_manage(monkeypatch, path):
    assert _status(monkeypatch, "POST", path, _principal(*REPLIER)) == 200


def test_messages_needs_no_client_read_at_all(monkeypatch):
    """Messages authority is SELF-CONTAINED. The capabilities are not layered on top of client.read:
    a principal holding only the message pair works for both reading and replying. If this regresses,
    the double-gate is back and the declared capability is no longer the real one."""
    only_messages = _principal(READ_CAP, WRITE_CAP)
    assert "client.read" not in only_messages.capabilities
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads", only_messages) == 200
    assert _status(monkeypatch, "POST", "/admin/client-portal/threads/7/reply", only_messages) == 200


def test_the_administrator_can_still_open_messages(monkeypatch):
    """Changing the gate must not cost the role that already had access."""
    admin = _principal(*REPLIER, "identity.manage", "record.read_all")
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads", admin) == 200


# --- 2. everyone else is denied ----------------------------------------------

@pytest.mark.parametrize("path", THREAD_READS)
def test_staff_without_the_message_capability_cannot_open_messages(monkeypatch, path):
    assert _status(monkeypatch, "GET", path, _principal("work.read", "task.read")) == 403


def test_client_read_alone_does_not_open_messages(monkeypatch):
    """The point of msgcap01. client.read is held by eleven roles — Accounting, Payroll, Reviewer and
    Read Only among them. If this ever passes, the permission surface has silently widened back."""
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads",
                   _principal("client.read")) == 403
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads",
                   _principal("client.read", "client.write")) == 403


def test_client_write_does_not_permit_replying(monkeypatch):
    """Nor does the old write capability reach the reply endpoint."""
    assert _status(monkeypatch, "POST", "/admin/client-portal/threads/7/reply",
                   _principal("client.read", "client.write")) == 403


def test_identity_manage_alone_does_not_open_messages(monkeypatch):
    """The gate is genuinely the dedicated capability — not "it OR the old admin capability"."""
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads",
                   _principal("identity.manage")) == 403


# --- 3. viewing and replying are SEPARATELY gated ----------------------------

def test_a_viewer_cannot_reply(monkeypatch):
    """Read does not imply write. Tax Staff reads a conversation for context and cannot answer."""
    viewer = _principal(*VIEWER)
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads/7", viewer) == 200
    for path in THREAD_WRITES:
        assert _status(monkeypatch, "POST", path, viewer) == 403, path


def test_the_write_capability_alone_does_not_bypass_the_read_gate(monkeypatch):
    """A malformed grant (write without read) must not open the door: effective write can never
    exist without read, because the GET gate is the read capability."""
    write_only = _principal(WRITE_CAP)
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads", write_only) == 403


def test_middleware_read_authority_cannot_substitute_for_route_write_authority():
    """The middleware resolves mutations to the WRITE capability, and each POST handler declares the
    same. Middleware read authority can never stand in for a handler's write requirement."""
    for path in THREAD_WRITES:
        assert _required(path, "POST") == WRITE_CAP, path
    for path in THREAD_READS:
        assert _required(path, "GET") == READ_CAP, path


def test_unauthenticated_request_never_reaches_messages(monkeypatch):
    assert _status(monkeypatch, "GET", "/admin/client-portal/threads", None) != 200


# --- 4. middleware and route dependencies AGREE ------------------------------

def _declared_capabilities(path, method):
    """The capabilities the route itself declares via require_capability."""
    from app.main import app
    for route in app.routes:
        if getattr(route, "path", None) == path and method in (getattr(route, "methods", None) or ()):
            for dep in route.dependant.dependencies:
                codes = getattr(dep.call, CAPABILITY_DEP_ATTR, None)
                if codes:
                    return tuple(codes)
            return ()
    raise AssertionError(f"route not found: {method} {path}")


THREAD_ROUTES = (
    ("/admin/client-portal/threads", "GET", READ_CAP),
    ("/admin/client-portal/threads/{thread_id}", "GET", READ_CAP),
    ("/admin/client-portal/threads/new", "POST", WRITE_CAP),
    ("/admin/client-portal/threads/{thread_id}/reply", "POST", WRITE_CAP),
    ("/admin/client-portal/threads/{thread_id}/assign", "POST", WRITE_CAP),
    ("/admin/client-portal/threads/{thread_id}/resolve", "POST", WRITE_CAP),
    ("/admin/client-portal/threads/{thread_id}/link-request", "POST", WRITE_CAP),
    ("/admin/client-portal/threads/{thread_id}/create-request", "POST", WRITE_CAP),
)


@pytest.mark.parametrize("path,method,cap", THREAD_ROUTES)
def test_every_threads_handler_declares_the_messages_capability(path, method, cap):
    """All eight handlers — not the six the original repair described. The declared capability is
    the real one, so no handler can quietly fall back to client.read/client.write."""
    assert _declared_capabilities(path, method) == (cap,)


@pytest.mark.parametrize("path,method,cap", THREAD_ROUTES)
def test_route_dependency_and_middleware_resolve_to_the_same_capability(path, method, cap):
    """Agreement, not coincidence: the two layers must name the SAME capability, so direct URL access
    and the prefix rule can never diverge."""
    concrete = path.replace("{thread_id}", "7")
    assert _required(concrete, method) == cap
    assert _declared_capabilities(path, method) == (cap,)


def test_no_threads_handler_still_requires_client_read_or_write():
    """A holdover client.read/client.write on any /threads handler would resurrect the double-gate."""
    for path, method, _ in THREAD_ROUTES:
        declared = _declared_capabilities(path, method)
        assert "client.read" not in declared and "client.write" not in declared, (path, declared)


# --- 5. the carve-out did NOT become a general /admin exemption --------------

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
    assert _status(monkeypatch, "GET", path, _principal(READ_CAP, WRITE_CAP)) == 403
    assert _status(monkeypatch, "GET", path, _principal("identity.manage")) == 200


def test_diagnostics_is_not_part_of_the_messages_carve_out(monkeypatch):
    """/admin/client-portal/diagnostics is NOT under /threads (the original repair's notes claimed a
    "/threads/diagnostics" path that does not exist). It keeps the generic ^/admin -> identity.manage
    rule AND its own observability.audit dependency; the Messages capabilities do not participate."""
    assert _required("/admin/client-portal/diagnostics") == "identity.manage"
    assert _declared_capabilities("/admin/client-portal/diagnostics", "GET") == ("observability.audit",)
    assert _status(monkeypatch, "GET", "/admin/client-portal/diagnostics",
                   _principal(READ_CAP, WRITE_CAP)) == 403


def test_admin_review_was_deliberately_left_alone():
    """Scoped out of this repair on purpose: /admin/review keeps the generic rule until it gets its
    own change. Pinned so nobody "tidies" it in by accident."""
    assert _required("/admin/review") == "identity.manage"


@pytest.mark.parametrize("path,cap", [
    ("/admin/audit", "audit.read"),
    ("/admin/rule-catalog", "audit.read"),
    ("/admin/roles", "role.manage"),
    ("/admin/team-memberships", "team.manage"),
    ("/admin/assignments", "assignment.manage"),
])
def test_the_pre_existing_admin_carve_outs_are_unchanged(path, cap):
    """The new rule sits among five that already preceded the generic ^/admin rule; ordering matters."""
    assert _required(path) == cap


def test_unrelated_communications_capabilities_are_untouched():
    """This repair adds a capability; it must not repurpose the existing communications family."""
    assert _required("/microsoft/mail") == "communication.read"
    assert _required("/mail") == "communication.read"
