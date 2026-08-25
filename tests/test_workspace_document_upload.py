"""Direct document upload from the person, household and business workspaces.

Routing over the EXISTING canonical uploader. The recurring invariants: exactly one owner column is
set, the owner comes from the URL and never the form, storage/sha are populated by the existing
uploader, original_name is preserved verbatim, and nothing touches the relationship graph.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, insert, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    people,
    relationship_entities,
    relationships,
)
from app.security.models import Principal
from app.services.documents import DOCUMENT_ROOT, DocumentOwnerNotFound, save_workspace_document

_BASE = {"client.read", "documents.view"}
EDITOR = Principal(1, "staff@t", "Staff", frozenset(_BASE | {"documents.edit", "record.read_all",
                                                             "record.write_all"}))
NO_EDIT = Principal(2, "ro@t", "ReadOnly", frozenset(_BASE | {"record.read_all",
                                                              "record.write_all"}))
#: holds the capability but no record scope -> may not upload to anyone's record
UNSCOPED = Principal(3, "unscoped@t", "Unscoped", frozenset(_BASE | {"documents.edit"}))

_TAGS: list[str] = []
_MADE: list[int] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _MADE:
            for path in c.execute(select(documents.c.storage_path)
                                  .where(documents.c.id.in_(_MADE))).scalars():
                Path(path).unlink(missing_ok=True)
            c.execute(documents.delete().where(documents.c.id.in_(_MADE)))
    _MADE.clear()
    for tag in _TAGS:
        like = f"%{tag}%"
        with engine.begin() as c:
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(like))))
            hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
            ents = list(c.scalars(select(relationship_entities.c.id)
                                  .where(relationship_entities.c.name.like(like))))
            for col, vals in ((documents.c.person_id, ppl), (documents.c.household_id, hhs),
                              (documents.c.organization_id, ents)):
                if vals:
                    c.execute(documents.delete().where(col.in_(vals)))
            if ents:
                c.execute(relationship_entities.delete().where(relationship_entities.c.id.in_(ents)))
            if ppl:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.person_id.in_(ppl)))
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            if hhs:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.household_id.in_(hhs)))
                c.execute(households.delete().where(households.c.id.in_(hhs)))
    _TAGS.clear()


def _tag():
    t = "UPL" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _owners(c, tag):
    hid = c.execute(insert(households).values(name=f"Steinman Household {tag}")
                    .returning(households.c.id)).scalar_one()
    pid = c.execute(insert(people).values(first_name="Adam", last_name=f"Steinman{tag}",
                                          household_id=hid, active=True)
                    .returning(people.c.id)).scalar_one()
    c.execute(insert(household_relationships).values(
        household_id=hid, person_id=pid, relationship_type="member",
        is_primary=True, is_primary_household=True))
    bid = c.execute(insert(relationship_entities).values(
        entity_type="business", name=f"Steinman Holdings {tag}", active=True)
        .returning(relationship_entities.c.id)).scalar_one()
    return pid, hid, bid


class _Upload:
    """Minimal stand-in for Starlette's UploadFile."""

    def __init__(self, filename, body, content_type="application/pdf"):
        self.filename, self.file, self.content_type = filename, io.BytesIO(body), content_type
        self.closed = False

    async def close(self):
        self.closed = True


def _post(route, principal, owner_id, upload, category=""):
    from tests._portal_util import fake_request
    resp = asyncio.run(route(request=fake_request("/upload", method="POST",
                                                  state_principal=principal),
                             **{owner_id[0]: owner_id[1]}, file=upload, category=category,
                             principal=principal))
    return resp


def _latest(col, owner_id):
    with engine.connect() as c:
        row = c.execute(select(documents).where(col == owner_id)
                        .order_by(documents.c.id.desc()).limit(1)).mappings().first()
    if row:
        _MADE.append(row["id"])
    return dict(row) if row else None


# --------------------------------------------------------------------- the three workspaces
def test_person_upload_creates_one_document_owned_by_that_person():
    from app.routes.document_upload import upload_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, hid, bid = _owners(c, tag)
    body = b"%PDF-1.7 person bytes"
    resp = _post(upload_person_document, EDITOR, ("person_id", pid), _Upload("W2 2025.pdf", body))
    assert resp.status_code == 303 and resp.headers["location"] == f"/client/{pid}?tab=documents"
    row = _latest(documents.c.person_id, pid)
    assert row["person_id"] == pid
    assert row["household_id"] is None and row["organization_id"] is None   # no cross-owner
    assert row["original_name"] == "W2 2025.pdf"                            # provenance preserved
    assert row["sha256"] == hashlib.sha256(body).hexdigest()
    assert row["size_bytes"] == len(body) and row["stored_name"] and row["storage_path"]
    assert Path(row["storage_path"]).read_bytes() == body


