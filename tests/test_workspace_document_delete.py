"""Removing a document from the person, household and business workspaces.

Routing over the EXISTING canonical soft delete. The recurring invariants: the row survives with
provenance intact, the stored file is never touched, the document leaves the normal listing, and
nothing in the relationship graph moves.
"""
from __future__ import annotations

import hashlib
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

_BASE = {"client.read", "documents.view"}
_SCOPE = {"record.read_all", "record.write_all"}
REMOVER = Principal(1, "staff@t", "Staff", frozenset(_BASE | _SCOPE | {"documents.delete"}))
NO_DELETE = Principal(2, "ro@t", "ReadOnly", frozenset(_BASE | _SCOPE))
UNSCOPED = Principal(3, "unscoped@t", "Unscoped", frozenset(_BASE | {"documents.delete"}))

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
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
    t = "DEL" + uuid.uuid4().hex[:8]
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


def _doc(c, tmp_path, name, *, body=b"%PDF-1.7 stored", **owner):
    u = uuid.uuid4().hex
    f = tmp_path / f"{u}.bin"
    f.write_bytes(body)
    return c.execute(insert(documents).values(
        original_name=name, stored_name=f"stored-{u}", storage_path=str(f), storage_uri=str(f),
        size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), status="active",
        archived=False, content_type="application/pdf", display_name="2025 - W-2 - Adam Steinman",
        **owner).returning(documents.c.id)).scalar_one()


def _row(doc_id):
    with engine.connect() as c:
        return dict(c.execute(select(documents).where(documents.c.id == doc_id)).mappings().one())


def _post(route, principal, owner_kw, document_id, confirm="yes"):
    from tests._portal_util import fake_request
    return route(request=fake_request("/delete", method="POST", state_principal=principal),
                 **owner_kw, document_id=document_id, confirm=confirm, principal=principal)


# --------------------------------------------------------------------- the three workspaces
def test_person_document_can_be_removed(tmp_path):
    from app.routes.document_delete import delete_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
        did = _doc(c, tmp_path, f"w2 {tag}.pdf", person_id=pid)
    resp = _post(delete_person_document, REMOVER, {"person_id": pid}, did)
    assert resp.status_code == 303 and resp.headers["location"] == f"/client/{pid}?tab=documents"
    assert _row(did)["status"] == "deleted" and _row(did)["deleted_at"] is not None


def test_household_document_can_be_removed(tmp_path):
    from app.routes.document_delete import delete_household_document
    tag = _tag()
    with engine.begin() as c:
        _, hid, _ = _owners(c, tag)
        did = _doc(c, tmp_path, f"1040 {tag}.pdf", household_id=hid)
    resp = _post(delete_household_document, REMOVER, {"household_id": hid}, did)
    assert resp.headers["location"] == f"/client/household/{hid}?tab=documents"
    assert _row(did)["status"] == "deleted"


def test_business_document_can_be_removed(tmp_path):
    from app.routes.document_delete import delete_business_document
    tag = _tag()
    with engine.begin() as c:
        _, _, bid = _owners(c, tag)
        did = _doc(c, tmp_path, f"1120S {tag}.pdf", organization_id=bid)
    resp = _post(delete_business_document, REMOVER, {"organization_id": bid}, did)
    assert resp.headers["location"] == f"/business/{bid}"
    assert _row(did)["status"] == "deleted"


# --------------------------------------------------------------------- leaves the listing
def test_document_disappears_from_the_workspace_listing(tmp_path):
    from app.routes.document_delete import delete_business_document
    from app.services.business_workspace import get_business_workspace
    tag = _tag()
    with engine.begin() as c:
        _, _, bid = _owners(c, tag)
        did = _doc(c, tmp_path, f"1120S {tag}.pdf", organization_id=bid)
    assert did in {d["id"] for d in get_business_workspace(bid)["documents"]}
    _post(delete_business_document, REMOVER, {"organization_id": bid}, did)
    ws = get_business_workspace(bid)
    assert did not in {d["id"] for d in ws["documents"]}
    assert ws["document_count"] == 0


def test_person_document_leaves_the_documents_tab(tmp_path):
    from app.routes.document_delete import delete_person_document
    from app.services.document_platform.relationships import documents_for_entity
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
        did = _doc(c, tmp_path, f"w2 {tag}.pdf", person_id=pid)
    assert did in {d["id"] for d in documents_for_entity(REMOVER, "person", pid)}
    _post(delete_person_document, REMOVER, {"person_id": pid}, did)
    assert did not in {d["id"] for d in documents_for_entity(REMOVER, "person", pid)}


