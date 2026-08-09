"""Coverage for the folder-centric Linkage Review UI (PR-5).

Tests the testable data functions + the resolve handler directly (mirroring test_identity_review): the queue
lists linkage exceptions only and firm-wide; detail exposes the PR-2 evidence bundle; permissions are
enforced; every resolution action routes through the PR-4 adapter; target validation and explicit
confirmation are required; success advances queue state; defer waits; PR-4 errors surface cleanly; and no
file/storage changes occur.
"""
import asyncio
import hashlib
import uuid
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.db import (
    documents,
    engine,
    households,
    metadata,
    people,
    relationship_entities,
    source_contacts,
    users,
)
from app.importers.taxdome_drive import _name_key
from app.routes.linkage_review import (
    linkage_detail_context,
    linkage_queue,
    review_resolve,
)
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.migration.evidence_assembler import build_context
from app.services.migration.linkage_detector import detect
from app.services.resolution_knowledge import get_current_decision, get_reusable_resolution

_TAG = uuid.uuid4().hex[:8]
_SYS = "TaxDome Drive"
_frd = metadata.tables["folder_resolution_decisions"]
_exceptions = metadata.tables["exceptions"]
_psl = metadata.tables["person_source_links"]
_document_sources = metadata.tables.get("document_sources")
# Rebound to real users by the module fixture (exception_events.actor_user_id FKs to users).
_WRITER = Principal(999010, "w@e.com", "Writer", frozenset({"exception.read", "exception.write"}))
_READER = Principal(999011, "r@e.com", "Reader", frozenset({"exception.read"}))
_C = {"documents": [], "people": [], "households": [], "relationship_entities": [], "source_contacts": []}


@pytest.fixture(scope="module", autouse=True)
def _users():
    """Real user rows so resolution audit events (actor_user_id FK -> users) insert cleanly. Left in
    place afterwards (exception_events are append-only)."""
    global _WRITER, _READER
    we, re_ = f"w-{_TAG}@e.com", f"r-{_TAG}@e.com"
    with engine.begin() as c:
        wid = c.execute(users.insert().values(email=we, normalized_email=we, display_name="Writer")
                        .returning(users.c.id)).scalar_one()
        rid = c.execute(users.insert().values(email=re_, normalized_email=re_, display_name="Reader")
                        .returning(users.c.id)).scalar_one()
    _WRITER = Principal(wid, we, "Writer", frozenset({"exception.read", "exception.write"}))
    _READER = Principal(rid, re_, "Reader", frozenset({"exception.read"}))
    yield