def test_household_upload_creates_one_document_owned_by_that_household():
    from app.routes.document_upload import upload_household_document
    tag = _tag()
    with engine.begin() as c:
        pid, hid, bid = _owners(c, tag)
    body = b"%PDF-1.7 household bytes"
    resp = _post(upload_household_document, EDITOR, ("household_id", hid),
                 _Upload("1040 2024.pdf", body))
    assert resp.headers["location"] == f"/client/household/{hid}?tab=documents"
    row = _latest(documents.c.household_id, hid)
    assert row["household_id"] == hid
    assert row["person_id"] is None and row["organization_id"] is None
    assert row["original_name"] == "1040 2024.pdf"
    assert row["sha256"] == hashlib.sha256(body).hexdigest()
    assert Path(row["storage_path"]).read_bytes() == body


def test_business_upload_creates_one_document_owned_by_that_business():
    from app.routes.document_upload import upload_business_document
    tag = _tag()
    with engine.begin() as c:
        pid, hid, bid = _owners(c, tag)
    body = b"%PDF-1.7 business bytes"
    resp = _post(upload_business_document, EDITOR, ("organization_id", bid),
                 _Upload("1120S 2024.pdf", body))
    assert resp.headers["location"] == f"/business/{bid}"
    row = _latest(documents.c.organization_id, bid)
    assert row["organization_id"] == bid
    assert row["person_id"] is None and row["household_id"] is None
    assert row["original_name"] == "1120S 2024.pdf"
    assert row["sha256"] == hashlib.sha256(body).hexdigest()


def test_optional_category_is_stored_when_supplied():
    from app.routes.document_upload import upload_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
    _post(upload_person_document, EDITOR, ("person_id", pid),
          _Upload("a.pdf", b"%PDF-1.7 categorised"), category="tax")
    assert _latest(documents.c.person_id, pid)["category"] == "tax"


# --------------------------------------------------------------------- appears in the workspace
def test_uploaded_document_appears_in_its_workspace_with_download_and_email():
    from app.routes.document_upload import upload_business_document
    from app.services.business_workspace import get_business_workspace
    tag = _tag()
    with engine.begin() as c:
        _, _, bid = _owners(c, tag)
    _post(upload_business_document, EDITOR, ("organization_id", bid),
          _Upload("1120S 2024.pdf", b"%PDF-1.7 biz"))
    row = _latest(documents.c.organization_id, bid)
    ws = get_business_workspace(bid)
    listed = {d["id"]: d for d in ws["documents"]}
    assert row["id"] in listed
    assert listed[row["id"]]["download_url"] == f"/documents/{row['id']}/download"
    assert listed[row["id"]]["source_kind"] == "canonical"      # Email action is reachable
    assert ws["document_count"] >= 1


def test_uploaded_document_downloads_the_same_bytes():
    from app.routes.document_upload import upload_person_document
    from app.routes.documents import download_document
    from tests._portal_util import fake_request
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
    body = b"%PDF-1.7 downloadable"
    _post(upload_person_document, EDITOR, ("person_id", pid), _Upload("W2.pdf", body))
    row = _latest(documents.c.person_id, pid)
    resp = download_document(row["id"], fake_request(f"/documents/{row['id']}/download"))
    assert resp.filename == "W2.pdf"                            # no display_name yet -> original
    assert Path(str(resp.path)).read_bytes() == body


# --------------------------------------------------------------------- authorization
def test_capability_is_required():
    from fastapi import HTTPException

    from app.security.dependencies import require_capability
    dep = require_capability("documents.edit")
    assert dep(principal=EDITOR) is EDITOR
    with pytest.raises(HTTPException) as exc:
        dep(principal=NO_EDIT)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("route_name,owner_index,param", [
    ("upload_person_document", 0, "person_id"),
    ("upload_household_document", 1, "household_id"),
    ("upload_business_document", 2, "organization_id"),
])
def test_out_of_scope_user_cannot_upload_to_another_record(route_name, owner_index, param):
    import app.routes.document_upload as mod
    tag = _tag()
    with engine.begin() as c:
        owners = _owners(c, tag)
    before = _count()
    resp = _post(getattr(mod, route_name), UNSCOPED, (param, owners[owner_index]),
                 _Upload("sneak.pdf", b"x"))
    assert resp.status_code == 404                              # never discloses existence
    assert _count() == before                                   # nothing written


def _count():
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(documents))