# --------------------------------------------------------------------- provenance + file preserved
def test_provenance_and_ownership_survive_and_only_status_fields_change(tmp_path):
    from app.routes.document_delete import delete_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
        did = _doc(c, tmp_path, f"w2 {tag}.pdf", person_id=pid)
    before = _row(did)
    _post(delete_person_document, REMOVER, {"person_id": pid}, did)
    after = _row(did)
    for field in ("original_name", "display_name", "stored_name", "storage_path", "storage_uri",
                  "sha256", "size_bytes", "person_id", "household_id", "organization_id"):
        assert after[field] == before[field], field
    # only the documented soft-delete fields move
    assert {k for k in before if before[k] != after[k]} <= {
        "status", "deleted_at", "updated_at", "updated_by_user_id"}


def test_the_physical_file_is_never_touched(tmp_path):
    from app.routes.document_delete import delete_person_document
    tag = _tag()
    body = b"%PDF-1.7 must survive"
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
        did = _doc(c, tmp_path, f"w2 {tag}.pdf", person_id=pid, body=body)
    path = Path(_row(did)["storage_path"])
    _post(delete_person_document, REMOVER, {"person_id": pid}, did)
    assert path.is_file() and path.read_bytes() == body


# --------------------------------------------------------------------- authorization + safety
def test_capability_is_required():
    from fastapi import HTTPException

    from app.security.dependencies import require_capability
    dep = require_capability("documents.delete")
    assert dep(principal=REMOVER) is REMOVER
    with pytest.raises(HTTPException) as exc:
        dep(principal=NO_DELETE)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("route_name,kw_name,owner_index", [
    ("delete_person_document", "person_id", 0),
    ("delete_household_document", "household_id", 1),
    ("delete_business_document", "organization_id", 2),
])
def test_out_of_scope_is_non_disclosing_and_writes_nothing(tmp_path, route_name, kw_name,
                                                           owner_index):
    import app.routes.document_delete as mod
    tag = _tag()
    with engine.begin() as c:
        owners = _owners(c, tag)
        did = _doc(c, tmp_path, f"x {tag}.pdf",
                   **{("person_id", "household_id", "organization_id")[owner_index]:
                      owners[owner_index]})
    resp = _post(getattr(mod, route_name), UNSCOPED, {kw_name: owners[owner_index]}, did)
    assert resp.status_code == 404
    assert _row(did)["status"] == "active"


def test_missing_document_and_out_of_scope_are_indistinguishable(tmp_path):
    from app.routes.document_delete import delete_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
        did = _doc(c, tmp_path, f"x {tag}.pdf", person_id=pid)
    missing = _post(delete_person_document, REMOVER, {"person_id": pid}, 2_000_000_000)
    wrong_owner = _post(delete_person_document, UNSCOPED, {"person_id": pid}, did)
    assert missing.status_code == wrong_owner.status_code == 404
    assert _row(did)["status"] == "active"


def test_a_person_route_cannot_remove_another_workspaces_document(tmp_path):
    from app.routes.document_delete import delete_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, _, bid = _owners(c, tag)
        biz_doc = _doc(c, tmp_path, f"biz {tag}.pdf", organization_id=bid)
    resp = _post(delete_person_document, REMOVER, {"person_id": pid}, biz_doc)
    assert resp.status_code == 404
    assert _row(biz_doc)["status"] == "active"


def test_confirmation_is_required_server_side(tmp_path):
    from app.routes.document_delete import delete_person_document
    tag = _tag()
    with engine.begin() as c:
        pid, _, _ = _owners(c, tag)
        did = _doc(c, tmp_path, f"x {tag}.pdf", person_id=pid)
    resp = _post(delete_person_document, REMOVER, {"person_id": pid}, did, confirm="")
    assert resp.status_code == 400
    assert _row(did)["status"] == "active"


def test_routes_are_post_only_and_capability_gated():
    from app.main import app
    from app.security.dependencies import CAPABILITY_DEP_ATTR
    wanted = {"/client/{person_id}/documents/{document_id}/delete",
              "/client/household/{household_id}/documents/{document_id}/delete",
              "/business/{organization_id}/documents/{document_id}/delete"}
    found = [r for r in app.routes if getattr(r, "path", None) in wanted]
    assert {r.path for r in found} == wanted
    for r in found:
        assert r.methods == {"POST"}                       # GET can never delete
        caps = [getattr(d.call, CAPABILITY_DEP_ATTR, None) for d in r.dependant.dependencies]
        assert ("documents.delete",) in caps


