"""The Messages thread HANDLERS require the same capability the middleware rule already enforces.

`tests/test_messages_route_capability.py` proves the middleware (the door) honours
`communications.message.read` / `.write` on the `/admin/client-portal/threads` subtree.
`tests/test_messages_access_matrix.py` proves the right ROLES hold those capabilities. This file
pins the third half of the contract, which was the one out of step: what the route's own
`require_capability` asks for.

THE DEFECT THIS CLOSES. The middleware carve-out (msgcap01) gated the `/threads` subtree on
`communications.message.read`, but all eight thread handlers still declared `client.read` /
`client.write`. `require_capability` runs ON TOP of the middleware rule, never instead of it, so
both families were required at once. It happened to work — every role msgcap01 grants also holds
the matching `client.*` — but the authority model stated two different things, and the stated
intent of msgcap01 ("eleven roles hold client.read, including Accounting, Payroll, Reviewer and
Read Only, who have no business reading a client's correspondence") was not what the handler
actually enforced. Narrowing `client.read` later would have silently broken Messages.

WHAT IS PINNED HERE:

  1. every thread READ handler declares exactly `communications.message.read`;
  2. every thread WRITE handler declares exactly `communications.message.write`;
  3. the dependency ADMITS a holder of the message capability that holds no `client.*` at all;
  4. the dependency REFUSES a `client.read`/`client.write` holder that lacks the message
     capability - the exact over-broad model this replaced;
  5. middleware and handler now resolve to the SAME capability for every thread route;
  6. nothing else moved: the rest of `/admin/client-portal` and `/admin/review` are unchanged.

Capability codes are read from the real FastAPI dependants (`require_capability` tags each
dependency with its codes), so a handler whose declaration is edited is caught here rather than
by a string search. Record scope is a SEPARATE layer and is untouched -
`communication_hub.thread_in_staff_scope` still decides WHICH threads a holder can see; these
capabilities gate the door, not the contents.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routes.portal_admin import router as portal_admin_router
from app.security.dependencies import CAPABILITY_DEP_ATTR, require_capability
from app.security.middleware import RULES
from app.security.models import Principal

# --- the eight thread handlers, by method + path ------------------------------

THREAD_READS = (
    ("GET", "/admin/client-portal/threads"),
    ("GET", "/admin/client-portal/threads/{thread_id}"),
)
THREAD_WRITES = (
    ("POST", "/admin/client-portal/threads/new"),
    ("POST", "/admin/client-portal/threads/{thread_id}/reply"),
    ("POST", "/admin/client-portal/threads/{thread_id}/assign"),
    ("POST", "/admin/client-portal/threads/{thread_id}/resolve"),
    ("POST", "/admin/client-portal/threads/{thread_id}/link-request"),
    ("POST", "/admin/client-portal/threads/{thread_id}/create-request"),
)

READ_CAP = "communications.message.read"
WRITE_CAP = "communications.message.write"


def _routes():
    return {(method, route.path): route
            for route in portal_admin_router.routes
            for method in getattr(route, "methods", ()) or ()}


def _declared(method, path):
    """Capability codes the route's own dependencies enforce, read from the real dependant tree."""
    route = _routes().get((method, path))
    assert route is not None, f"no route {method} {path} on the portal-admin router"
    codes: set[str] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        codes.update(getattr(dep.call, CAPABILITY_DEP_ATTR, ()))
        stack.extend(dep.dependencies)
    return codes


def _dependency(method, path):
    """The single require_capability dependency function guarding this route."""
    route = _routes()[(method, path)]
    for dep in route.dependant.dependencies:
        if hasattr(dep.call, CAPABILITY_DEP_ATTR):
            return dep.call
    raise AssertionError(f"{method} {path} declares no require_capability dependency")


def _principal(*capabilities):
    return Principal(42, "staff@example-demo.example", "Staff", frozenset(capabilities))


def _middleware_capability(path, method="GET"):
    """What the RULES map resolves for a path, including the .read->.write inference."""
    cap = next((code for pattern, code in RULES if pattern.search(path)), None)
    if method not in {"GET", "HEAD", "OPTIONS"} and cap:
        cap = cap.replace(".read", ".write")
    return cap


# --- 1 + 2. the handlers declare the message capabilities --------------------

@pytest.mark.parametrize("method,path", THREAD_READS)
def test_thread_read_handlers_declare_the_message_read_capability(method, path):
    assert _declared(method, path) == {READ_CAP}, path


@pytest.mark.parametrize("method,path", THREAD_WRITES)
def test_thread_write_handlers_declare_the_message_write_capability(method, path):
    assert _declared(method, path) == {WRITE_CAP}, path


def test_no_thread_handler_still_declares_a_client_capability():
    """The double-gate regression test: if either family comes back, both are required again."""
    for method, path in THREAD_READS + THREAD_WRITES:
        declared = _declared(method, path)
        assert "client.read" not in declared, path
        assert "client.write" not in declared, path