def test_unknown_owner_is_refused_by_the_uploader():
    with pytest.raises(DocumentOwnerNotFound):
        save_workspace_document(owner_type="household", owner_id=2_000_000_000,
                                original_name="a.pdf", source=io.BytesIO(b"x"))


# --------------------------------------------------------------------- file safety
def test_no_filesystem_path_from_the_request_can_influence_storage():
    """The stored name is random + a short sanitised suffix, and the directory comes from the owner
    id, so a traversal filename cannot escape the document root."""
    from app.routes.document_upload import upload_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
    _post(upload_person_document, EDITOR, ("person_id", pid),
          _Upload("../../../../etc/passwd.pdf", b"%PDF-1.7 x"))
    row = _latest(documents.c.person_id, pid)
    stored = Path(row["storage_path"]).resolve()
    assert DOCUMENT_ROOT.resolve() in stored.parents             # inside the document root
    assert ".." not in row["stored_name"] and "/" not in row["stored_name"]
    assert row["original_name"] == "../../../../etc/passwd.pdf"  # provenance kept verbatim


def test_disallowed_extension_is_refused_by_the_existing_validation():
    from app.routes.document_upload import upload_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
    before = _count()
    resp = _post(upload_person_document, EDITOR, ("person_id", pid),
                 _Upload("payload.exe", b"MZ\x90\x00"))
    assert resp.status_code == 400 and _count() == before


def test_a_file_is_required():
    from app.routes.document_upload import upload_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
    before = _count()
    resp = _post(upload_person_document, EDITOR, ("person_id", pid), _Upload("", b""))
    assert resp.status_code == 400 and _count() == before


# --------------------------------------------------------------------- no side effects
def test_upload_creates_no_relationship_or_ownership_rows():
    from app.routes.document_upload import upload_business_document
    tag = _tag()
    with engine.begin() as c:
        _, _, bid = _owners(c, tag)
    with engine.connect() as c:
        rels_before = c.scalar(select(func.count()).select_from(relationships))
        ents_before = c.scalar(select(func.count()).select_from(relationship_entities))
    _post(upload_business_document, EDITOR, ("organization_id", bid),
          _Upload("1120S.pdf", b"%PDF-1.7 x"))
    _latest(documents.c.organization_id, bid)
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(relationships)) == rels_before
        assert c.scalar(select(func.count()).select_from(relationship_entities)) == ents_before


def test_exactly_one_owner_column_is_ever_set():
    from app.routes import document_upload as mod
    tag = _tag()
    with engine.begin() as c:
        pid, hid, bid = _owners(c, tag)
    for route, param, owner in ((mod.upload_person_document, "person_id", pid),
                                (mod.upload_household_document, "household_id", hid),
                                (mod.upload_business_document, "organization_id", bid)):
        _post(route, EDITOR, (param, owner), _Upload("a.pdf", b"%PDF-1.7 x"))
    with engine.connect() as c:
        rows = c.execute(select(documents.c.id, documents.c.person_id, documents.c.household_id,
                                documents.c.organization_id)
                         .where(documents.c.original_name == "a.pdf")
                         .order_by(documents.c.id.desc()).limit(3)).mappings().all()
    assert len(rows) == 3
    for r in rows:
        _MADE.append(r["id"])
        assert sum(1 for k in ("person_id", "household_id", "organization_id") if r[k]) == 1


def test_routes_are_registered_and_capability_gated():
    from app.main import app
    from app.security.dependencies import CAPABILITY_DEP_ATTR
    wanted = {"/client/{person_id}/documents/upload",
              "/client/household/{household_id}/documents/upload",
              "/business/{organization_id}/documents/upload"}
    found = [r for r in app.routes if getattr(r, "path", None) in wanted]
    assert {r.path for r in found} == wanted
    for r in found:
        caps = [getattr(d.call, CAPABILITY_DEP_ATTR, None) for d in r.dependant.dependencies]
        assert ("documents.edit",) in caps
        assert "POST" in r.methods


def test_no_sharepoint_or_onedrive_reference_in_the_upload_path():
    """Code only -- the module docstring names those systems precisely to record that it never
    touches them, so comments and docstrings are stripped before scanning."""
    import inspect
    import re

    from app.routes import document_upload
    src = inspect.getsource(document_upload)
    src = re.sub(r'"""[\s\S]*?"""', "", src)          # docstrings
    src = re.sub(r"(?m)#.*$", "", src)                 # comments
    for forbidden in ("sharepoint", "onedrive", "graph", "requests.", "urllib", "smtplib"):
        assert forbidden not in src.lower(), forbidden
