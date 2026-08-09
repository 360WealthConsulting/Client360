"""Coverage for the linkage resolution adapter (PR-4).

Proves every approved action (link/create person, household, business; firm_material; defer) applies the
right canonical + document-owner + durable-ledger writes atomically through the existing primitives and the
Exception Engine lifecycle; that positives become reusable knowledge while defer does not; and that the
fail-closed guards (conflict abort with no partial write, target validation, no silent overwrite,
idempotent repeat) and the read-only guarantees (no file/storage changes) hold.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import func, or_, select

from app.db import (
    documents,
    engine,
    households,
    metadata,
    people,
    relationship_entities,
    source_contacts,
)
from app.importers.taxdome_drive import _name_key
from app.security.models import Principal
from app.services.migration.evidence_assembler import build_context
from app.services.migration.linkage_detector import detect
from app.services.migration.linkage_resolution import (
    LinkageConflictError,
    LinkageResolutionError,
    resolve_linkage_exception,
)
from app.services.resolution_knowledge import (
    get_current_decision,
    get_decision_history,
    get_reusable_resolution,
)

_TAG = uuid.uuid4().hex[:8]
_SYS = "TaxDome Drive"
_frd = metadata.tables["folder_resolution_decisions"]
_exceptions = metadata.tables["exceptions"]
_exception_events = metadata.tables["exception_events"]
_psl = metadata.tables["person_source_links"]
_document_sources = metadata.tables.get("document_sources")
_PRINCIPAL = Principal(999002, "reviewer@e.com", "Reviewer", frozenset({"exception.read", "exception.write"}))
_C = {"documents": [], "people": [], "households": [], "relationship_entities": [], "source_contacts": []}


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


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _C["households"].append(hid)
    return hid


def _business(name):
    with engine.begin() as c:
        eid = c.execute(relationship_entities.insert().values(
            entity_type="business", name=name, active=True).returning(relationship_entities.c.id)
        ).scalar_one()
    _C["relationship_entities"].append(eid)
    return eid


def _sc(full_name):
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system=_SYS, source_file="t.csv", source_hash=uuid.uuid4().hex,
            full_name=full_name, raw_data={}).returning(source_contacts.c.id)).scalar_one()
    _C["source_contacts"].append(sid)
    return sid


def _doc(folder, name="doc.pdf", *, person_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None, original_name=name,
            stored_name=f"lr-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri="C:\\legacy\\" + name, size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            tags={"source_system": _SYS, "taxdome_folder": folder}).returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)
    return did


def _subject(folder):
    return {"source_system": _SYS, "subject_type": "folder",
            "subject_key": _name_key(folder), "display_name": folder}


def _make_exception(folder, ndocs=1):
    for i in range(ndocs):
        _doc(folder, f"doc{i}.pdf")
    s = detect(preview=False, principal=_PRINCIPAL, subjects=[_subject(folder)], context=build_context())
    return s["exception_ids"][0]


def _owners(folder):
    with engine.connect() as c:
        return [dict(m) for m in c.execute(select(
            documents.c.person_id, documents.c.household_id, documents.c.organization_id).where(
            documents.c.tags["taxdome_folder"].astext == folder)).mappings()]


def _status(eid):
    with engine.connect() as c:
        return c.execute(select(_exceptions.c.status).where(_exceptions.c.id == eid)).scalar_one()


def _key(folder):
    return _name_key(folder)


# --- positive actions ---------------------------------------------------------

def test_link_existing_person():
    folder = f"Link Person {_TAG}"
    pid = _person(f"Target Person {_TAG}")
    eid = _make_exception(folder, ndocs=2)
    r = resolve_linkage_exception(eid, "link_person", principal=_PRINCIPAL, target_entity_id=pid)
    assert r["resulting_entity_id"] == pid and r["documents_linked"] == 2
    assert all(o["person_id"] == pid for o in _owners(folder))
    assert get_reusable_resolution(_SYS, "folder", _key(folder))["decision"] == "link_person"
    assert _status(eid) == "resolved"


def test_create_person_bare():
    folder = f"Create Person {_TAG}"
    eid = _make_exception(folder)
    r = resolve_linkage_exception(eid, "create_person", principal=_PRINCIPAL, name=f"New Client {_TAG}")
    _C["people"].append(r["resulting_entity_id"])
    assert all(o["person_id"] == r["resulting_entity_id"] for o in _owners(folder))
    assert get_current_decision(_SYS, "folder", _key(folder))["decision"] == "create_person"


def test_create_person_promotes_source_contact_with_provenance():
    folder = f"Promote Person {_TAG}"
    sid = _sc(folder)
    eid = _make_exception(folder)
    r = resolve_linkage_exception(eid, "create_person", principal=_PRINCIPAL, source_contact_id=sid)
    _C["people"].append(r["resulting_entity_id"])
    with engine.connect() as c:
        link = c.execute(select(_psl.c.person_id).where(_psl.c.source_contact_id == sid)).scalar_one()
    assert link == r["resulting_entity_id"]                       # canonical provenance written
    assert all(o["person_id"] == r["resulting_entity_id"] for o in _owners(folder))


def test_link_and_create_household():
    folder1 = f"Link HH {_TAG}"
    hid = _household(f"Existing HH {_TAG}")
    eid1 = _make_exception(folder1)
    resolve_linkage_exception(eid1, "link_household", principal=_PRINCIPAL, target_entity_id=hid)
    assert all(o["household_id"] == hid for o in _owners(folder1))

    folder2 = f"Create HH {_TAG}"
    eid2 = _make_exception(folder2)
    r = resolve_linkage_exception(eid2, "create_household", principal=_PRINCIPAL, name=f"Fresh HH {_TAG}")
    _C["households"].append(r["resulting_entity_id"])
    assert all(o["household_id"] == r["resulting_entity_id"] for o in _owners(folder2))


def test_link_and_create_business():
    folder1 = f"Link Biz {_TAG}"
    eid1 = _make_exception(folder1)
    ent = _business(f"Existing Biz {_TAG}")
    resolve_linkage_exception(eid1, "link_business", principal=_PRINCIPAL, target_entity_id=ent)
    assert all(o["organization_id"] == ent for o in _owners(folder1))

    folder2 = f"Create Biz {_TAG}"
    eid2 = _make_exception(folder2)
    r = resolve_linkage_exception(eid2, "create_business", principal=_PRINCIPAL, name=f"Fresh Biz {_TAG}")
    _C["relationship_entities"].append(r["resulting_entity_id"])
    assert all(o["organization_id"] == r["resulting_entity_id"] for o in _owners(folder2))
    assert r["resulting_entity_type"] == "relationship_entity"


def test_firm_material_preserves_documents_unowned():
    folder = f"Firm Material {_TAG}"
    eid = _make_exception(folder, ndocs=2)
    r = resolve_linkage_exception(eid, "firm_material", principal=_PRINCIPAL)
    assert r["resulting_entity_id"] is None
    # documents are NOT assigned to any person/household/business — preserved for Firm handling
    assert all(o["person_id"] is None and o["household_id"] is None and o["organization_id"] is None
               for o in _owners(folder))
    reuse = get_reusable_resolution(_SYS, "folder", _key(folder))
    assert reuse and reuse["decision"] == "firm_material" and reuse["resulting_entity_type"] == "firm"
    assert _status(eid) == "resolved"


def test_defer_waits_and_is_not_reusable():
    folder = f"Defer {_TAG}"
    eid = _make_exception(folder)
    resolve_linkage_exception(eid, "defer", principal=_PRINCIPAL, notes="need more info")
    assert all(o["person_id"] is None for o in _owners(folder))
    assert get_current_decision(_SYS, "folder", _key(folder))["decision"] == "defer"
    assert get_reusable_resolution(_SYS, "folder", _key(folder)) is None    # never reusable knowledge
    assert _status(eid) == "waiting"


# --- fail-closed guards -------------------------------------------------------

def test_conflict_aborts_with_no_partial_write():
    folder = f"Conflict {_TAG}"
    other = _person(f"Other Owner {_TAG}")
    target = _person(f"Wanted Owner {_TAG}")
    linked_doc = _doc(folder, "already.pdf", person_id=other)      # one doc already owned by 'other'
    _doc(folder, "free.pdf")                                       # one still-NULL doc
    s = detect(preview=False, principal=_PRINCIPAL, subjects=[_subject(folder)], context=build_context())
    eid = s["exception_ids"][0]
    with pytest.raises(LinkageConflictError):
        resolve_linkage_exception(eid, "link_person", principal=_PRINCIPAL, target_entity_id=target)
    owners = _owners(folder)
    assert any(o["person_id"] == other for o in owners)           # untouched
    assert any(o["person_id"] is None for o in owners)            # the free doc stayed NULL (no partial)
    assert get_current_decision(_SYS, "folder", _key(folder)) is None
    assert _status(eid) == "open"                                 # lifecycle not advanced
    assert linked_doc                                             # referenced


def test_target_validation():
    folder = f"BadTarget {_TAG}"
    eid = _make_exception(folder)
    with pytest.raises(LinkageResolutionError):
        resolve_linkage_exception(eid, "link_person", principal=_PRINCIPAL, target_entity_id=999_000_777)
    # wrong entity type: a household id used for a business link
    hid = _household(f"HH For BadType {_TAG}")
    with pytest.raises(LinkageResolutionError):
        resolve_linkage_exception(eid, "link_business", principal=_PRINCIPAL, target_entity_id=hid)


def test_no_silent_overwrite_of_active_resolution():
    folder = f"Overwrite {_TAG}"
    p1, p2 = _person(f"First {_TAG}"), _person(f"Second {_TAG}")
    eid = _make_exception(folder)
    resolve_linkage_exception(eid, "link_person", principal=_PRINCIPAL, target_entity_id=p1)
    # a different resolution without supersede must be refused
    from app.services.resolution_knowledge import ResolutionConflictError
    with pytest.raises(ResolutionConflictError):
        resolve_linkage_exception(eid, "link_person", principal=_PRINCIPAL, target_entity_id=p2)
    assert get_current_decision(_SYS, "folder", _key(folder))["resulting_entity_id"] == p1


def test_idempotent_repeat_no_duplicate_knowledge():
    folder = f"Idem {_TAG}"
    pid = _person(f"Idem Person {_TAG}")
    eid = _make_exception(folder)
    resolve_linkage_exception(eid, "link_person", principal=_PRINCIPAL, target_entity_id=pid)
    r2 = resolve_linkage_exception(eid, "link_person", principal=_PRINCIPAL, target_entity_id=pid)
    assert r2["idempotent"] is True
    assert len(get_decision_history(_SYS, "folder", _key(folder))) == 1     # no duplicate ledger row


# --- lifecycle + read-only guarantees ----------------------------------------

def test_exception_lifecycle_audit_events():
    folder = f"Lifecycle {_TAG}"
    pid = _person(f"LC Person {_TAG}")
    eid = _make_exception(folder)
    resolve_linkage_exception(eid, "link_person", principal=_PRINCIPAL, target_entity_id=pid)
    with engine.connect() as c:
        events = {e for (e,) in c.execute(select(_exception_events.c.event_type).where(
            _exception_events.c.exception_id == eid))}
    assert {"opened", "started", "resolved"} <= events


def test_no_file_or_storage_changes():
    folder = f"NoStorage {_TAG}"
    pid = _person(f"NS Person {_TAG}")
    eid = _make_exception(folder, ndocs=2)
    with engine.connect() as c:
        before_uri = {m["id"]: m["storage_uri"] for m in c.execute(select(
            documents.c.id, documents.c.storage_uri).where(
            documents.c.tags["taxdome_folder"].astext == folder)).mappings()}
        before_ds = (c.execute(select(func.count()).select_from(_document_sources)).scalar_one()
                     if _document_sources is not None else 0)

    resolve_linkage_exception(eid, "link_person", principal=_PRINCIPAL, target_entity_id=pid)

    with engine.connect() as c:
        after_uri = {m["id"]: m["storage_uri"] for m in c.execute(select(
            documents.c.id, documents.c.storage_uri).where(
            documents.c.tags["taxdome_folder"].astext == folder)).mappings()}
        after_ds = (c.execute(select(func.count()).select_from(_document_sources)).scalar_one()
                    if _document_sources is not None else 0)
    assert after_uri == before_uri            # storage_uri unchanged (no file movement / repoint)
    assert after_ds == before_ds              # no document_sources changes


def test_requires_exception_write_capability():
    folder = f"NoCap {_TAG}"
    pid = _person(f"NoCap Person {_TAG}")
    eid = _make_exception(folder)
    weak = Principal(999003, "weak@e.com", "Weak", frozenset({"exception.read"}))
    with pytest.raises(LinkageResolutionError):
        resolve_linkage_exception(eid, "link_person", principal=weak, target_entity_id=pid)