# --------------------------------------------------------------------- UI gating
def test_delete_action_is_canonical_only_and_capability_gated():
    # The client Documents screen moved into one shared partial, so the person and household
    # surfaces cannot gate this differently; the business workspace still carries its own copy.
    # `is_canonical` is the partial's local alias for the same `source_kind == "canonical"` test.
    #
    # encoding= is explicit: these templates contain em-dashes, and open() with the Windows
    # default codec cannot read them at all.
    for path, gate in (
        ("app/templates/client360/_documents_screen.html",
         'is_canonical and delete_url and principal and principal.can("documents.delete")'),
        ("app/templates/business/workspace.html",
         'd.source_kind == "canonical" and principal and principal.can("documents.delete")'),
    ):
        tpl = open(path, encoding="utf-8").read()
        assert gate in tpl, path
        assert 'name="confirm" value="yes"' in tpl, path    # server-side confirmation, not JS

    # The client surfaces must not have grown a second, ungated delete of their own.
    for path in ("app/templates/client360/workspace.html",
                 "app/templates/client360/household.html"):
        tpl = open(path, encoding="utf-8").read()
        assert "/delete" not in tpl, path

    # And `is_canonical` really is that test, not a looser one.
    partial = open("app/templates/client360/_documents_screen.html", encoding="utf-8").read()
    assert '{% set is_canonical = d.source_kind == "canonical" %}' in partial


def test_vault_row_gets_no_canonical_delete_form(tmp_path):
    """A vault row's id belongs to vault_documents; a canonical delete form pointed at it would
    remove an unrelated document. Injected beside a REAL canonical row on the same rendered page."""
    from fastapi.templating import Jinja2Templates

    from app.services.client360 import get_workspace
    from app.templating import install_filters
    from tests._portal_util import fake_request, render
    tag = _tag()
    with engine.begin() as c:
        _, hid, _ = _owners(c, tag)
        canonical = _doc(c, tmp_path, f"canonical {tag}.pdf", household_id=hid)
    ws = get_workspace(REMOVER, household_id=hid)
    section = ws["sections"]["documents"]
    # The screen renders `screen.rows` (shaped + paginated), so the vault row is injected THERE —
    # beside the real canonical row it has to be distinguished from on the same page.
    rows = section["screen"]["rows"]
    assert rows, "expected the canonical document to be listed"
    rows.append({**dict(rows[0]), "id": 999_001, "name": "Vault doc", "source_kind": "vault",
                 "source": "Vault", "sources": [], "is_duplicate": False,
                 "download_url": "/api/vault/documents/999001/download"})
    tpl = Jinja2Templates(directory="app/templates")
    install_filters(tpl)
    html = render(tpl.TemplateResponse(
        request=fake_request(f"/client/household/{hid}?tab=documents", state_principal=REMOVER),
        name="client360/household.html",
        context={"principal": REMOVER, "ws": ws, "active_tab": "documents"}))
    assert "/documents/999001/delete" not in html            # vault id NEVER deleted
    assert "/api/vault/documents/999001/download" in html     # its own download preserved
    assert f"/documents/{canonical}/delete" in html           # canonical sibling still removable
    # The same id-space confusion applies to every canonical /documents/{id} route, not just
    # delete: the drawer and the canonical download must not be addressed with a vault id either.
    # Matched on the href BOUNDARY -- "/documents/999001/download" is a substring of the vault
    # row's own legitimate "/api/vault/documents/999001/download".
    assert 'href="/documents/999001/' not in html
    assert "/documents/999001/panel" not in html


# --------------------------------------------------------------------- no side effects
def test_removal_creates_no_relationship_or_ownership_mutation(tmp_path):
    from app.routes.document_delete import delete_business_document
    tag = _tag()
    with engine.begin() as c:
        _, _, bid = _owners(c, tag)
        did = _doc(c, tmp_path, f"1120S {tag}.pdf", organization_id=bid)
    with engine.connect() as c:
        rels = c.scalar(select(func.count()).select_from(relationships))
        ents = c.scalar(select(func.count()).select_from(relationship_entities))
    _post(delete_business_document, REMOVER, {"organization_id": bid}, did)
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(relationships)) == rels
        assert c.scalar(select(func.count()).select_from(relationship_entities)) == ents
    assert _row(did)["organization_id"] == bid                  # ownership unchanged


def test_upload_download_and_email_paths_are_unchanged():
    """This task added a route; it must not have altered the neighbouring document paths."""
    import inspect

    from app.routes import document_email, document_upload
    assert "soft_delete" not in inspect.getsource(document_upload)
    assert "soft_delete" not in inspect.getsource(document_email)
