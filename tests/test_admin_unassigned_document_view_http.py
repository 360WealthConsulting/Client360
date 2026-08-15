"""Integration tests for the document-view authorization decision in the admin review workflow.

Reproduces the ACTUAL failing request — GET /documents/{id}/download?inline=1 — by driving the real
AuthenticationMiddleware.dispatch end-to-end (the full capability + document-scope path that produces
the production 403), not just unit-testing _document_in_scope. The stub route stands in for the document
route: a 200 means the middleware AUTHORIZED the request; a 403 means it denied. Proves:
  * authenticated admin (client.write) -> genuinely all-NULL document -> authorized (200)
  * ordinary user (no review capability) -> same URL -> 403
  * already-owned out-of-scope document -> still 403
  * the GET does not change ownership
"""
import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import Response

import app.security.middleware as mw
from app.db import documents, engine, households
from app.security.middleware import AuthenticationMiddleware
from app.security.models import Principal

_TAG = uuid.uuid4().hex[:8]
_DOCS: list = []
_HH: list = []
_MW = AuthenticationMiddleware(app=lambda scope, receive, send: None)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _HH:
            c.execute(households.delete().where(households.c.id.in_(_HH)))
    _DOCS.clear()
    _HH.clear()


def _seed_doc(*, person_id=None, household_id=None, organization_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            original_name="view.pdf", stored_name=f"vh-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri="C:\\x.pdf", size_bytes=1, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            status="active", archived=False, content_type="application/pdf",
            tags={"source_system": "TaxDome Drive", "taxdome_folder": f"F-{_TAG}"}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _household():
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"HH {_TAG}").returning(households.c.id)).scalar_one()
    _HH.append(hid)
    return hid


def _owner(did):
    with engine.connect() as c:
        r = c.execute(select(documents.c.person_id, documents.c.household_id, documents.c.organization_id)
                      .where(documents.c.id == did)).mappings().first()
    return (r["person_id"], r["household_id"], r["organization_id"])


def _request(path):
    query = path.split("?", 1)[1] if "?" in path else ""
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": path.split("?", 1)[0], "raw_path": path.encode(), "query_string": query.encode(),
        "headers": [(b"host", b"testserver"), (b"accept", b"application/json")],
        "session": {"session_token": "x"}, "client": ("t", 1), "server": ("t", 80),
        "scheme": "http", "root_path": "",
    }
    return Request(scope)


def _authorize(monkeypatch, path, caps):
    """Run the real middleware auth path for `path` as a principal with `caps`. Returns HTTP status
    (200 = authorized by the middleware, 403 = denied). Audit writes are no-op'd — these tests exercise
    the authorization DECISION, not the audit log."""
    p = Principal(999_100, "admin@e.com", "Admin", frozenset(caps))
    monkeypatch.setattr(mw, "resolve_principal", lambda token: p)
    monkeypatch.setattr(mw, "write_audit_event", lambda **kw: None)

    async def call_next(request):   # stands in for the document route on authorized requests
        return Response("ok", status_code=200, headers={"content-disposition": "inline"})

    resp = asyncio.run(_MW.dispatch(_request(path), call_next))
    return resp.status_code


def test_http_admin_authorized_to_view_unassigned_document(monkeypatch):
    did = _seed_doc()   # genuinely all-NULL
    assert _authorize(monkeypatch, f"/documents/{did}/download?inline=1", {"client.write", "document.read"}) == 200
    assert _owner(did) == (None, None, None)   # the GET did not change ownership


def test_http_ordinary_user_denied_unassigned_document(monkeypatch):
    did = _seed_doc()
    assert _authorize(monkeypatch, f"/documents/{did}/download?inline=1", {"document.read"}) == 403


def test_http_owned_out_of_scope_document_still_denied(monkeypatch):
    did = _seed_doc(household_id=_household())   # already owned; admin has no record scope for it
    assert _authorize(monkeypatch, f"/documents/{did}/download?inline=1", {"client.write", "document.read"}) == 403


def test_http_workbook_preview_uses_same_authorization(monkeypatch):
    # The /preview path is gated identically to the document route (middleware document-scope check).
    did = _seed_doc()   # genuinely all-NULL
    assert _authorize(monkeypatch, f"/documents/{did}/preview", {"client.write", "document.read"}) == 200
    assert _authorize(monkeypatch, f"/documents/{did}/preview", {"document.read"}) == 403       # ordinary
    owned = _seed_doc(household_id=_household())
    assert _authorize(monkeypatch, f"/documents/{owned}/preview", {"client.write", "document.read"}) == 403
