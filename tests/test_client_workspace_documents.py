"""Client Workspace — Documents tab (the canonical document operating center) coverage.

Exercises the enriched Documents section (person + household compositions), duplicate detection by
SHA-256, source-badge derivation, the in-product Resolve Ownership workflow, capability/record-scope
enforcement, version display, and empty/error states — all on the canonical model (ADR-072/073), with
no new ownership logic and no duplicate rows. Temp/test rows only.
"""
import pytest
from sqlalchemy import delete, insert, select

from app.db import documents, engine, household_relationships, households, people
from app.security.models import Principal
from app.services import households as hh
from app.services.client360 import get_workspace
from app.services.client360.sections import _source_badge, enrich_documents

_TAG = "CWDOCS"
_CAPS = frozenset({"client.read", "client.write", "record.read_all", "documents.view",
                   "timeline.read", "tax.read"})


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            c.execute(documents.delete().where(documents.c.stored_name.like(f"%{_TAG}%")))
            if pids:
                c.execute(delete(household_relationships).where(household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    yield
    _wipe()


def _household(name=f"{_TAG} White"):
    with engine.begin() as c:
        return c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()


def _person(first, last, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last} {_TAG}", active=True,
            household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id is not None:
            c.execute(insert(household_relationships).values(
                household_id=household_id, person_id=pid, relationship_type="member"))
    return pid


def _doc(name, *, person_id=None, household_id=None, sha=None, provider="Client360 Local",
         source="TaxDome Drive", review="none", folder=None):
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=name, stored_name=f"{name}-{_TAG}", storage_path=f"/x/{name}",
            storage_provider=provider, size_bytes=100, sha256=sha or ("a" * 64),
            person_id=person_id, household_id=household_id, status="active", archived=False,
            review_status=review, current_version=1,
            tags={"source_system": source, "taxdome_folder": folder or f"{_TAG} F"})
            .returning(documents.c.id)).scalar_one()


def _principal(caps=_CAPS):
    return Principal(0, "adv@e.test", "Advisor", caps)


# --- source badge derivation -------------------------------------------------

@pytest.mark.parametrize("source,provider,expected", [
    ("TaxDome Drive", "Client360 Local", "TaxDome"),
    ("Drake", "Client360 Local", "Drake"),
    ("Schwab", "Client360 Local", "Schwab"),
    ("", "Client360 Local", "Upload"),
])
def test_source_badge_derivation(source, provider, expected):
    row = {"tags": {"source_system": source}, "storage_provider": provider}
    assert _source_badge(row) == expected


# --- canonical rendering + ownership scope (person) --------------------------

def test_person_documents_scope_and_metadata():
    pid = _person("Solo", "Client")
    _doc("W-2.pdf", person_id=pid, review="pending")
    other = _person("Other", "Person")
    _doc("Theirs.pdf", person_id=other)
    ws = get_workspace(_principal(), person_id=pid)
    docs = ws["sections"]["documents"]["documents"]
    names = {d["name"] for d in docs}
    assert "W-2.pdf" in names and "Theirs.pdf" not in names       # scoped to this client
    d = next(x for x in docs if x["name"] == "W-2.pdf")
    assert d["source"] == "TaxDome" and d["owner_label"] == "Client"
    assert d["ai_status"] is None and d["version_count"] == 1     # OCR/AI honest, version shown


def test_documents_view_model_flags_are_honest():
    pid = _person("Flag", "Client")
    _doc("x.pdf", person_id=pid)
    sec = get_workspace(_principal(), person_id=pid)["sections"]["documents"]
    assert sec["ocr_enabled"] is True and sec["ai_extraction_enabled"] is False
    assert sec["multi_source_enabled"] is False
    assert set(sec["supported_sources"]) >= {"TaxDome", "Drake", "SharePoint", "Schwab",
                                             "AssetMark", "Upload", "Scanner", "Email"}


# --- household composition shares the enriched operating center --------------

def test_household_documents_include_household_and_member_docs():
    hid = _household()
    a = _person("Michael", "White", household_id=hid)
    _doc("Joint 1040.pdf", household_id=hid)
    _doc("Michael W-2.pdf", person_id=a)
    from app.services.client360.household import get_household_workspace
    ws = get_household_workspace(_principal(), hid)
    names = {d["name"] for d in ws["sections"]["documents"]["documents"]}
    assert {"Joint 1040.pdf", "Michael W-2.pdf"} <= names