# --- 3. a message-capability holder is admitted WITHOUT client.* --------------

@pytest.mark.parametrize("method,path", THREAD_READS)
def test_a_message_reader_is_admitted_without_client_read(method, path):
    """The point of the change: reading correspondence no longer also requires reading the record."""
    reader = _principal(READ_CAP)
    assert _dependency(method, path)(principal=reader) is reader


@pytest.mark.parametrize("method,path", THREAD_WRITES)
def test_a_message_writer_is_admitted_without_client_write(method, path):
    writer = _principal(READ_CAP, WRITE_CAP)
    assert _dependency(method, path)(principal=writer) is writer


# --- 4. holders of the OLD capabilities are refused ---------------------------

@pytest.mark.parametrize("method,path", THREAD_READS)
def test_client_capabilities_alone_are_refused_on_read_routes(method, path):
    """Accounting, Payroll, Reviewer and Read Only hold client.read. None of them may read a
    client's correspondence, which is the whole reason msgcap01 exists."""
    broad = _principal("client.read", "client.write", "record.read_all", "record.write_all")
    with pytest.raises(HTTPException) as excinfo:
        _dependency(method, path)(principal=broad)
    assert excinfo.value.status_code == 403


@pytest.mark.parametrize("method,path", THREAD_WRITES)
def test_client_capabilities_alone_are_refused_on_write_routes(method, path):
    broad = _principal("client.read", "client.write", "record.write_all")
    with pytest.raises(HTTPException) as excinfo:
        _dependency(method, path)(principal=broad)
    assert excinfo.value.status_code == 403


@pytest.mark.parametrize("method,path", THREAD_WRITES)
def test_a_view_only_principal_cannot_act_on_a_thread(method, path):
    """View and reply stay SEPARATELY gated: Tax Staff reads the conversation for context, but
    replying to the client is the coordinator's job."""
    viewer = _principal(READ_CAP)
    with pytest.raises(HTTPException) as excinfo:
        _dependency(method, path)(principal=viewer)
    assert excinfo.value.status_code == 403


def test_identity_manage_does_not_substitute_for_the_message_capability():
    """The handler gate is genuinely the dedicated capability, not "it OR the admin capability"."""
    admin_only = _principal("identity.manage", "record.read_all")
    with pytest.raises(HTTPException):
        _dependency("GET", "/admin/client-portal/threads")(principal=admin_only)


# --- 5. the two layers now agree ---------------------------------------------

@pytest.mark.parametrize("method,path", THREAD_READS + THREAD_WRITES)
def test_middleware_and_handler_require_the_same_capability(method, path):
    """The defect was that these two disagreed. A concrete path is used for the middleware side
    because RULES matches URLs, not FastAPI path templates."""
    concrete = path.replace("{thread_id}", "7")
    assert _declared(method, path) == {_middleware_capability(concrete, method)}, path


# --- 6. nothing else moved ----------------------------------------------------

UNCHANGED_PORTAL_ADMIN = (
    ("GET", "", {"client.read"}),
    ("GET", "/accounts", {"client.read"}),
    ("GET", "/client-search", {"client.read"}),
    ("POST", "/invite", {"client.write"}),
    ("POST", "/invite-form", {"client.write"}),
    ("POST", "/create-client", {"client.write"}),
    ("POST", "/accounts/{account_id}/revoke", {"client.write"}),
    ("GET", "/accounts/{account_id}/preview", {"client.read"}),
    ("GET", "/diagnostics", {"observability.audit"}),
)


@pytest.mark.parametrize("method,suffix,expected", UNCHANGED_PORTAL_ADMIN)
def test_the_rest_of_client_portal_admin_is_untouched(method, suffix, expected):
    """Invitations, account revocation, create-client and diagnostics are genuinely administrative
    and keep both their own capability and the generic ^/admin -> identity.manage middleware rule."""
    path = "/admin/client-portal" + suffix
    assert _declared(method, path) == expected, path
    # identity.manage has no ".read" to infer from, so it is the same for GET and POST alike:
    # the carve-out never became a general /admin exemption.
    assert _middleware_capability(path, method) == "identity.manage", path


def test_admin_review_authorization_is_unchanged():
    """Explicitly out of scope for this change. /admin/review keeps the generic middleware rule
    AND its handler's client.read; pinned so nobody "tidies" it in alongside Messages."""
    from app.routes.admin_review_inbox import review_inbox

    assert _middleware_capability("/admin/review") == "identity.manage"
    codes = getattr(
        next(d for d in review_inbox.__defaults__ if hasattr(d, "dependency")).dependency,
        CAPABILITY_DEP_ATTR, ())
    assert set(codes) == {"client.read"}


def test_require_capability_still_tags_its_dependencies():
    """The whole file reads capability codes off the dependency tag; if that contract changes,
    every assertion above would silently pass against an empty set."""
    assert getattr(require_capability("x.y"), CAPABILITY_DEP_ATTR) == ("x.y",)
