"""Who may reach Edit Profile, and from where.

The edit form is a staff mutation on a client record, so three separate gates stand in front of
it, all of them in ``AuthenticationMiddleware`` rather than in the handler:

  * **Capability.** ``/people`` maps to ``client.read``, and a mutating method turns that into
    ``client.write``. Read the page with one, save it only with the other.
  * **Record scope.** ``/people/{id}`` is a record path, so a principal without an assignment to
    this client — and without the firm-wide bypass — is refused before the form is parsed.
  * **Same origin.** Client360 has no CSRF token; state-changing requests are rejected when the
    Origin (or, failing that, the Referer) is not this site. A form posted from another page on
    another host does not save.

Driven through the real middleware with a mocked ``call_next``, the way
``test_staff_authz_fail_closed`` does: a 200 means the middleware AUTHORIZED the request and the
handler would run; a 403 means it stopped there.

NO REAL DATA — the fixtures are a synthetic person and a synthetic staff user.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db import engine, people, record_assignments
from app.security.middleware import RULES, AuthenticationMiddleware
from app.security.models import Principal
from tests._portal_util import seed_staff_user

BASE = "http://testserver"

WRITE = frozenset({"client.read", "client.write", "record.read_all", "record.write_all"})
READ_ONLY = frozenset({"client.read", "record.read_all"})
UNSCOPED_WRITE = frozenset({"client.read", "client.write", "record.read_all"})
#: ``record.read_all`` opens READS firm-wide; a WRITE still needs ``record.write_all`` or an
#: assignment to this very client. That gap is the point of the scope tests below.


@pytest.fixture(scope="module")
def app():
    stub = FastAPI()

    @stub.api_route("/people/{person_id}/edit", methods=["GET", "POST"])
    def _edit(person_id: int):
        return {"ok": True}

    return stub


@pytest.fixture(scope="module")
def person_id() -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(
            full_name=f"Authz Subject {uuid.uuid4().hex[:10]}", active=True)
            .returning(people.c.id)).scalar_one()


def _status(monkeypatch, app, caps, method, path, headers=()):
    principal = Principal(seed_staff_user(), "staff@example.test", "Staff", frozenset(caps))
    monkeypatch.setattr("app.security.middleware.resolve_principal", lambda token: principal)
    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"accept", b"application/json"), *headers],
        "session": {}, "app": app, "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80), "scheme": "http",
    }

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _call_next(request):
        return JSONResponse({"ok": True})     # reached only when the middleware authorized

    mw = AuthenticationMiddleware(app)
    return asyncio.run(mw.dispatch(Request(scope, _receive), _call_next)).status_code


# --- capability --------------------------------------------------------------------------------

def test_the_edit_path_is_covered_by_the_central_capability_map():
    """Not self-protected by accident: the RULES map is what gates this route."""
    assert any(pattern.search("/people/1/edit") for pattern, _ in RULES)
    capability = next(code for pattern, code in RULES if pattern.search("/people/1/edit"))
    assert capability == "client.read"
    assert capability.replace(".read", ".write") == "client.write"


def test_opening_the_form_needs_only_read(monkeypatch, app, person_id):
    assert _status(monkeypatch, app, READ_ONLY, "GET", f"/people/{person_id}/edit") == 200


def test_saving_without_client_write_is_denied(monkeypatch, app, person_id):
    assert _status(monkeypatch, app, READ_ONLY, "POST", f"/people/{person_id}/edit") == 403


def test_saving_with_client_write_is_authorized(monkeypatch, app, person_id):
    assert _status(monkeypatch, app, WRITE, "POST", f"/people/{person_id}/edit") == 200


# --- record scope ------------------------------------------------------------------------------

def test_a_principal_outside_this_record_cannot_save(monkeypatch, app, person_id):
    """client.write is not enough: the client has to be one this principal may touch."""
    assert _status(monkeypatch, app, UNSCOPED_WRITE, "POST", f"/people/{person_id}/edit") == 403


def test_an_assigned_principal_may_save_without_the_firm_wide_bypass(monkeypatch, app, person_id):
    principal = Principal(seed_staff_user(), "assigned@example.test", "Assigned",
                          UNSCOPED_WRITE)
    with engine.begin() as c:
        c.execute(record_assignments.insert().values(
            user_id=principal.user_id, entity_type="person", entity_id=person_id,
            assignment_type="owner"))
    monkeypatch.setattr("app.security.middleware.resolve_principal", lambda token: principal)
    scope = {
        "type": "http", "method": "POST", "path": f"/people/{person_id}/edit",
        "raw_path": f"/people/{person_id}/edit".encode(), "query_string": b"",
        "headers": [(b"accept", b"application/json")], "session": {}, "app": app,
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80), "scheme": "http",
    }

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _call_next(request):
        return JSONResponse({"ok": True})

    mw = AuthenticationMiddleware(app)
    assert asyncio.run(mw.dispatch(Request(scope, _receive), _call_next)).status_code == 200


# --- same origin (the CSRF defence this app actually has) ---------------------------------------

def test_a_save_posted_from_another_site_is_rejected(monkeypatch, app, person_id):
    status = _status(monkeypatch, app, WRITE, "POST", f"/people/{person_id}/edit",
                     headers=[(b"origin", b"https://evil.example")])
    assert status == 403


def test_a_save_with_a_foreign_referer_is_rejected(monkeypatch, app, person_id):
    status = _status(monkeypatch, app, WRITE, "POST", f"/people/{person_id}/edit",
                     headers=[(b"referer", b"https://evil.example/attack")])
    assert status == 403


def test_a_save_from_this_site_is_accepted(monkeypatch, app, person_id):
    status = _status(monkeypatch, app, WRITE, "POST", f"/people/{person_id}/edit",
                     headers=[(b"origin", BASE.encode())])
    assert status == 200


def test_the_cross_site_check_does_not_apply_to_opening_the_form(monkeypatch, app, person_id):
    """A GET is not state-changing, so a foreign Referer must not lock staff out of reading."""
    status = _status(monkeypatch, app, READ_ONLY, "GET", f"/people/{person_id}/edit",
                     headers=[(b"referer", b"https://elsewhere.example/")])
    assert status == 200