# --- duplicate detection by SHA-256 -----------------------------------------

def test_duplicate_detection_by_sha256():
    pid = _person("Dup", "Client")
    _doc("a.pdf", person_id=pid, sha="d" * 64)
    _doc("b.pdf", person_id=pid, sha="d" * 64)               # same hash
    _doc("c.pdf", person_id=pid, sha="e" * 64)               # unique
    docs = {d["name"]: d for d in get_workspace(_principal(), person_id=pid)["sections"]["documents"]["documents"]}
    assert docs["a.pdf"]["is_duplicate"] and docs["a.pdf"]["duplicate_count"] == 2
    assert not docs["c.pdf"]["is_duplicate"]
    assert len(hh.duplicate_candidates("d" * 64)) == 2


def test_enrich_documents_pure_helper():
    rows = [{"id": 1, "original_name": "n.pdf", "sha256": "z" * 64, "current_version": 2,
             "tags": {"source_system": "Drake"}, "storage_provider": "Client360 Local"}]
    out = enrich_documents(rows)
    assert out[0]["source"] == "Drake" and out[0]["version_count"] == 2


# --- Resolve ownership workflow (in-product; no PowerShell) ------------------

def test_resolve_ownership_links_folder_no_duplicates():
    hid = _household()
    _doc("f1.pdf", folder=f"{_TAG} Unassigned", review="none")
    _doc("f2.pdf", folder=f"{_TAG} Unassigned")
    before = engine.connect().execute(
        select(documents.c.id).where(documents.c.stored_name.like(f"%{_TAG}%"))).rowcount
    res = hh.resolve_folder_ownership(f"{_TAG} Unassigned", household_id=hid, actor_user_id=1,
                                      request_id="t")
    assert res["documents_updated"] == 2
    with engine.connect() as c:
        linked = c.execute(select(documents.c.id).where(
            documents.c.tags["taxdome_folder"].astext == f"{_TAG} Unassigned",
            documents.c.household_id == hid)).rowcount
        total = c.execute(select(documents.c.id).where(documents.c.stored_name.like(f"%{_TAG}%"))).rowcount
    assert linked == 2 and total == before                   # linked in place, NO new rows


def test_resolve_ownership_is_idempotent():
    hid = _household()
    _doc("f1.pdf", folder=f"{_TAG} Once")
    hh.resolve_folder_ownership(f"{_TAG} Once", household_id=hid)
    second = hh.resolve_folder_ownership(f"{_TAG} Once", household_id=hid)
    assert second["documents_updated"] == 0                  # already linked; nothing to fill


def test_resolve_requires_a_target():
    with pytest.raises(ValueError):
        hh.resolve_folder_ownership(f"{_TAG} X")


def test_resolve_route_enforces_capability_and_scope():
    from fastapi import HTTPException

    from app.security.dependencies import require_capability
    # A principal without client.write cannot use the resolve route.
    gate = require_capability("client.write")
    with pytest.raises(HTTPException) as exc:
        gate(principal=Principal(0, "x@e.test", "X", frozenset({"client.read"})))
    assert exc.value.status_code == 403


def test_resolve_route_out_of_scope_target_is_404():
    from starlette.requests import Request

    from app.routes.client360 import resolve_document_ownership
    _doc("f1.pdf", folder=f"{_TAG} Scope")
    limited = Principal(0, "x@e.test", "X", frozenset({"client.write", "client.read"}))  # no record.read_all
    scope = {"type": "http", "method": "POST", "path": "/client/documents/resolve", "headers": [],
             "query_string": b"", "state": {}}
    req = Request(scope)
    req.state.request_id = "t"
    resp = resolve_document_ownership(req, folder=f"{_TAG} Scope", household_id=999_000_001,
                                      person_id=None, return_to="/", principal=limited)
    assert resp.status_code == 404


# --- empty / error states ----------------------------------------------------

def test_empty_client_has_no_documents_and_no_error():
    pid = _person("Empty", "Client")
    sec = get_workspace(_principal(), person_id=pid)["sections"]["documents"]
    assert sec["documents"] == [] and "supported_sources" in sec