class _Req:
    def __init__(self, form=None):
        self._b = urlencode(form or {}).encode()
        self.state = type("S", (), {"request_id": "rev"})()
        self.headers = {}

    async def body(self):
        return self._b


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C["documents"]:
            c.execute(documents.delete().where(documents.c.id.in_(_C["documents"])))
        if _C["people"] or _C["source_contacts"]:
            c.execute(_psl.delete().where(or_(
                _psl.c.person_id.in_(_C["people"] or [-1]),
                _psl.c.source_contact_id.in_(_C["source_contacts"] or [-1]))))
        for tbl, key in ((relationship_entities, "relationship_entities"), (people, "people"),
                         (households, "households"), (source_contacts, "source_contacts")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
        c.execute(_frd.delete().where(_frd.c.subject_system == _SYS.casefold()))
    for k in _C:
        _C[k].clear()


def _person(full_name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True)
                        .returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _doc(folder, name="doc.pdf", *, person_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None, original_name=name,
            stored_name=f"rv-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri="C:\\legacy\\" + name, size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            tags={"source_system": _SYS, "taxdome_folder": folder}).returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)
    return did


def _make_exception(folder, ndocs=1):
    for i in range(ndocs):
        _doc(folder, f"doc{i}.pdf")
    subject = {"source_system": _SYS, "subject_type": "folder",
               "subject_key": _name_key(folder), "display_name": folder}
    s = detect(preview=False, principal=_WRITER, subjects=[subject], context=build_context())
    return s["exception_ids"][0]


def _owners(folder):
    with engine.connect() as c:
        return [dict(m) for m in c.execute(select(
            documents.c.person_id, documents.c.household_id, documents.c.organization_id).where(
            documents.c.tags["taxdome_folder"].astext == folder)).mappings()]


def _status(eid):
    with engine.connect() as c:
        return c.execute(select(_exceptions.c.status).where(_exceptions.c.id == eid)).scalar_one()


def _resolve(eid, principal=None, **form):
    form.setdefault("confirm", "true")
    return asyncio.run(review_resolve(eid, _Req(form), principal or _WRITER))


# --- queue + detail -----------------------------------------------------------

def test_queue_lists_linkage_exceptions_only():
    folder = f"Queue Folder {_TAG}"
    eid = _make_exception(folder, ndocs=3)
    rows = linkage_queue(_WRITER, status="open")
    mine = next((r for r in rows if r["exception_id"] == eid), None)
    assert mine is not None
    assert mine["display_name"] == folder and mine["source_system"] == _SYS
    assert mine["document_count"] == 3 and "people" in mine["candidate_summary"]
    # every row in the queue is a linkage-domain exception
    with engine.connect() as c:
        domains = {d for (d,) in c.execute(select(_exceptions.c.domain).where(
            _exceptions.c.id.in_([r["exception_id"] for r in rows])))}
    assert domains == {"linkage"}


def test_detail_returns_evidence():
    folder = f"Detail Folder {_TAG}"
    _person(folder)                       # a same-name person -> a candidate
    eid = _make_exception(folder)
    ctx = linkage_detail_context(eid, _WRITER)
    ev = ctx["evidence"]
    assert ctx["subject"]["display_name"] == folder
    for key in ("documents", "identifiers", "source_contact_candidates", "person_candidates",
                "household_candidates", "business_candidates", "provenance", "relationships",
                "deterministic_outcome", "held_reason", "match_reason", "confidence", "evidence_flags"):
        assert key in ev
    assert ctx["can_write"] is True


# --- permissions --------------------------------------------------------------

def test_permission_enforcement():
    folder = f"Perm Folder {_TAG}"
    eid = _make_exception(folder)
    # read-only principal sees no active controls
    assert linkage_detail_context(eid, _READER)["can_write"] is False
    # the write gate rejects a read-only principal
    with pytest.raises(HTTPException) as ei:
        require_capability("exception.write")(_READER)
    assert ei.value.status_code == 403


# --- resolution actions route through PR-4 -----------------------------------

def test_link_person_action_routes_through_pr4():
    folder = f"UI Link Person {_TAG}"
    pid = _person(f"UI Target {_TAG}")
    eid = _make_exception(folder, ndocs=2)
    resp = _resolve(eid, action="link_person", target_entity_id=str(pid))
    assert resp.status_code == 303
    assert all(o["person_id"] == pid for o in _owners(folder))
    assert get_reusable_resolution(_SYS, "folder", _name_key(folder))["resulting_entity_id"] == pid
    assert _status(eid) == "resolved"


def test_create_business_action_routes_through_pr4():
    folder = f"UI Create Biz {_TAG}"
    eid = _make_exception(folder)
    _resolve(eid, action="create_business", name=f"UI Biz {_TAG}")
    org = _owners(folder)[0]["organization_id"]
    assert org is not None
    _C["relationship_entities"].append(org)
    assert get_current_decision(_SYS, "folder", _name_key(folder))["decision"] == "create_business"


def test_firm_material_action_routes_through_pr4():
    folder = f"UI Firm {_TAG}"
    eid = _make_exception(folder, ndocs=2)
    _resolve(eid, action="firm_material")
    assert all(o["person_id"] is None and o["household_id"] is None and o["organization_id"] is None
               for o in _owners(folder))
    assert get_reusable_resolution(_SYS, "folder", _name_key(folder))["decision"] == "firm_material"
    assert _status(eid) == "resolved"


def test_defer_behavior():
    folder = f"UI Defer {_TAG}"
    eid = _make_exception(folder)
    _resolve(eid, action="defer", notes="need info")
    assert _status(eid) == "waiting"
    assert get_reusable_resolution(_SYS, "folder", _name_key(folder)) is None
    # deferred (waiting) items remain visible in the open queue
    assert any(r["exception_id"] == eid for r in linkage_queue(_WRITER, status="open"))


# --- validation / confirmation / errors --------------------------------------

def test_candidate_target_validation_error_surfaces():
    folder = f"UI BadTarget {_TAG}"
    eid = _make_exception(folder)
    resp = _resolve(eid, action="link_person", target_entity_id="999000555")
    assert resp.status_code == 303 and "error=" in resp.headers["location"]
    assert get_current_decision(_SYS, "folder", _name_key(folder)) is None
    assert _status(eid) == "open"


def test_confirmation_required():
    folder = f"UI Confirm {_TAG}"
    pid = _person(f"Confirm Target {_TAG}")
    eid = _make_exception(folder)
    resp = asyncio.run(review_resolve(
        eid, _Req({"action": "link_person", "target_entity_id": str(pid)}), _WRITER))  # no confirm
    assert resp.status_code == 400
    assert get_current_decision(_SYS, "folder", _name_key(folder)) is None


def test_conflict_error_surfaces_cleanly():
    folder = f"UI Conflict {_TAG}"
    other = _person(f"UI Other {_TAG}")
    target = _person(f"UI Wanted {_TAG}")
    _doc(folder, "owned.pdf", person_id=other)
    _doc(folder, "free.pdf")
    subject = {"source_system": _SYS, "subject_type": "folder",
               "subject_key": _name_key(folder), "display_name": folder}
    eid = detect(preview=False, principal=_WRITER, subjects=[subject],
                 context=build_context())["exception_ids"][0]
    resp = _resolve(eid, action="link_person", target_entity_id=str(target))
    assert resp.status_code == 303 and "error=" in resp.headers["location"]
    assert _status(eid) == "open"                       # not advanced


def test_success_advances_queue_state():
    folder = f"UI Advance {_TAG}"
    pid = _person(f"Advance Target {_TAG}")
    eid = _make_exception(folder)
    _resolve(eid, action="link_person", target_entity_id=str(pid))
    assert not any(r["exception_id"] == eid for r in linkage_queue(_WRITER, status="open"))
    assert any(r["exception_id"] == eid for r in linkage_queue(_WRITER, status="all"))


def test_no_file_or_storage_changes():
    folder = f"UI NoStorage {_TAG}"
    pid = _person(f"NS Target {_TAG}")
    eid = _make_exception(folder, ndocs=2)
    with engine.connect() as c:
        before_uri = {m["id"]: m["storage_uri"] for m in c.execute(select(
            documents.c.id, documents.c.storage_uri).where(
            documents.c.tags["taxdome_folder"].astext == folder)).mappings()}
        before_ds = (c.execute(select(func.count()).select_from(_document_sources)).scalar_one()
                     if _document_sources is not None else 0)
    _resolve(eid, action="link_person", target_entity_id=str(pid))
    with engine.connect() as c:
        after_uri = {m["id"]: m["storage_uri"] for m in c.execute(select(
            documents.c.id, documents.c.storage_uri).where(
            documents.c.tags["taxdome_folder"].astext == folder)).mappings()}
        after_ds = (c.execute(select(func.count()).select_from(_document_sources)).scalar_one()
                    if _document_sources is not None else 0)
    assert after_uri == before_uri and after_ds == before_ds
